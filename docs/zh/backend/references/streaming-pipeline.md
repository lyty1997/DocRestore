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

# 流式并行 Pipeline 设计（Streaming Pipeline）

> **说明（2026-04-14）**：本文档成文时 Pipeline 仍采用 `llm_override: dict`
> 风格的请求级覆盖。Pipeline 后续已完成 Config 对象化重构——API 层一次性
> 合成完整 `LLMConfig / OCRConfig / PIIConfig` 并直接传入下游，`pipeline`
> 内部不再做 dict 合并。阅读本设计的伪代码时，请把 `llm_override` 等参数
> 理解为 `llm: LLMConfig | None`（以及 `ocr` / `pii`）。设计思路不变，
> 只是传参类型由 dict 升级为完整 Config 快照。

> **方向调整（2026-04-19）**：本轮实施在原设计基础上做三处关键精简与加强：
>
> 1. **放弃 LLM 文档聚合**。图片还原场景下一个子目录即一篇文档，`process_many`
>    不再检测 `DOC_BOUNDARY` / 拆分多文档 / 终结化并行。保留
>    `parse_doc_boundaries` / `_split_by_doc_boundaries` 代码和测试给下一版
>    代码还原场景使用。删除 `DocumentState`，`process_many` 返回单一
>    `PipelineResult`（上层 `process_tree` 聚合成 list）。
> 2. **移除 LPT 排序**。多子目录 gpu_lock 实际 acquire 顺序被前置 async IO
>    （mkdir / scan_images / engine_manager.ensure）的 race 污染，LPT 索引
>    排序不等于实际串行顺序，收益无法兑现。改用 `process_tree` warmup
>    cold start：按页数降序，**最长子目录先串行跑**到 `RateController`
>    采样就绪，再 `asyncio.gather` 剩余子目录并发。
> 3. **RateController 自适应段长**。抛弃固定 `max_chars_per_segment` 常量，
>    运行时用 EMA + 线性回归实时估计 `T_ocr / overhead / k`，解析解 `L*`
>    匹配 OCR/LLM 吞吐。冷启动走动态采样序列（1500 → 3000 → 6000），
>    样本 ≥ 3 进入自适应。不同机器 / 不同 LLM provider 自动个性化。
>
> 阅读本文档时，Section 2（决策）、Section 4.1 IncrementalMerger（移除
> 页面归属查询方法）、Section 4.3 DocumentState（已删除）、Section 5
> Pipeline 重构（单文档流式 + RateController）都已按上述调整更新。

## 1. 背景与目标

### 1.1 问题

当前 `process_many()` 是严格串行的：

```
OCR 全部照片(5min) → 合并 → 分段精修全部(3min) → 拆分 → 后处理
总耗时 = 5 + 3 = 8min
```

OCR（GPU 密集）和 LLM 精修（网络 I/O 密集）资源不重叠，但被串行编排，导致 GPU 和网络交替闲置。

### 1.2 目标

将 OCR 和 LLM 精修改为**流式并行**：OCR 边产出，下游边消费。积累到 segment 大小后立即送 LLM，两者重叠执行：

```
理论加速 ≈ max(OCR时间, LLM时间) ≈ 5min（省 3min）
```

### 1.3 工程评估

**刚刚好**：
- 复用所有现有组件（`merge_two_pages`、`refine_one_segment`、`_maybe_fill_gaps`、`Renderer`）
- 标准 asyncio Queue + create_task，无需引入新依赖
- 无 boundary 场景退化为单文档，与串行版行为一致
- 不做的：LLM 段间并发（复杂度高收益低）、进度模型改造（单通道够用）、跨任务队列（AGE-16 独立话题）

## 2. 设计决策

| 决策 | 结论 | 原因 |
|------|------|------|
| LLM 段间并发 | **串行** | 段间顺序与页面顺序一致；简单稳健，收益 / 复杂度不划算 |
| 文档聚合 | **放弃**（代码保留） | 图片还原场景：一目录 = 一篇文档；避免 LLM boundary 假正例引发跨文档污染。代码还原版需要时再回退启用 |
| 多子目录调度 | **warmup cold start + gather** | `asyncio.gather` 下 gpu_lock 实际 acquire 顺序被前置 async IO race 污染，LPT 排序不稳；最长子目录串行独跑作 warmup，`RateController` 就绪后剩余子目录并发 |
| 段长参数 | **`RateController` 运行时自适应** | 不同机器 / LLM provider 性能差异大，写死常量偏差不可控；EMA + 线性回归估计 `T_ocr / overhead / k`，解析解 `L*` 匹配吞吐 |
| 冷启动段长 | **动态序列**（1500 → 3000 → 6000） | 每段都真 refine 不浪费；样本 ≥ 3 切自适应 |
| PII 策略 | **Regex 先行 + 延迟实体检测** | 前 5 页积累后获取 lexicon，后续复用 |
| 进度模型 | **单通道不变** | OCR/refine 交替报告，前端无需改动 |

## 3. 架构总览

### 3.1 组件关系

```
┌──────────────┐    Queue[PageOCR|None]    ┌──────────────────────┐       ┌─────────────┐
│ OCR Producer │ ────────────────────────▶ │   Stream Processor    │ ◀────▶│ RateController│
│  (gpu_lock)  │                           │                       │       │ EMA + 回归   │
└──────────────┘                           │ ┌───────────────────┐ │       │ 输出 L*     │
    逐张 OCR+清洗                           │ │IncrementalMerger  │ │       └─────────────┘
    不等 LLM                                │ │  (增量合并)        │ │         ▲    ▲
    埋点 record_ocr                         │ └────────┬──────────┘ │         │    │
                                           │          ↓            │ record_ocr  record_llm
                                           │  有新文本时              │         │    │
                                           │          ↓            │         │    │
                                           │ ┌───────────────────┐ │         │    │
                                           │ │StreamSegExtractor │ │ ◀─ L* ──┘    │
                                           │ │ (按 L* 切段)       │ │              │
                                           │ └────────┬──────────┘ │              │
                                           │          ↓            │              │
                                           │ ┌───────────────────┐ │              │
                                           │ │  LLM Refine (段间) │─────埋点──────┘
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │  收集 RefinedResult    │
                                           │  （全部段完成后）       │
                                           └──────────────────────┘
                                                      ↓
                                      reassemble → gap fill →
                                      final refine → render
                                                      ↓
                                              PipelineResult (单篇)
```

### 3.2 并行时序

```
时间 ──────────────────────────────────────────────────▶
OCR:  [p1][p2][p3][p4][p5] [p6][p7][p8][p9][p10] [p11..pN]
             ↓                     ↓                    ↓
LLM:       [seg1]               [seg2]               [seg3] [seg4..]
段长 L:    (冷1500)           (冷3000)            (L*=?)
                                                              ↓（OCR/LLM 全部结束）
                                                  reassemble → gap fill →
                                                  final refine → render
                                                              ↓
                                                        PipelineResult
```

- OCR 逐张产出 PageOCR 放入 asyncio.Queue，每张完成后 `controller.record_ocr(duration)`
- Stream Processor 消费页面 → 增量合并 → 向 controller 拿 `L*` → extractor 按 L* 切段
- 每段 LLM 精修完成后 `controller.record_llm(chars, duration)` 更新回归
- 所有段完成 + OCR 哨兵到达 → reassemble → gap fill → final refine → render
- **无 DOC_BOUNDARY 检测、无终结化并行**：单目录单文档，全部段收齐再终结化

### 3.3 GPU 锁竞争

Gap fill 的 `reocr_page` 与 OCR Producer 竞争 `gpu_lock`。但由于全部段收齐后才进入 gap fill（此时 OCR Producer 已结束、哨兵已发），gap fill 期间 gpu_lock 无竞争。多子目录并发场景下，子目录间的 OCR Producer / Gap fill 由 `gpu_lock` 互斥串行，安全无死锁。

## 4. 组件详细设计

### 4.1 IncrementalMerger

**文件**：`backend/docrestore/processing/dedup.py`（新增类，同文件）

**职责**：逐页增量合并，维护带 page marker 的 markdown，提供页面归属查询。

```python
class IncrementalMerger:
    """增量合并器：逐页合并，复用 PageDeduplicator.merge_two_pages()。"""

    def __init__(self, config: DedupConfig) -> None:
        """初始化。"""
        self._dedup = PageDeduplicator(config)
        self._raw_text: str = ""                      # 不含 page marker 的纯文本
        self._page_infos: list[tuple[str, int]] = []   # [(filename, char_offset_in_raw)]
        self._page_images: dict[str, list[Region]] = {} # filename → regions
        self._md_cache: str | None = None              # get_markdown() 缓存

    def add_page(self, page: PageOCR) -> None:
        """合并新页到累积文本。

        实现：
        1. 重写图片引用：![](images/N.jpg) → ![]({stem}_OCR/images/N.jpg)
           （复用 PageDeduplicator._rewrite_image_refs 逻辑）
        2. 如果是第一页：
           - _raw_text = page_text
           - _page_infos = [(filename, 0)]
        3. 否则：
           - result = _dedup.merge_two_pages(_raw_text, page_text)
           - offset = PageDeduplicator._find_page_start(_raw_text, result)
           - _raw_text = result.text
           - _page_infos.append((filename, offset))
        4. _page_images[filename] = page.regions
        5. _md_cache = None（清除缓存）
        """

    def get_markdown(self) -> str:
        """返回当前带 page marker 的完整 markdown。

        实现（与 merge_all_pages 最后阶段相同）：
        1. 如果 _md_cache 存在，直接返回
        2. lines = _raw_text.splitlines(keepends=True)
        3. 从后往前遍历 _page_infos：
           - marker = '<!-- page: {filename} -->\\n'
           - 将 char_offset 转换为行号
           - lines.insert(line_idx, marker)
        4. _md_cache = ''.join(lines).rstrip('\\n')
        5. 返回 _md_cache
        """

    def get_text_after(self, char_offset: int) -> str:
        """返回 get_markdown()[char_offset:]。"""

    def get_all_images(self) -> list[Region]:
        """返回所有已合并页面的 Region 汇总（供终结化使用）。"""

    @property
    def total_length(self) -> int:
        """当前 markdown 总字符数。"""

    @property
    def page_count(self) -> int:
        """已合并的页面数。"""

    @property
    def all_page_names(self) -> list[str]:
        """所有已合并页面的文件名列表（按合并顺序）。"""
```

> 2026-04-19 调整：单文档场景不再需要"按 page_name 查询归属页/归属图片"，
> `get_page_names_up_to` / `get_page_names_after` / `get_images_for_pages`
> 三个方法从设计中移除。保留 `get_all_images()` 供终结化 reassemble 使用。

**关键约束**：
- `_raw_text` 不含 page marker，避免 marker 干扰 `SequenceMatcher` 的重叠检测
- `get_markdown()` 惰性计算 + 缓存，`add_page()` 清除缓存
- 图片引用重写必须与 `merge_all_pages` 中 `_rewrite_image_refs` 一致

**一致性保证**：对相同输入，`IncrementalMerger` 逐页 `add_page` 后 `get_markdown()` 结果必须与 `PageDeduplicator.merge_all_pages(pages).markdown` 完全一致。这是核心不变量，必须有测试覆盖。

### 4.2 StreamSegmentExtractor

**文件**：`backend/docrestore/processing/segmenter.py`（新增类，同文件）

**职责**：从增长中的文本增量提取 segment，支持 backward overlap。

```python
class StreamSegmentExtractor:
    """流式分段提取器：从增长的文本中按需提取 segment。"""

    def __init__(self, max_chars: int = 8000, overlap_lines: int = 5) -> None:
        """初始化。"""
        self._max_chars = max_chars
        self._overlap_lines = overlap_lines
        self._prev_tail_lines: list[str] = []  # 上一段尾部行（backward overlap 源）

    def try_extract(
        self, full_text: str, offset: int,
    ) -> tuple[str, int] | None:
        """尝试从 full_text[offset:] 提取一个 segment。

        条件：full_text[offset:] 长度 >= max_chars 时才提取。

        切点搜索范围：[offset + max_chars*0.8, offset + max_chars*1.2]
        切点优先级（从高到低）：
          1. heading 行 (^#{1,6}\\s+)
          2. page marker 行 (<!-- page:)
          3. 空行
          4. 任意换行符

        超出 1.2 倍仍无切点：在 offset + max_chars 处强制切断。

        返回值：
          - None：文本不够长，等待更多页面
          - (segment_text, new_offset)：
            - segment_text 包含 backward overlap（来自 _prev_tail_lines）
            - new_offset 指向本段结束位置（不含 overlap），用于下次调用的 offset

        副作用：更新 _prev_tail_lines 为本段尾部行。
        """

    def extract_remaining(
        self, full_text: str, offset: int,
    ) -> tuple[str, int]:
        """强制提取 full_text[offset:] 为最后一个 segment。

        不要求长度足够。始终返回有效结果（可能为空字符串）。
        包含 backward overlap。更新 _prev_tail_lines。
        """

    def reset(self) -> None:
        """重置状态。新文档开始时调用（清除 overlap 历史）。"""
        self._prev_tail_lines = []
```

**Backward overlap 机制**：
- 非第一段：segment_text = `'\n'.join(_prev_tail_lines) + '\n' + actual_segment`
- 第一段：无 overlap，直接返回 actual_segment
- Forward overlap 不支持（流式模式下未来文本未知），质量影响可忽略（现有 Pipeline 的 `RefineContext.overlap_before/after` 本就为空字符串）

### 4.3 DocumentState （已删除）

> 2026-04-19 调整：单文档简化后 `DocumentState` 不再需要。`_stream_process`
> 直接维护 `list[RefinedResult]` + `list[Gap]` 局部变量即可。代码还原场景
> 恢复文档聚合时再引入。

## 5. Pipeline 重构详细设计

**文件**：`backend/docrestore/pipeline/pipeline.py`

### 5.1 process_many() 简化为单文档流式

删除原串行逻辑，改为启动 OCR 生产者 + 流式处理器。**返回单一 `PipelineResult`**
（不再 list），上层 `process_tree` 聚合多子目录为 list。

```python
async def process_many(
    self,
    image_dir: Path,
    output_dir: Path,
    on_progress: Callable[[TaskProgress], None] | None = None,
    llm: LLMConfig | None = None,
    gpu_lock: asyncio.Lock | None = None,
    pii: PIIConfig | None = None,
    ocr: OCRConfig | None = None,
    controller: RateController | None = None,
) -> PipelineResult:
    """OCR Producer + Stream Processor 单文档流式。

    `controller` 非空时使用共享实例（`process_tree` 跨子目录复用），
    否则本次 process_many 内部临时创建。
    """
    async with self._task_profiler(output_dir) as (profiler, _):
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        images = await asyncio.to_thread(scan_images, image_dir)
        if not images:
            raise FileNotFoundError(f"未找到图片文件: {image_dir}")

        if controller is None:
            controller = RateController(self._config.llm)

        page_queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()
        pages_ref: list[PageOCR] = []  # gap fill 终结化用

        ocr_task = asyncio.create_task(
            self._ocr_producer(
                images, output_dir, gpu_lock, page_queue,
                pages_ref, controller, _report, ocr,
            ),
            name=f"ocr-producer-{image_dir.name}",
        )
        try:
            return await self._stream_process(
                page_queue, pages_ref, image_dir, output_dir,
                llm, gpu_lock, pii, controller, _report,
            )
        finally:
            await ocr_task
```

### 5.2 _ocr_producer()

逐张 OCR → 清洗 → 可选 regex-only PII → 放入队列。埋点记录 OCR 单张耗时。
异常路径也要发哨兵，避免消费者永久阻塞。

```python
async def _ocr_producer(
    self,
    images: list[Path],
    output_dir: Path,
    gpu_lock: asyncio.Lock | None,
    queue: asyncio.Queue[PageOCR | None],
    pages_ref: list[PageOCR],
    controller: RateController,
    report_fn: ReportFn,
    ocr: OCRConfig | None = None,
) -> None:
    """OCR 生产者：逐张 OCR + 清洗 → 埋点 + 入队 + 追加到 pages_ref。"""
    engine = await self._resolve_engine(ocr, report_fn)
    cleaner = OCRCleaner()
    pii_cfg = self._config.pii
    redactor = PIIRedactor(pii_cfg) if pii_cfg.enable else None

    try:
        for i, img in enumerate(images):
            t0 = time.perf_counter()
            if gpu_lock is not None:
                async with gpu_lock:
                    page = await engine.ocr(img, output_dir)
            else:
                page = await engine.ocr(img, output_dir)
            await cleaner.clean(page)

            if redactor is not None:
                page.cleaned_text, _ = redactor.redact_regex_only(
                    page.cleaned_text,
                )

            await self._save_debug(
                output_dir,
                f"{page.image_path.stem}_cleaned.md",
                page.cleaned_text,
            )

            controller.record_ocr(time.perf_counter() - t0)
            pages_ref.append(page)
            await queue.put(page)
            controller.set_queue_depth(queue.qsize())
            report_fn(
                "ocr", i + 1, len(images),
                f"OCR {i + 1}/{len(images)}",
            )
    finally:
        await queue.put(None)  # 任何异常路径都要发哨兵
```

**新增方法**：`PIIRedactor.redact_regex_only(text) -> tuple[str, list[RedactionRecord]]`
只做结构化 regex（手机/邮箱/身份证/银行卡 + 自定义敏感词），不依赖 LLM lexicon。

### 5.3 _stream_process()（单文档简化版）

```python
async def _stream_process(
    self,
    page_queue: asyncio.Queue[PageOCR | None],
    pages_ref: list[PageOCR],
    image_dir: Path,
    output_dir: Path,
    llm: LLMConfig | None,
    gpu_lock: asyncio.Lock | None,
    pii: PIIConfig | None,
    controller: RateController,
    report_fn: ReportFn,
) -> PipelineResult:
    """消费 OCR 队列：增量合并 → 按 L* 切段 → LLM 精修 → 收齐终结化。"""
    merger = IncrementalMerger(self._config.dedup)
    extractor = StreamSegmentExtractor(
        overlap_lines=self._config.llm.segment_overlap_lines,
    )
    refiner = self._get_refiner(llm)

    segmented_offset = 0
    segment_index = 0
    refined_segments: list[RefinedResult] = []
    all_gaps: list[Gap] = []
    entity_lexicon: EntityLexicon | None = None
    pii_entity_done = False
    pii_cfg = pii or self._config.pii

    while True:
        page = await page_queue.get()
        if page is None:
            break
        merger.add_page(page)

        if (pii_cfg.enable
                and not pii_entity_done
                and merger.page_count >= _PII_DETECT_THRESHOLD):
            entity_lexicon = await self._delayed_pii_detect(merger, llm)
            pii_entity_done = True

        segmented_offset, segment_index = await self._try_extract_and_refine(
            merger, extractor, refiner, controller,
            segmented_offset, segment_index,
            refined_segments, all_gaps, report_fn,
        )

    # 最后段：OCR 全部结束后的剩余文本
    md = merger.get_markdown()
    if segmented_offset < len(md):
        remaining, _ = extractor.extract_remaining(md, segmented_offset)
        if remaining.strip():
            t0 = time.perf_counter()
            result = await self._refine_one_segment(
                refiner, remaining, segment_index, 0,
            )
            controller.record_llm(
                len(remaining), time.perf_counter() - t0,
            )
            refined_segments.append(result)
            all_gaps.extend(result.gaps)
            segment_index += 1
            report_fn(
                "refine", segment_index, 0, f"精修段 {segment_index}",
            )

    return await self._finalize_single_doc(
        merger, pages_ref, refined_segments, all_gaps,
        output_dir, llm, gpu_lock, report_fn, entity_lexicon,
    )
```

### 5.4 _try_extract_and_refine()（按动态 L* 切段精修）

```python
async def _try_extract_and_refine(
    self,
    merger: IncrementalMerger,
    extractor: StreamSegmentExtractor,
    refiner: LLMRefiner | None,
    controller: RateController,
    segmented_offset: int,
    segment_index: int,
    refined_segments: list[RefinedResult],
    all_gaps: list[Gap],
    report_fn: ReportFn,
) -> tuple[int, int]:
    """在 merger 有新文本后，按 controller.target L* 尝试切段精修。

    返回更新后的 (segmented_offset, segment_index)。无 boundary 检测、
    无终结化派发；全部段收齐后由 _stream_process 调 _finalize_single_doc。
    """
    md = merger.get_markdown()
    while True:
        target = controller.target_segment_chars()
        seg = extractor.try_extract(md, segmented_offset, target)
        if seg is None:
            break
        seg_text, new_offset = seg

        t0 = time.perf_counter()
        result = await self._refine_one_segment(
            refiner, seg_text, segment_index, 0,
        )
        controller.record_llm(
            len(seg_text), time.perf_counter() - t0,
        )
        refined_segments.append(result)
        all_gaps.extend(result.gaps)
        segmented_offset = new_offset
        segment_index += 1
        report_fn(
            "refine", segment_index, 0, f"精修段 {segment_index}",
        )

    return segmented_offset, segment_index
```

### 5.5 _finalize_single_doc()（全部段收齐后终结化）

```python
async def _finalize_single_doc(
    self,
    merger: IncrementalMerger,
    pages_ref: list[PageOCR],
    refined_segments: list[RefinedResult],
    all_gaps: list[Gap],
    output_dir: Path,
    llm: LLMConfig | None,
    gpu_lock: asyncio.Lock | None,
    report_fn: ReportFn,
    entity_lexicon: EntityLexicon | None,
) -> PipelineResult:
    """单文档：reassemble → gap fill → final refine → render。"""
    doc = self._reassemble(refined_segments, MergedDocument(
        markdown="", images=merger.get_all_images(), gaps=[],
    ))
    await self._save_debug(output_dir, "reassembled.md", doc.markdown)

    doc = await self._maybe_fill_gaps(
        doc, all_gaps, pages_ref, output_dir, llm, gpu_lock,
        report_fn, entity_lexicon,
    )
    doc, truncated = await self._do_final_refine(
        doc, output_dir, llm, report_fn,
    )

    renderer = Renderer(self._config.output)
    doc_path = await renderer.render(doc, output_dir)
    final_md = await asyncio.to_thread(
        doc_path.read_text, encoding="utf-8",
    )

    return PipelineResult(
        output_path=doc_path,
        markdown=final_md,
        images=doc.images,
        gaps=all_gaps,
        doc_title=extract_first_heading(doc.markdown),
        doc_dir="",  # 单文档直接落 output_dir 根，不再建子目录
        warnings=self._collect_warnings(
            refined_segments, all_gaps, truncated,
        ),
    )
```

### 5.6 RateController（自适应段长）

**文件**：`backend/docrestore/pipeline/rate_controller.py`（新增）

```python
class RateController:
    """运行时估计 T_ocr / overhead / k，输出目标段长 L*。

    数据模型：
      R_ocr = chars_per_page / T_ocr         # OCR 吞吐（chars/s）
      R_llm(L) = L / (overhead + k · L)      # LLM 吞吐（chars/s）
      令两者相等 → L* = R_ocr · overhead / (1 - R_ocr · k)
      R_ocr·k ≥ 1（LLM 再大也跟不上）→ L* = MAX，摊薄 overhead

    接口：
      record_ocr(duration: float) → None
          每张 OCR 完成时埋点，EMA 更新 T_ocr 和 chars_per_page。
      record_llm(chars: int, duration: float) → None
          每段 LLM 完成时埋点 (chars, duration)，滑窗最小二乘回归 overhead/k。
      target_segment_chars() → int
          冷启动（样本 < 3）→ 动态序列 [1500, 3000, 6000][sample_count]
          自适应（样本 ≥ 3）→ 解析解 L*，clamp [1500, 12000]，±30% 变化率限幅
      set_queue_depth(n: int) → None
          观测指标（仅用于复盘 profile.json，不做反馈控制）
      wait_cold_start() → None
          await 直到样本 ≥ 3 或 60s 超时，用于 process_tree warmup 同步
      cold_start_done: asyncio.Event

    冷启动 fallback（60s 超时）：
      已有 1-2 个 LLM 样本 → 用 duration/chars 作为 k 估算、overhead=0，进入自适应
      0 个样本 → 保持 MIN_CHARS，cold_start_done 强制 set；其他子目录照常开跑，
               内部继续采样直到回归可用。

    线程/并发：
      内部用 asyncio 锁保护回归样本列表；EMA 可无锁（单写入协程/顺序更新）。
      多子目录并发时，record_* 接口被多协程调用，必须加锁保护样本 append/EMA。

    可观测性：
      状态快照写入 profile.json：
        ocr_avg_ms / chars_per_page_avg / llm_overhead_ms / llm_per_char_ms
        samples_llm / cold_start_elapsed_s / final_target_chars
    """
```

### 5.7 process_tree 并行分支（warmup cold start）

**文件**：`backend/docrestore/pipeline/pipeline.py`

```python
async def process_tree(
    self,
    image_dir: Path,
    output_dir: Path,
    on_progress: Callable[[TaskProgress], None] | None = None,
    llm: LLMConfig | None = None,
    gpu_lock: asyncio.Lock | None = None,
    pii: PIIConfig | None = None,
    ocr: OCRConfig | None = None,
) -> list[PipelineResult]:
    leaf_dirs = await asyncio.to_thread(find_image_dirs, image_dir)
    if not leaf_dirs:
        raise FileNotFoundError(f"未找到图片文件: {image_dir}")

    # 单目录：直接委托，不做 warmup
    if len(leaf_dirs) == 1 and leaf_dirs[0] == image_dir:
        result = await self.process_many(
            image_dir, output_dir, on_progress,
            llm, gpu_lock, pii, ocr,
        )
        return [result]

    # 多子目录：warmup 最长子目录 → 并发剩余
    controller = RateController(self._config.llm)
    leaves_sorted = sorted(
        leaf_dirs, key=lambda p: (-_count_images(p), str(p)),
    )
    warmup_leaf, *rest = leaves_sorted

    warmup_task = asyncio.create_task(
        self._process_leaf(
            0, warmup_leaf, image_dir, output_dir,
            on_progress, llm, gpu_lock, pii, ocr,
            total=len(leaves_sorted), controller=controller,
        ),
        name=f"warmup-leaf-{warmup_leaf.name}",
    )

    # 等 controller 冷启动就绪（样本 ≥ 3 或 60s 超时）
    await controller.wait_cold_start()

    # 并发启动剩余子目录，读 controller 当前 L*
    rest_tasks = [
        asyncio.create_task(
            self._process_leaf(
                i + 1, leaf, image_dir, output_dir,
                on_progress, llm, gpu_lock, pii, ocr,
                total=len(leaves_sorted), controller=controller,
            ),
            name=f"leaf-{leaf.name}",
        )
        for i, leaf in enumerate(rest)
    ]

    results = list(await asyncio.gather(warmup_task, *rest_tasks))
    return results
```

**关键点**：
- 移除 `_sort_leaves_lpt`：新排序仅用于"选最长目录做 warmup"，不再假装 LPT 调度。
- warmup 期间其他子目录**完全不启动**（不抢 gpu_lock、无采样干扰，回归样本干净）。
- 其他子目录启动后读到的 `target_segment_chars()` 是基于 warmup 样本的解析解 L*。
- 异常语义：任一 leaf 失败 → `asyncio.gather` fail-fast → 整个 task FAILED（与原语义一致）。
- 共享 `controller` 让所有子目录的 `record_ocr/record_llm` 埋点统一入栈，稳态估计越跑越准。

## 6. PII 延迟实体检测

```python
_PII_DETECT_THRESHOLD = 5  # 积累 5 页后做实体检测

async def _delayed_pii_detect(self, merger, llm_override) -> EntityLexicon | None:
    """在前 N 页积累后做一次 LLM 实体检测获取 lexicon。

    成功：返回 EntityLexicon，后续 gap fill 的 re-OCR 文本可复用。
    失败：返回 None，仅靠 regex PII 保护。
    不会 block cloud（与串行模式不同，流式模式下 LLM 精修已在进行）。
    """
```

**新增方法**：`PIIRedactor.redact_regex_only(text: str) -> tuple[str, list[RedactionRecord]]`
- 只执行结构化 regex 替换（手机/邮箱/身份证/银行卡）
- 不需要 EntityLexicon
- 在 `_ocr_producer` 中逐页调用

## 7. PipelineResult 临时排序字段（已删除）

> 2026-04-19 调整：单文档简化后不再需要 `_doc_index`。上层 `process_tree`
> 直接按子目录顺序（按页数降序）保留 `gather` 返回的 list，无需再排序。

## 8. 进度报告

保持单通道 `TaskProgress`，stage 交替出现：

| stage | current | total | 时机 |
|-------|---------|-------|------|
| `ocr` | i | N（已知） | 每张 OCR 完成 |
| `refine` | seg_idx | 0（未知） | 每段精修完成 |
| `gap_fill` | gi | len(gaps) | gap fill 中（复用现有） |
| `final_refine` | 0 | 1 | 整篇精修（复用现有） |
| `render` | 1 | 1 | 渲染完成（复用现有） |

- 去掉 `finalize` stage（不再有"终结化并行"的语义）。
- `process_tree` 的 `_wrap_progress` 继续在 message 前缀加 `[i/N {subdir}]`，
  前端按 subtask 分轨展示（与现有逻辑一致）。

## 9. 被删除/替代的代码

| 旧方法 | 处理 |
|--------|------|
| `_ocr_and_clean()` | 由 `_ocr_producer()` 替代 |
| `_refine_segments()` | 由 `_stream_process` 主循环 + `_try_extract_and_refine` 替代 |
| `_sort_leaves_lpt()` | **删除**。`process_tree` 改用"最长子目录做 warmup" + 普通排序 |
| `_redact_pii()`（全局批量版） | 拆为 (a) `_ocr_producer` 内逐页 `redact_regex_only`；(b) `_delayed_pii_detect` 在 merger.page_count ≥ 5 时异步获取 lexicon |
| `_detect_doc_boundaries()` / `_insert_doc_boundaries()` / `_split_by_doc_boundaries()` / `_handle_refined_result()` / `_split_refined_at_boundary()` | **保留代码**（单元测试仍依赖），但 `process_many` 不再调用。下一版代码还原场景重新启用 |
| `_reassemble()` | 继续复用（由 `_finalize_single_doc` 调） |
| `_finalize_document()` / `_move_to_root()` | 不需要（单文档直接落 `output_dir` 根） |

## 10. 需要修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/docrestore/pipeline/rate_controller.py` | **新增** | `RateController` 类（EMA + 回归 + L\* + 冷启动序列 + `wait_cold_start`）|
| `backend/docrestore/processing/dedup.py` | 新增类 | `IncrementalMerger`（含 `add_page` / `get_markdown` / `get_all_images` / `page_count` / `all_page_names`）|
| `backend/docrestore/processing/segmenter.py` | 新增类 | `StreamSegmentExtractor`（`try_extract(text, offset, max_chars)` / `extract_remaining` / `reset`）|
| `backend/docrestore/privacy/redactor.py` | 新增方法 | `PIIRedactor.redact_regex_only()` |
| `backend/docrestore/pipeline/pipeline.py` | **重构** | `process_many` → 单文档流式；`process_tree` 并行分支 → warmup cold start；移除 `_sort_leaves_lpt` |
| `backend/docrestore/pipeline/config.py` | 更新 | `LLMConfig` 移除 `max_chars_per_segment` 硬默认（或改为冷启动序列终值兜底） |
| `backend/docrestore/models.py` | 无改动 | `PipelineResult` 字段不新增（无 `_doc_index`） |
| `tests/pipeline/test_rate_controller.py` | 新增 | EMA / 回归 / 冷启动序列 / 超时 fallback |
| `tests/processing/test_incremental_merger.py` | 新增 | 逐页 `add_page` 后 `get_markdown()` 与 `merge_all_pages(pages).markdown` 完全一致 |
| `tests/processing/test_stream_segmenter.py` | 新增 | 切点优先级、动态 `max_chars`、backward overlap、`< max_chars` 返回 None |
| `tests/pipeline/test_process_tree_parallel.py` | 更新断言 | 移除 LPT 期望；验证"warmup 先跑、其他目录等 cold start 再开"的时序 |
| `tests/pipeline/test_pipeline.py` | 更新断言 | `process_many` 返回单 `PipelineResult`（不再 list）；相关断言跟随改动 |
| `tests/pipeline/test_boundary_gap_combo.py` / `tests/llm/test_doc_boundary.py` | 标 skip | 单元测试保留，pipeline 层集成 skip（代码还原版解 skip）|
| `docs/zh/backend/pipeline.md` / `docs/zh/progress.md` | 更新 | 流式架构 + 进度记录 |

## 11. 实施顺序

| 步骤 | 内容 | 验收 |
|------|------|------|
| 1 | 撤回上次诊断日志（routes.py / pipeline.py）| 已完成 |
| 2 | `RateController` + 单元测试 | EMA / 回归 / 冷启动序列 / 超时 fallback 均覆盖 |
| 3 | `IncrementalMerger` + 单元测试 | 对 parallel_test/ 某真实子目录，增量合并结果与 `merge_all_pages` 完全一致 |
| 4 | `StreamSegmentExtractor` + 单元测试 | 动态 max_chars 下切点与 overlap 正确 |
| 5 | `PIIRedactor.redact_regex_only` | 结构化 regex + 自定义敏感词替换都 ok |
| 6 | `process_many` 重构为流式（单文档）| 原 `_refine_segments` / DOC_BOUNDARY 路径全走旁路；mock 测试通过 |
| 7 | `process_tree` 并行分支 warmup cold start + 移除 LPT | `test_process_tree_parallel.py` 改断言，新期望"warmup 完成后剩余并发"通过 |
| 8 | 现有测试全量回归 | 标 skip 的 2 个文件外全部通过 |
| 9 | `parallel_test/基础系统` E2E 对比基线（289s） | 新实现 wall-time ≤ 基线 * 0.75；`profile.json` 中可见 controller 稳态 L\* |
| 10 | `docs/zh/progress.md` 记录实测数据 | —— |

## 12. 风险与应对

| 风险 | 应对 |
|------|------|
| 增量合并与批量合并结果不一致 | 严格复用 `merge_two_pages` + 一致性对比测试（步骤 3） |
| RateController 估计震荡 / 跑飞 | 解析解 L\* 是观测量收敛函数；±30% 变化率限幅；clamp [1500, 12000] 硬护栏 |
| LLM 吞吐 < OCR（`R_ocr·k ≥ 1`）| fallback L\* = MAX（摊薄 overhead）+ warning；总耗时 ≈ 总 chars / (1/k) 由 LLM 决定 |
| warmup 子目录本身太短攒不够 3 样本 | 动态序列缩进（剩余 chars 不足下个目标时合并到下段）；2 样本时用简易 (duration, chars) 估算进入自适应 |
| Cold start LLM 全挂 | 60s 超时 fallback（保守 L = 1500），warning 记录 `cold_start_failed` |
| OCR Producer 异常 | `try/finally` 保证哨兵入队；`process_many` `finally` 里 `await ocr_task` 让 exception 传出 |
| gap fill 与 OCR Producer 竞争 gpu_lock | gap fill 在 OCR 全部结束之后发生（单文档流程），无竞争；多子目录并发场景下同子目录无冲突，跨子目录由 `gpu_lock` 互斥串行 |
| 停用 DOC_BOUNDARY 回归覆盖 | `parse_doc_boundaries` / `_split_by_doc_boundaries` 单元测试保留；集成级 `test_boundary_gap_combo.py` 标 `@pytest.mark.skip(reason="流式版停用文档聚合，下一版解锁")` |
