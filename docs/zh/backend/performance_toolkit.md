<!--
Copyright 2026 @lyty1997

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# DocRestore 性能观测与吞吐优化工具链

状态：Draft（2026-04-16）  
范围：方案 1（OCR batch + 流水化）+ GPU Monitor + Pipeline 全流程 Profiler 埋点

## 1. 背景与目标

2026-04-16 的 vLLM 优化参数基线对比结论（见 `docs/progress.md`）：

- 通用 vLLM 参数对两款 OCR 引擎稳态吞吐无收益
- GPU 利用率偏低：PaddleOCR 均值 20% / p95 66%；DeepSeek 52% / p95 73%
- `enforce_eager=True` 反而让 PaddleOCR 劣化 -70%（CUDA Graph 被关闭）

下一步优化方向是**提高 GPU 利用率**。本文档覆盖三块改造，按独立开关交付：

| 模块 | 解决什么 | 默认 |
|---|---|---|
| OCR batch + worker 内 GPU↔CPU 流水化 | Pipeline 串行 gap + GPU/CPU 阶段无法 overlap | 开（K=4） |
| GPU Monitor | 显存碎片化与 OOM 缺乏可观测性与兜底 | 开（轻量采样） |
| Pipeline Profiler 埋点 | 端到端各阶段耗时占比不清晰 | **关**（调试开启） |

非目标：Pipeline 级异步流水（原方案 3，与本工作正交，后续独立推进）。

## 2. 整体数据流

```
┌──────────────────────────────────────────────────────────────────┐
│ Pipeline.process()                                              │
│   with profiler.stage("pipeline.total"):                        │
│     with profiler.stage("ocr.phase"):                           │
│       async for batch in chunks(imgs, K):       ◀── 方案 1       │
│         async with gpu_lock:                                    │
│           pages = await engine.ocr_batch(batch) ──▶┐            │
│     with profiler.stage("cleaner.page"): ...       │            │
│     ... 后续阶段（dedup/pii/llm/render）全部埋点     │            │
└────────────────────────────────────────────────────┼────────────┘
                                                     ▼
                ┌──────────────────────────────────────────────────┐
                │ DeepSeek worker (独立 conda 进程)                 │
                │                                                  │
                │  cmd:"ocr_batch" {image_paths:[...]}             │
                │    ↓                                             │
                │  asyncio.gather(*[_process_one(img) for img])    │
                │    ├─ vLLM.generate()  ─┐                        │
                │    │                    │ continuous batching    │
                │    │   (GPU 合批)        │                        │
                │    ├─ grounding 解析    │                        │
                │    ├─ 图片裁剪           │  (CPU 后处理与下一张    │
                │    └─ 写盘              │   GPU 推理天然 overlap) │
                │    → profile{gpu_ms,cpu_ms,parse_ms,write_ms}    │
                │                                                  │
                │  [后台 task] gpu_monitor                         │
                │    每 1s 采 mem_get_info + allocated/reserved    │
                │    free < margin → empty_cache + WARN            │
                │                                                  │
                │  OOM catch → K/2 重试 → K=1 仍失败抛错            │
                └──────────────────────────────────────────────────┘
```

## 3. OCR batch + 流水化

### 3.1 协议扩展

Worker JSON Lines 协议新增命令 `ocr_batch`：

**请求**
```json
{
  "cmd": "ocr_batch",
  "image_paths": ["/abs/path/A.jpg", "/abs/path/B.jpg", ...],
  "output_dir": "/abs/path/output",
  "enable_column_filter": false,
  "column_filter_min_sidebar": 5
}
```

**响应**
```json
{
  "ok": true,
  "results": [
    {
      "ok": true,
      "image_path": "/abs/path/A.jpg",
      "ocr_dir": "/abs/path/output/A_OCR",
      "raw_text": "...",
      "image_size": [W, H],
      "has_eos": true,
      "regions": [...],
      "profile": {"gpu_ms": 1840, "cpu_ms": 210, "parse_ms": 35, "write_ms": 60}
    },
    {"ok": false, "image_path": "/abs/path/B.jpg", "error": "..."},
    ...
  ]
}
```

单张失败不阻塞其他（gather `return_exceptions=True`），结果按 `image_paths` 顺序返回。

### 3.2 Worker 内部并发模型

```python
async def handle_ocr_batch(req: dict) -> dict:
    imgs = [Path(p) for p in req["image_paths"]]
    output_dir = Path(req["output_dir"])

    async def _process_one(img: Path) -> dict:
        t0 = time.monotonic()
        # GPU 阶段 —— vLLM async generate，多协程并发时自动 continuous batching
        final = None
        async for out in engine.generate(prompt, sampling_params, request_id=...):
            final = out
        t_gpu = time.monotonic()
        # CPU 阶段 —— grounding 解析 + 裁剪 + 写盘（to_thread 释放事件循环）
        result = await asyncio.to_thread(postprocess, final, img, output_dir)
        t_cpu = time.monotonic()
        result["profile"] = {
            "gpu_ms": int((t_gpu - t0) * 1000),
            "cpu_ms": int((t_cpu - t_gpu) * 1000),
            ...
        }
        return result

    results = await asyncio.gather(
        *[_process_one(img) for img in imgs],
        return_exceptions=True,
    )
    # 异常转 {ok: False, error: str(exc)}
    return {"ok": True, "results": [_normalize(r) for r in results]}
```

### 3.3 Pipeline 侧调用

```python
# pipeline/pipeline.py 主循环
batch_size = self._config.ocr.ocr_batch_size
if batch_size < 2:
    # 回退：逐张处理（保留现有路径）
    for img in images:
        async with gpu_lock:
            page = await engine.ocr(img, output_dir)
        ...
else:
    for batch in _chunks(images, batch_size):
        async with gpu_lock:
            with self._profiler.stage("ocr.batch", batch_size=len(batch)):
                pages = await engine.ocr_batch(batch, output_dir)
        for page in pages:
            await cleaner.clean(page)
            ...
```

### 3.4 增量 OCR 兼容

每张图进 `_process_one` 前先判断 `{stem}_OCR/result.mmd` 是否存在，存在则返回 cached；所有图都 cached 则 worker 无需加载 vLLM。

### 3.5 PaddleOCR 侧（延后）

ppocr-server 已支持并发请求，瓶颈只在 `scripts/paddle_ocr_worker.py` 的逐张 HTTP。改造：新增 `ocr_batch` 命令，`asyncio.gather` 多个 HTTP 请求。DeepSeek 侧验证收益后再做。

## 4. GPU Monitor

### 4.1 DeepSeek worker 内嵌 Monitor

```python
# scripts/deepseek_ocr_worker.py
async def _gpu_monitor(interval_s: float, safety_margin_bytes: int,
                       stop: asyncio.Event) -> None:
    import torch
    while not stop.is_set():
        free, total = torch.cuda.mem_get_info()
        alloc = torch.cuda.memory_allocated()
        reserved = torch.cuda.memory_reserved()
        frag = (reserved - alloc) / reserved if reserved else 0.0

        # 结构化日志（stderr，父进程 _extract_stderr_message 可解析）
        sys.stderr.write(
            f"[gpu_monitor] free_mib={free/1024/1024:.0f} "
            f"alloc_mib={alloc/1024/1024:.0f} "
            f"reserved_mib={reserved/1024/1024:.0f} "
            f"frag={frag:.2f}\n"
        )
        sys.stderr.flush()

        if free < safety_margin_bytes:
            sys.stderr.write("[gpu_monitor] WARN low_free_mem, empty_cache\n")
            sys.stderr.flush()
            torch.cuda.empty_cache()

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass
```

启动位置：`initialize` 成功后 `asyncio.create_task(_gpu_monitor(...))`，保存 task 引用 + stop event；`shutdown` 时 set stop + await task。

### 4.2 OOM 兜底（在 DeepSeekOCR2Engine 层）

```python
async def ocr_batch(self, imgs, output_dir):
    cur_k = len(imgs)
    while cur_k >= 1:
        try:
            return await self._send_ocr_batch(imgs[:cur_k], output_dir) + \
                   (await self.ocr_batch(imgs[cur_k:], output_dir)
                    if cur_k < len(imgs) else [])
        except torch.OutOfMemoryError:
            cur_k = cur_k // 2
            if cur_k < 1:
                raise
            logger.warning("OOM, 降级 batch_size=%d", cur_k)
```

注：`torch.OutOfMemoryError` 是通过 worker stderr 识别（worker 内部捕获 `torch.cuda.OutOfMemoryError` 并以 `{"ok": false, "error": "OOM"}` 响应），主进程侧判 error 字符串触发降级。

### 4.3 环境变量

Worker 子进程 env 追加：
```
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

torch 2.1+ 官方降碎片开关，OCR 反复分配不同尺寸图片 tensor 的场景收益明显。

### 4.4 PaddleOCR 侧

ppocr-server 显存在另一个进程，worker 用 pynvml 按 `CUDA_VISIBLE_DEVICES` 采样。与 DeepSeek 共享 stderr 日志格式。延后做。

## 5. Pipeline Profiler 埋点

### 5.1 模块边界

新建 `backend/docrestore/pipeline/profiler.py`：

```python
class Profiler(Protocol):
    def stage(self, name: str, **attrs: Any) -> AbstractContextManager: ...
    def record_external(self, name: str, duration_s: float, **attrs: Any) -> None: ...
    def export_json(self, path: Path) -> None: ...
    def export_summary_table(self) -> str: ...

class NullProfiler(Profiler):
    """禁用时零开销实现。"""
    def stage(self, name: str, **attrs: Any) -> AbstractContextManager:
        return _NULL_CTX  # 全局单例，纳秒级

class MemoryProfiler(Profiler):
    """启用时的实现 —— 事件收集到内存 list。"""
    ...
```

Pipeline 构造时按 `config.profiling_enable` 实例化对应 Profiler；禁用路径的 `stage()` 返回预先构造的 no-op context manager（`@contextmanager` 装饰的空函数），单次调用 ~50ns。

### 5.2 StageEvent 数据结构

```python
@dataclass
class StageEvent:
    name: str                   # "ocr.batch"
    start_ts: float             # time.monotonic()
    duration_s: float
    depth: int                  # 嵌套层级（用于缩进打印）
    attrs: dict[str, Any]       # batch_size=4, image_path="..."
```

### 5.3 埋点点位（Pipeline 全流程）

| 阶段名 | 粒度 | 关键 attrs |
|---|---|---|
| `pipeline.total` | 整任务 | task_id, num_images |
| `ocr.phase` | 整个 OCR 阶段 | num_images, batch_size |
| `ocr.batch` | 每批 K 张 | batch_size, image_paths |
| `ocr.engine.gpu_infer` | 单张 GPU（worker 回传） | image_path, out_tokens |
| `ocr.engine.cpu_postproc` | 单张 CPU（worker 回传） | image_path, n_regions |
| `cleaner.page` | 每页清洗 | image_path |
| `dedup.merge` | 去重合并 | num_pages |
| `pii.regex` | regex 脱敏 | num_replacements |
| `pii.detect_entities` | LLM 实体检测 | llm_model, char_count |
| `llm.refine_segment` | 每段精修 | segment_idx, char_count, provider |
| `llm.fill_gap` | 每个 gap | gap_idx, page_filename |
| `llm.final_refine` | 最终精修 | char_count |
| `render.write` | 输出写文件 | num_files |

Worker 回传的 per-image `profile` 通过 `profiler.record_external("ocr.engine.gpu_infer", duration_s=...)` 吸收为外部事件，统一汇总。

### 5.4 输出

任务结束（`pipeline.total` 退出）时：

1. 写 `{output_dir}/profile.json`（完整事件流，机器可读）
2. 扁平化表打印到日志（便于人看）：

```
stage                         count    total_s    mean_s   share%
pipeline.total                    1     152.3     152.3    100.0%
  ocr.phase                       1     102.3     102.3     67.2%
    ocr.batch                     9      94.8      10.5       —
    ocr.engine.gpu_infer         36      72.1       2.00     47.3%
    ocr.engine.cpu_postproc      36      18.4       0.51     12.1%
  llm.refine_segment             12      22.5       1.87     14.8%
  pii.detect_entities             1       6.1       6.10      4.0%
  dedup.merge                     1       0.3       0.3       0.2%
  render.write                    1       0.8       0.8       0.5%
```

`share%` 基于 `pipeline.total` 的 100%。

### 5.5 开关

- `PipelineConfig.profiling_enable: bool = False`
- `PipelineConfig.profiling_output_path: str = ""`（空 → `{output_dir}/profile.json`）
- 环境变量 `DOCRESTORE_PROFILING=1` 覆盖 config（调试便利）

## 6. OCRConfig 新增字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ocr_batch_size` | `int` | `4` | OCR 批大小；< 2 回退逐张 |
| `gpu_monitor_enable` | `bool` | `True` | 启用 worker 内 GPU 监控 |
| `gpu_monitor_interval_s` | `float` | `1.0` | 采样间隔秒 |
| `gpu_memory_safety_margin_mib` | `int` | `1024` | free 低于此触发 empty_cache |

## 7. 验收标准

### 7.1 功能验收

- [ ] `ocr_batch_size=1` 路径与原逐张实现行为一致（已有 OCR 测试全通过）
- [ ] `ocr_batch_size=4` 下 36 张图正常处理，结果等价（diff `result.mmd`）
- [ ] 单张图失败时其他图仍成功返回（`return_exceptions` 路径）
- [ ] OOM 模拟：人为降低 `gpu_memory_utilization` 触发 OOM，观察自动降级到 K/2
- [ ] `profiling_enable=False` 的 bench 吞吐与无 profiler 基线差距 < 1%
- [ ] `profiling_enable=True` 产出 `profile.json` 且表格合理

### 7.2 性能验收（RTX 4070，36 张图 × 2 runs）

| 指标 | 当前基线 | 目标 |
|---|---:|---:|
| DeepSeek throughput (img/s) | 0.30 | ≥ 0.45 |
| DeepSeek GPU util mean (%) | 52 | ≥ 75 |
| DeepSeek GPU mem peak (MiB) | 9867 | ≤ 11500 |

不达目标需回退或重新评估 K 值。

## 8. 回退策略

每一层都有独立开关，任意组合禁用：

| 故障 | 回退 |
|---|---|
| Batch 模式异常 | 设 `ocr_batch_size=1` 回到逐张 |
| GPU Monitor 干扰 | 设 `gpu_monitor_enable=False`，worker 不起后台 task |
| Profiler bug | 设 `profiling_enable=False`，Pipeline 用 NullProfiler |
| OOM 频繁 | 降 `ocr_batch_size` 或 `gpu_memory_utilization` |

## 9. 风险

- **vLLM continuous batching 的实际合批行为**：AsyncLLMEngine 对多个并发 generate 的合批由 scheduler 决定，最大 K 受 `max_num_seqs` / `gpu_memory_utilization` 共同约束。先跑小 K（4）观察再调。
- **碎片化真实程度**：OCR 图片 tensor 尺寸相对固定（base_size=1024 + 少量 crops），碎片应可控。若 monitor 看到 `frag_ratio > 0.3` 持续出现，评估加定期 `empty_cache`。
- **Profiler 事件量**：一次任务约几百事件，JSON 文件 < 100KB，可忽略。
- **PaddleOCR 侧未做**：本轮仅 DeepSeek 落地。若用户切到 PaddleOCR 主力，单进程串行 HTTP 仍是瓶颈，下一轮再做。

## 10. 实施计划

按 TaskList 已列 9 个任务顺序推进：

1. 设计文档（本文件）
2. Profiler 基础设施 + PipelineConfig 字段
3. OCRConfig 字段扩展
4. DeepSeek worker ocr_batch + 流水化
5. DeepSeek worker GPU Monitor
6. DeepSeekOCR2Engine.ocr_batch 层
7. Pipeline 主循环改造 + 全流程埋点
8. Paddle worker 并发 HTTP（延后）
9. Bench 验证 + 更新文档

每一步都有对应提交；任务 8 可独立 PR。
