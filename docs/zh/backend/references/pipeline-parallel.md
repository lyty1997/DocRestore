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

# Pipeline 级并行设计（Multi-Task Parallel Pipelines）

> 2026-04-16 制定。与 `streaming-pipeline.md`（单任务内 OCR↔LLM overlap）正交：
> 本设计让**多个任务**同时跑 Pipeline，OCR 阶段靠 GPU 锁串行，LLM/PII/
> render 等非 GPU 阶段在任务间并发执行，以吃掉当前多任务场景下 GPU 空闲周期。

## 1. 背景与目标

### 1.1 问题

当前 `TaskManager` 已经在 `POST /tasks` 时 `asyncio.create_task(run_task)`，
多任务本该并发跑。但实际上：

- `QueueConfig.max_concurrent_pipelines = 3` 对应的 `scheduler._pipeline_semaphore`
  **代码中从未被 acquire**（`scheduler.py:32` 创建 → 全局无调用点）
- 单任务 bench 下 DeepSeek-OCR-2 batch=4 吞吐 0.56 img/s，GPU p95 81%，
  **非 GPU 阶段（PII 检测 / LLM 分段精修 / gap fill / final refine）**
  期间 GPU 空闲
- 云端 LLM 通常 >30s/段，一条 pipeline 的 LLM 阶段耗时通常 ≥ OCR 阶段

→ 多任务场景（用户一次提交几个目录）当前是"并发启动但串行等 GPU，
  LLM 阶段完全没做并发保护"，既没吃到 GPU 并发，也容易因为 LLM API 突发
  并发被限流打爆。

### 1.2 目标

| 维度 | 当前 | 目标 |
|---|---|---|
| GPU 利用 | 单任务时 p95 81%、均值 52% | 多任务时 GPU 均值 ≥70% |
| 端到端吞吐（2 个任务并发） | ≈ 2 × 单任务耗时（串行等 OCR） | ≈ 1 × 单任务耗时 + LLM 时间 |
| LLM API 稳定性 | 无限流，突发并发打爆中转站 | 全局 ≤ `max_concurrent_requests` |
| 可观测性 | 单任务 profile.json | 每任务独立 profile，支持对比分析 |

### 1.3 工程评估

**刚刚好**：
- 不引入新组件、不改 Pipeline 数据流
- 激活已存在的 `scheduler._pipeline_semaphore`（实际上换成 `llm_semaphore`，见 §3）
- 改动面：`scheduler.py`、`pipeline.py` 少量 `async with`、`config.py` 新增 2 字段
- 预计 < 200 行改动 + 测试 ~150 行

**不做的**：
- 单任务内 OCR↔LLM 流式 overlap（streaming-pipeline.md，延后到超大 PDF 场景）
- Pipeline 实例池（当前单例 + asyncio 并发已足够）
- 跨进程/分布式调度（单机单进程 3 任务是当前需求上限）

## 2. 现状快照

### 2.1 关键代码锚点

| 关注点 | 文件:行 | 当前行为 |
|---|---|---|
| 任务创建后立即启动 | `api/routes.py:339` | `asyncio.create_task(manager.run_task(task_id))` — 无等待、无限流 |
| 全局单例 Pipeline | `api/app.py:174` | `app.state.pipeline = Pipeline(config)` |
| Scheduler 创建 | `api/app.py:~180` | `PipelineScheduler(max_concurrent_pipelines=3)` |
| GPU 锁 | `scheduler.py:31` | 全局 `asyncio.Lock()`，注入 EngineManager / Pipeline |
| Semaphore | `scheduler.py:32` | 创建但**从未调用** `.acquire()` |
| Pipeline 入口 | `task_manager.py:242` | `pipeline.process_tree(gpu_lock=scheduler.gpu_lock, ...)` |
| LLM 调用散点 | `pipeline.py` 多处 | 分段精修 / gap fill / final refine / doc boundary detection — **全部无限流** |

### 2.2 当前并发语义

```
POST /tasks A     POST /tasks B     POST /tasks C
     │                │                │
     ├ create_task ───┤ create_task ───┤ create_task
     ↓                ↓                ↓
  run_task(A)     run_task(B)      run_task(C)
     │                │                │
     ├── OCR ─┐       ├── OCR ─┐       ├── OCR ─┐
     │  gpu_lock ═════════════════════════════ 共享锁，串行化
     ↓        │       ↓        │       ↓        │
  [PII]   [等 A 放锁]        [等 B 放锁]
     │        ↓        │        ↓        │        ↓
  [LLM×N]  [PII]     [LLM×N]  [PII]   [LLM×N]
     │        │        │        │        │
     └── 全部并发发 LLM 请求，3×N 段同时飞 → 打爆中转站
```

### 2.3 Benchmark 外推（2 任务场景）

单任务 DeepSeek-OCR-2 36 图 + LLM 精修 ≈ 180s（OCR 65s + PII 15s + LLM 100s）。
两任务并发按当前语义：
- 两份 OCR 串行：130s
- LLM 重叠但不受限：~100s
- 总耗时：≈ 230s（= 2 × 180 − 100 重叠）
- 理想（LLM 受限 2 并发）：≈ 250s，但 LLM API 不被打爆

## 3. 设计决策

### 3.1 已拍板决策（对话 2026-04-16 下午）

| # | 决策 | 结论 | 理由 |
|---|---|---|---|
| 1 | 范围 | **只做多任务并行**，streaming 延后 | 改动小、收益明确、streaming 留给超大文档 |
| 2 | 默认并发度 | **3** | GPU 锁天然串行化 OCR，3 条 pipeline 同时在 LLM 阶段对内存 + API 均安全 |
| 3 | Semaphore 粒度 | **只包 LLM/PII 非 GPU 阶段**（决策 B） | A（包整个 run_task）会让后续任务阻塞在 semaphore 上，OCR 阶段白占名额 |
| 4 | LLM API 限流 | **要**，独立于 pipeline semaphore，默认 3 | 分段精修一条 pipeline 可能发 10+ 次请求，pipeline semaphore 粒度太粗 |
| 5 | Profiler | **每任务独立 profile** | ContextVar 已 per-asyncio-task 隔离，只需按 task_id 命名输出文件 |

### 3.2 派生决策（本文档新增）

**决策 6：废弃 `pipeline_semaphore`，只保留 `llm_semaphore`**

pipeline_semaphore（粗粒度）和 llm_semaphore（细粒度）二选一：
- pipeline_semaphore = 3：允许 3 条 pipeline 同时进入 LLM 阶段，但每条内部
  分段精修 10 段会同时发 10 × 3 = 30 个 API 请求，对 API 无保护
- llm_semaphore = 3：全局最多 3 个 API 请求在飞，对 API 精确保护，
  自然限制了并发 pipeline 数（因为每条 pipeline 的 LLM 阶段都会被 llm_semaphore 卡住）

→ **只保留 llm_semaphore**，删除 pipeline_semaphore（当前是 dead code）。
  `QueueConfig.max_concurrent_pipelines` 字段改名为
  `LLMConfig.max_concurrent_requests`，语义更准确。

**决策 7：LLM 限流范围**

所有云端 / 本地 LLM 调用：
- `CloudLLMRefiner.refine()` — 分段精修
- `CloudLLMRefiner.fill_gap()` — 缺口补充
- `CloudLLMRefiner.final_refine()` — 整篇精修
- `CloudLLMRefiner.detect_pii_entities()` — 实体检测
- `LocalLLMRefiner.*` — 本地 LLM 相同粒度

统一在 `_BaseLLMRefiner` 基类的 `_build_kwargs` / 实际 API 调用前 acquire
semaphore，避免每个调用点分别加一遍。

**决策 8：Semaphore 注入方式**

通过构造器参数注入 Refiner，**不走 ContextVar**：
- Refiner 在 `Pipeline._create_refiner()` 创建，可直接把 `scheduler.llm_semaphore` 传入
- ContextVar 适合"深层调用栈自动拿到"，但 Refiner 创建点已经能看到 scheduler
- 构造器注入便于测试（mock 不需要设 ContextVar）

**决策 9：PII Regex 阶段不限流**

PII 脱敏分两段：
- Regex（`patterns.py`）— 纯 CPU，无网络，无需限流
- 实体检测（`detect_pii_entities`）— LLM 调用，走 llm_semaphore

→ 只有 `detect_pii_entities` 需要限流，`_replace_custom_words` / regex
  匹配保持直通。

**决策 10：profile.json 命名**

- 当前：`{output_dir}/profile.json`
- 多任务并发：`{output_dir}/profile.json`（每任务 output_dir 独立，天然隔离）

→ 路径已经天然唯一（每个 Task 的 output_dir 不同），**无需改命名**。
  `PipelineConfig.profiling_output_path` 保留"手动指定绝对路径"的能力，
  但多任务并发场景建议**留空**让它落到 output_dir。

## 4. 架构改动

### 4.1 改动清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `pipeline/config.py` | 修改 | 删 `QueueConfig.max_concurrent_pipelines` → 增 `LLMConfig.max_concurrent_requests` |
| `pipeline/scheduler.py` | 修改 | 删 `pipeline_semaphore` → 增 `llm_semaphore`（用 `LLMConfig.max_concurrent_requests` 构造） |
| `api/app.py` | 修改 | 创建 Scheduler 时传入 `config.llm.max_concurrent_requests` |
| `pipeline/pipeline.py` | 修改 | `_create_refiner()` 把 `scheduler.llm_semaphore` 传给 Refiner 构造器 |
| `llm/cloud.py` | 修改 | `_BaseLLMRefiner.__init__` 接收 `llm_semaphore: asyncio.Semaphore \| None`，每次 API 调用前 `async with` |
| `llm/local.py` | — | 继承 `_BaseLLMRefiner`，自动获得限流能力 |
| `pipeline/task_manager.py` | 无改动 | `run_task` 语义不变，Pipeline 内部做限流 |
| `tests/pipeline/` | 新增 | `test_pipeline_parallel.py`：2 任务并发 + llm_semaphore 验证 |

### 4.2 前后对比

**scheduler.py**

```diff
 class PipelineScheduler:
-    def __init__(self, max_concurrent_pipelines: int = 3) -> None:
+    def __init__(self, max_concurrent_llm_requests: int = 3) -> None:
         self._gpu_lock = asyncio.Lock()
-        self._pipeline_semaphore = asyncio.Semaphore(max_concurrent_pipelines)
+        self._llm_semaphore = asyncio.Semaphore(max_concurrent_llm_requests)

     @property
     def gpu_lock(self) -> asyncio.Lock: ...

-    @property
-    def pipeline_semaphore(self) -> asyncio.Semaphore: ...
+    @property
+    def llm_semaphore(self) -> asyncio.Semaphore: ...
```

**llm/cloud.py**（基类片段）

```diff
 class _BaseLLMRefiner:
-    def __init__(self, config: LLMConfig) -> None:
+    def __init__(
+        self,
+        config: LLMConfig,
+        llm_semaphore: asyncio.Semaphore | None = None,
+    ) -> None:
         self._config = config
+        self._llm_semaphore = llm_semaphore

     async def _call_llm(self, messages: list[dict[str, str]]) -> str:
-        response = await litellm.acompletion(**kwargs)
+        if self._llm_semaphore is None:
+            response = await litellm.acompletion(**kwargs)
+        else:
+            async with self._llm_semaphore:
+                response = await litellm.acompletion(**kwargs)
         return response.choices[0].message.content or ""
```

> 注：当前 `cloud.py` 多处直接调 `litellm.acompletion`。重构时先抽一个
> `_call_llm()` 统一入口，再在入口处加 semaphore（避免多点维护）。

**pipeline/pipeline.py**

```diff
 class Pipeline:
     def __init__(
         self,
         config: PipelineConfig,
+        scheduler: PipelineScheduler | None = None,
     ) -> None:
         ...
+        self._scheduler = scheduler

-    def _create_refiner(self, llm_config: LLMConfig) -> LLMRefiner:
+    def _create_refiner(self, llm_config: LLMConfig) -> LLMRefiner:
         if llm_config.provider == "cloud":
             from .llm.cloud import CloudLLMRefiner
-            return CloudLLMRefiner(llm_config)
+            llm_sem = self._scheduler.llm_semaphore if self._scheduler else None
+            return CloudLLMRefiner(llm_config, llm_semaphore=llm_sem)
         ...
```

### 4.3 并发时序（目标态）

```
POST /tasks A         POST /tasks B         POST /tasks C
     │                     │                     │
     ├ create_task ────────┤ create_task ────────┤ create_task
     ↓                     ↓                     ↓
  run_task(A)           run_task(B)           run_task(C)
     │                     │                     │
  [OCR A ══ gpu_lock]   [等 A 放锁]           [等 B 放锁]
     │                     ↓                     │
  [PII regex]           [OCR B ══ gpu_lock]      ↓
     │                     │                  [等 B 放锁]
  [LLM seg1 ──sem──]    [PII regex]           [OCR C ══ gpu_lock]
     │                     │                     │
  [LLM seg2 ──sem──]    [LLM seg1 ──sem──]    [PII regex]
     │                     │                     │
  [gap fill ──sem──]    [LLM seg2 ──sem──]    [LLM seg1 ──sem──]
     │                     │                     │
  [final refine ─sem─]  [gap fill ──sem──]    ...
     │                     │                     
  [render + profile]    [final refine ─sem─]     
                           │
                        [render + profile]
```

- OCR 阶段：gpu_lock 串行（A → B → C，与现在一样）
- LLM 阶段：llm_semaphore=3 放 3 个请求同时飞，第 4 个开始排队
- 每个任务 profile.json 独立落到自己的 output_dir

## 5. 配置字段

### 5.1 新增

```python
# pipeline/config.py::LLMConfig
class LLMConfig(BaseModel):
    ...
    # 全局 LLM API 并发上限（跨所有活跃 pipeline）
    # 默认 3：与 max_concurrent_pipelines 保持一致的直觉值
    # 云端中转站多数限流 5-10 RPS，3 并发 × ~30s/req ≈ 0.1 RPS，留足余量
    max_concurrent_requests: int = 3
```

### 5.2 删除 / 迁移

```python
# pipeline/config.py::QueueConfig — 整个类废弃
# 因为 max_concurrent_pipelines 是该类唯一字段，且当前无其他使用点
class QueueConfig(BaseModel):
    max_concurrent_pipelines: int = 3  # ← 删除
```

`PipelineConfig.queue: QueueConfig` 字段一并移除。
API 层 `CreateTaskRequest` 若暴露过该字段（预计未暴露）需同步清理。

### 5.3 兼容性

- `QueueConfig` 是否被前端或 API 请求引用？
  - grep `max_concurrent_pipelines` / `QueueConfig` 全仓，预期只有 `config.py`、
    `scheduler.py`、测试 fixture 引用
  - 若前端有引用 → 提交前打个 deprecation warning 兼容一个小版本
- 是否有持久化该字段？
  - `PipelineConfig` 会被 `model_dump_json()` 写 DB（Task 表 config 列）
  - 旧 Task 记录含 `queue` 字段的情况：pydantic `model_validate_json()` 对
    未知字段默认 `extra="ignore"`（需确认当前 model_config），加载旧数据不报错

## 6. 并发语义与边界

### 6.1 GPU 锁 × LLM Semaphore 交互

两把同步原语彼此**独立**：
- GPU 锁：OCR 阶段 acquire，释放后任务可继续进入 LLM 阶段
- LLM semaphore：LLM 调用前 acquire，单次调用后释放

不存在死锁：任务持有 GPU 锁时不会 acquire LLM semaphore（OCR 阶段无 LLM 调用），
反之亦然。gap fill 路径特殊（LLM 检测 gap → re-OCR 需 GPU）：
- 先 `async with llm_semaphore: detect gap`（持 LLM sem）
- 释放 LLM sem
- `async with gpu_lock: reocr_page`（持 GPU 锁）
- 释放 GPU 锁
- `async with llm_semaphore: fill_gap(reocr_text)`
→ 两把锁交替持有，无嵌套，安全。

### 6.2 任务失败与取消

- 单任务失败：`run_task` 的 `try/except` 本身已经正确处理，不影响其他任务
- 全局关闭：`asyncio.CancelledError` 冒泡到 `run_task` → 释放所有
  semaphore（`async with` 保证）
- `llm_semaphore` 在 app 关闭时不做显式 close（Python semaphore 无此 API）—
  任务 cancel 后 semaphore 自然释放

### 6.3 背压

- LLM semaphore 就是背压：第 4 个请求 `await semaphore.acquire()` 阻塞直到
  前序请求 return
- 不会无限堆积：Pipeline 逐段调用 LLM，每段 await 完成后才发下一段，
  内存占用 = 当前在飞段 × segment_chars ≤ 3 × 8000 ≈ 24KB 文本 + 响应缓冲

### 6.4 进度推送

- 每任务通过 `TaskManager.publish_progress(task_id, ...)` 推到独立 Queue
- WS 连接 `/tasks/{task_id}/progress` 按 task_id 订阅
- 多任务并发时推送不混淆（现有机制已保证）
- 新增：`TaskProgress.message` 在 `await llm_semaphore` 期间加
  `"等待 LLM 限流 (n/3)"` 提示，让用户知道阻塞在哪里（可选优化）

### 6.5 Profiler 多任务

- `ContextVar` 自动与 `asyncio.Task` 绑定 → 每个 `run_task` 自己的
  Profiler，事件不串
- `profile.json` 落 `output_dir/profile.json`，天然隔离
- 新增 Profiler stage：`llm.acquire`（从 `await llm_semaphore.acquire()` 开始
  计时到 acquired）→ 方便量化限流等待时间

## 7. 实现步骤

按改动粒度递增，每步独立可测：

**Step 1：配置与 Scheduler 重构**
1. `LLMConfig` 加 `max_concurrent_requests: int = 3`
2. 删 `QueueConfig`（及 `PipelineConfig.queue`）
3. `scheduler.py`：`_pipeline_semaphore` → `_llm_semaphore`，构造参数改名
4. `api/app.py`：`PipelineScheduler(config.llm.max_concurrent_requests)`
5. 跑现有测试，确认无 import error

**Step 2：LLMRefiner 注入 + 限流**
1. `_BaseLLMRefiner.__init__` 接受 `llm_semaphore`
2. 抽 `_call_llm()` 方法，把所有 `litellm.acompletion` 调用收敛进来
3. 在 `_call_llm()` 入口 `async with self._llm_semaphore if not None`
4. `LocalLLMRefiner` 继承已生效，不用动
5. 写单测：mock litellm + 验证 semaphore.acquire 次数

**Step 3：Pipeline 注入 Scheduler**
1. `Pipeline.__init__` 接收 `scheduler: PipelineScheduler | None`
2. `_create_refiner()` 从 `self._scheduler.llm_semaphore` 取并传给 Refiner
3. `api/app.py` 创建 Pipeline 时 `Pipeline(config, scheduler=scheduler)`
4. 旧代码路径（无 scheduler）回退到 `llm_semaphore=None`，单测和独立调用不受影响

**Step 4：多任务并发集成测试**
1. 新建 `tests/pipeline/test_pipeline_parallel.py`
2. mock OCR 引擎 + mock LLM，起 3 个 `asyncio.create_task(run_task)`
3. 断言：LLM mock 观察到的最大并发 ≤ `max_concurrent_requests`
4. 断言：gpu_lock acquire 次数 == 每任务 OCR 调用次数
5. 用 `asyncio.Event` 模拟 LLM 慢响应，验证背压
6. 验证 3 个任务的 profile.json 各自独立、事件不串

**Step 5：真实 Benchmark**
1. `scripts/bench_pipeline_parallel.py`（新）：起 2-3 个并发任务，
   记录端到端耗时 + GPU trace
2. 对比串行（1 任务 × 3 次）vs 并发（3 任务同时），验证吞吐提升
3. 观察 `profile.json` 的 `llm.acquire` 阶段耗时分布（理想：大部分 < 100ms，
   少数长尾是被限流阻塞的请求）

**Step 6：文档与进度同步**
1. 更新 `docs/zh/backend/pipeline.md`：并发模型章节加"多任务并行"
2. 更新 `CLAUDE.md` 或 memory：`max_concurrent_pipelines` 字段已移除
3. `progress.md` 2026-04-XX 条目：覆盖改动清单 + bench 数据

## 8. 测试计划

### 8.1 单元测试

| 用例 | 文件 | 断言 |
|---|---|---|
| `_BaseLLMRefiner` 无 semaphore 透传 | `tests/llm/test_cloud.py` | 构造 `llm_semaphore=None` 时 `_call_llm` 不 acquire |
| `_BaseLLMRefiner` 有 semaphore 限流 | `tests/llm/test_cloud.py` | mock acompletion 慢响应，3 个 task 同时发 → 观察到的并发峰值 ≤ 2（semaphore=2） |
| Scheduler 重构向下兼容 | `tests/pipeline/test_scheduler.py` | 构造 `PipelineScheduler(max_concurrent_llm_requests=N)` → `scheduler.llm_semaphore._value == N` |
| Pipeline 无 scheduler 回退 | `tests/pipeline/test_pipeline.py` | `Pipeline(config, scheduler=None)` 全链路跑通（fixture OCR + fixture LLM） |

### 8.2 集成测试

- `test_pipeline_parallel.py`：3 任务 × mock OCR × mock LLM
  - 测 1：并发任务数上限 —— 同时起 5 个 task，验证 LLM 并发峰值 = 3
  - 测 2：失败隔离 —— 让 task 2 抛异常，验证 task 1/3 正常完成
  - 测 3：取消隔离 —— cancel task 2，验证 task 1/3 不受影响
  - 测 4：Profiler 独立 —— 3 任务 output_dir 下各有独立 profile.json

### 8.3 Benchmark 回归

- `scripts/bench_pipeline_parallel.py`：
  - 基线：1 task 串行 × 3 次
  - 目标态：3 task 并发 × 1 次
  - 指标：总耗时、GPU 利用率均值、LLM API 请求并发峰值
- 通过标准：并发耗时 ≤ 0.6 × 串行耗时（收益 ≥ 40%）

## 9. 风险与回滚

### 9.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| SQLite 多任务并发写 `database is locked` | 中 | 中 | aiosqlite + WAL 已启用，当前并发 3 不会触发；若触发加重试装饰器 |
| 内存爆炸（3 任务的中间文本累积） | 低 | 中 | 单任务文本 < 10MB，3 份 < 30MB，远小于当前 Python 进程常驻 |
| LLM 限流不均（大任务饿死小任务） | 低 | 低 | FIFO acquire 已经公平，严格 FIFO 需 `asyncio.BoundedSemaphore`（可选升级） |
| 用户配置 `max_concurrent_requests=1` 退化为串行 | — | — | 符合预期，作为 fallback 开关 |
| gap fill 的 LLM → GPU → LLM 三段解锁顺序出错 | 低 | 高（死锁） | §6.1 明确不嵌套持有，加集成测试覆盖 |

### 9.2 回滚方案

改动集中在 4 个文件，全部走 git：
1. Revert `feat(core): Pipeline 级并行 ...` commit
2. 验证 Pipeline 单任务路径仍工作
3. 前端如果没消费 `max_concurrent_requests`，无需改动

不做"feature flag"——改动小、测试充分就直接上；真出问题 `git revert`。

### 9.3 Deprecation

- `QueueConfig` 删除会影响序列化过的旧 Task 记录
- pydantic 默认 `extra="ignore"` 可兼容旧 JSON
- 如果当前 `PipelineConfig` 的 `model_config` 是 `extra="forbid"`，
  需先在实施 commit 里放宽一个过渡版本，下一版再删

## 10. 遗留与未来

- **Streaming Pipeline**（单任务内 OCR↔LLM overlap）：
  见 `streaming-pipeline.md`。超大 PDF 或代码照片场景下单任务延迟是痛点时再做。
- **Pipeline 级并行 + Streaming 叠加**：两者正交，后续可以同时启用
  （多任务并行 + 每任务内流式），届时 llm_semaphore 依然是正确的全局限流点。
- **PaddleOCR ocr_batch**（Task #13）：当前 PaddleOCR 走基类逐张串行，
  无法吃 batch=4 红利。本设计与 Paddle batch 正交，Paddle batch 完成后
  多任务并行自动继承收益。
- **API 层请求排队**：当前 `POST /tasks` 立即 `create_task`，无排队；
  若未来并发数 > `max_concurrent_requests × 3`，需在 API 层加 task queue
  （`asyncio.Queue` + worker pool），目前无需。
- **分布式调度**：单机单进程足够；如需跨机器，考虑 Celery/Ray，超出本设计范围。

## 附录 A：关键文件改动统计（预估）

| 文件 | +LOC | -LOC | 净变化 |
|---|---:|---:|---:|
| `pipeline/config.py` | +5 | -8 | -3 |
| `pipeline/scheduler.py` | +6 | -6 | 0 |
| `api/app.py` | +1 | -1 | 0 |
| `pipeline/pipeline.py` | +8 | 0 | +8 |
| `llm/cloud.py` | +20 | -5 | +15 |
| `tests/pipeline/test_pipeline_parallel.py` | +150 | 0 | +150 |
| `scripts/bench_pipeline_parallel.py` | +120 | 0 | +120 |
| `docs/zh/backend/pipeline.md` | +30 | -10 | +20 |
| **合计** | **+340** | **-30** | **+310** |

核心逻辑改动 < 50 行，其余是测试和基准。
