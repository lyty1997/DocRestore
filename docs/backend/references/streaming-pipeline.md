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
| LLM 段间并发 | **串行** | DOC_BOUNDARY 检测需有序处理，乱序会导致文档归属错误 |
| 终结化并行 | **继续消费** | doc N 终结化期间 OCR 和下一篇 LLM 不停，最大化吞吐 |
| PII 策略 | **Regex 先行 + 延迟实体检测** | 前 5 页积累后获取 lexicon，后续复用 |
| 进度模型 | **单通道不变** | OCR/refine 交替报告，前端无需改动 |
| 单文档兼容 | **先写子目录，最后移回** | 终结化时总文档数未知，全部完成后调整 |

## 3. 架构总览

### 3.1 组件关系

```
┌──────────────┐    Queue[PageOCR|None]    ┌──────────────────────┐
│ OCR Producer │ ────────────────────────▶ │   Stream Processor    │
│  (gpu_lock)  │                           │                       │
└──────────────┘                           │ ┌───────────────────┐ │
    逐张 OCR+清洗                           │ │IncrementalMerger  │ │
    不等 LLM                                │ │ (增量合并+追踪)    │ │
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │  积累 >= segment_size │
                                           │          ↓            │
                                           │ ┌───────────────────┐ │
                                           │ │StreamSegExtractor │ │
                                           │ │ (提取 segment)     │ │
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │ ┌───────────────────┐ │
                                           │ │  LLM Refine (串行)│ │
                                           │ └────────┬──────────┘ │
                                           │          ↓            │
                                           │   DOC_BOUNDARY?       │
                                           │     ├─ Yes → launch ──│──▶ asyncio.Task
                                           │     │   finalize()    │    (reassemble →
                                           │     │   reset state   │     gap fill →
                                           │     └─ No → continue  │     final refine →
                                           └──────────────────────┘     render)
```

### 3.2 并行时序

```
时间 ──────────────────────────────────────────────────▶
OCR:  [p1][p2][p3] [p4][p5][p6] [p7][p8][p9][p10]
             ↓           ↓                 ↓
LLM:     [seg1]    [seg2+BOUND]      [seg3]  [seg4]
                        ↓                       ↓
Final:            [Doc1: gap→refine→render]  [Doc2: gap→refine→render]
```

- OCR 逐张产出 PageOCR 放入 asyncio.Queue
- Stream Processor 消费页面，增量合并，积累够一段就送 LLM
- LLM 返回含 DOC_BOUNDARY → 后台 `asyncio.create_task` 终结化
- 终结化期间流处理器继续消费后续页面

### 3.3 GPU 锁竞争

终结化的 gap fill 需要 `reocr_page`（GPU），与 OCR Producer 竞争 `gpu_lock`。两者都用 `async with gpu_lock` 安全串行，不会死锁。OCR Producer 在等锁期间 await 挂起，不阻塞事件循环。

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

    def get_page_names_up_to(self, page_name: str) -> list[str]:
        """返回从开头到 page_name（含）的所有页面文件名列表。

        用途：DOC_BOUNDARY after_page 确定当前文档包含哪些页。
        如果 page_name 不存在，返回空列表。
        """

    def get_page_names_after(self, page_name: str) -> list[str]:
        """返回 page_name 之后（不含）的所有页面文件名列表。

        用途：确定下一篇文档包含哪些页。
        如果 page_name 不存在，返回全部页面名。
        """

    def get_images_for_pages(self, page_names: set[str]) -> list[Region]:
        """返回指定页面集合的所有 Region。"""

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

### 4.3 DocumentState

**文件**：`backend/docrestore/models.py`（新增 dataclass）

```python
@dataclass
class DocumentState:
    """流式处理中单篇文档的累积状态。

    由 _stream_process 维护。DOC_BOUNDARY 检测到时，
    当前 DocumentState 传给 _finalize_document，然后创建新的。
    """
    doc_index: int                                          # 文档序号（0-based）
    title: str = ""                                         # 标题
    refined_segments: list[RefinedResult] = field(default_factory=list)
    page_names: list[str] = field(default_factory=list)     # 属于此文档的页面
    images: list[Region] = field(default_factory=list)      # 属于此文档的图片
    gaps: list[Gap] = field(default_factory=list)           # 属于此文档的 gap
```

## 5. Pipeline 重构详细设计

**文件**：`backend/docrestore/pipeline/pipeline.py`

### 5.1 process_many() 入口改造

删除原有串行逻辑，改为启动 OCR 生产者 + 流式处理器。

```python
async def process_many(self, image_dir, output_dir, on_progress, llm_override, gpu_lock):
    # 1. 扫描图片、创建输出目录（不变）
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    images = await asyncio.to_thread(scan_images, image_dir)
    if not images:
        raise FileNotFoundError(f"未找到图片文件: {image_dir}")

    # 2. 创建页面队列（无限缓冲，OCR 不被阻塞）
    page_queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()

    # 3. 启动 OCR 生产者（后台协程）
    ocr_task = asyncio.create_task(
        self._ocr_producer(images, output_dir, gpu_lock, page_queue, _report)
    )

    # 4. 流式处理主循环
    try:
        results = await self._stream_process(
            page_queue, len(images), output_dir,
            llm_override, gpu_lock, _report,
        )
    finally:
        await ocr_task  # 确保 OCR 协程完成（异常时也要 await）

    # 5. 单文档兼容：如果只有 1 篇，将子目录内容移到根目录
    if len(results) == 1 and results[0].doc_dir:
        results[0] = await self._move_to_root(results[0], output_dir)

    return results if results else [self._empty_result(output_dir)]
```

### 5.2 _ocr_producer()

从现有 `_ocr_and_clean` 提取，改为往队列放。

```python
async def _ocr_producer(self, images, output_dir, gpu_lock, queue, report_fn):
    """OCR 生产者：逐张 OCR + 清洗，放入队列。OCR 完成后放入 None 哨兵。"""
    cleaner = OCRCleaner()
    for i, img in enumerate(images):
        # OCR（带 gpu_lock 保护）
        if gpu_lock is not None:
            async with gpu_lock:
                page = await self._ocr_engine.ocr(img, output_dir)
        else:
            page = await self._ocr_engine.ocr(img, output_dir)

        # 清洗
        await cleaner.clean(page)
        await self._save_debug(output_dir, f"{page.image_path.stem}_cleaned.md", page.cleaned_text)

        # Regex PII（逐页，轻量，不等 LLM）
        if self._config.pii.enable:
            redactor = PIIRedactor(self._config.pii)
            page.cleaned_text, _ = redactor.redact_regex_only(page.cleaned_text)

        await queue.put(page)
        report_fn("ocr", i + 1, len(images), f"OCR {i+1}/{len(images)}")

    await queue.put(None)  # 哨兵：所有 OCR 完成
```

**注意**：`PIIRedactor.redact_regex_only()` 是新增方法——只做结构化 regex（手机/邮箱/身份证/银行卡），不做实体检测。现有 `redact_snippet` 需要 lexicon 参数，这里 lexicon 尚未获取。

### 5.3 _stream_process()（核心流式处理器）

```python
async def _stream_process(self, page_queue, total_images, output_dir,
                           llm_override, gpu_lock, report_fn):
    """消费 OCR 页面队列，增量合并 + 分段精修 + 文档拆分。

    返回 list[PipelineResult]（按 doc_index 排序）。
    """
    llm_cfg = self._resolve_llm_config(llm_override)
    merger = IncrementalMerger(self._config.dedup)
    extractor = StreamSegmentExtractor(
        max_chars=llm_cfg.max_chars_per_segment,
        overlap_lines=llm_cfg.segment_overlap_lines,
    )
    refiner = self._get_refiner(llm_override)

    segmented_offset = 0          # 已提取 segment 的 markdown 偏移
    segment_index = 0             # 全局段序号
    current_doc = DocumentState(doc_index=0)
    finalize_tasks: list[asyncio.Task[PipelineResult]] = []
    assigned_pages: set[str] = set()  # 已分配给前序文档的页面
    entity_lexicon: EntityLexicon | None = None
    pii_entity_done = False

    # === 主循环：消费 OCR 页面 ===
    while True:
        page = await page_queue.get()
        if page is None:
            break

        merger.add_page(page)

        # PII 延迟实体检测（前 N 页后做一次）
        if (self._config.pii.enable
            and self._config.pii.redact_person_name
            and not pii_entity_done
            and merger.page_count >= _PII_DETECT_THRESHOLD):
            entity_lexicon = await self._delayed_pii_detect(merger, llm_override)
            pii_entity_done = True

        # 尝试提取 segment 并精修
        segmented_offset, segment_index, current_doc = await self._try_extract_and_refine(
            merger, extractor, refiner, segmented_offset, segment_index,
            current_doc, finalize_tasks, assigned_pages,
            output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
        )

    # === 处理剩余文本 ===
    md = merger.get_markdown()
    if segmented_offset < len(md):
        remaining, new_offset = extractor.extract_remaining(md, segmented_offset)
        if remaining.strip():
            result = await self._refine_one_segment(refiner, remaining, segment_index, 0)
            segment_index += 1
            # 处理可能的 boundary（复用同一逻辑）
            current_doc = self._handle_refined_result(
                result, current_doc, merger, extractor,
                finalize_tasks, assigned_pages,
                output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
            )

    # === 终结化最后一篇 ===
    remaining_pages = [n for n in merger.all_page_names if n not in assigned_pages]
    current_doc.page_names = remaining_pages
    current_doc.images = merger.get_images_for_pages(set(remaining_pages))
    if not current_doc.title:
        assembled = "\n".join(r.markdown for r in current_doc.refined_segments)
        current_doc.title = extract_first_heading(assembled)

    last_result = await self._finalize_document(
        current_doc, output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
    )

    # === 收集所有结果 ===
    bg_results: list[PipelineResult] = []
    if finalize_tasks:
        bg_results = list(await asyncio.gather(*finalize_tasks))

    all_results = bg_results + [last_result]
    # 按 doc_index 排序（PipelineResult 需要携带 doc_index）
    all_results.sort(key=lambda r: r._doc_index)
    return all_results
```

### 5.4 _try_extract_and_refine()（提取 + 精修循环）

从 `_stream_process` 提取的内循环，降低复杂度。

```python
async def _try_extract_and_refine(
    self, merger, extractor, refiner, segmented_offset, segment_index,
    current_doc, finalize_tasks, assigned_pages,
    output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
):
    """在 merger 有新文本后，尝试提取 segment 并精修。可能触发文档终结化。

    返回更新后的 (segmented_offset, segment_index, current_doc)。
    """
    md = merger.get_markdown()
    while True:
        seg = extractor.try_extract(md, segmented_offset)
        if seg is None:
            break
        seg_text, new_offset = seg

        result = await self._refine_one_segment(refiner, seg_text, segment_index, 0)
        report_fn("refine", segment_index + 1, 0, f"精修段 {segment_index + 1}")

        current_doc = self._handle_refined_result(
            result, current_doc, merger, extractor,
            finalize_tasks, assigned_pages,
            output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
        )

        segmented_offset = new_offset
        segment_index += 1

    return segmented_offset, segment_index, current_doc
```

### 5.5 _handle_refined_result()（处理精修结果 + boundary 检测）

```python
def _handle_refined_result(
    self, result, current_doc, merger, extractor,
    finalize_tasks, assigned_pages,
    output_dir, llm_override, gpu_lock, report_fn, entity_lexicon,
) -> DocumentState:
    """处理单个 segment 的精修结果。检测 DOC_BOUNDARY 并可能触发终结化。

    返回当前/新的 DocumentState。
    """
    cleaned_md, boundaries = parse_doc_boundaries(result.markdown)
    cleaned_result = RefinedResult(markdown=cleaned_md, gaps=result.gaps, truncated=result.truncated)

    if not boundaries:
        current_doc.refined_segments.append(cleaned_result)
        current_doc.gaps.extend(result.gaps)
        return current_doc

    # 有 DOC_BOUNDARY
    boundary = boundaries[0]
    before, after = self._split_refined_at_boundary(cleaned_md, boundary)

    # 完成当前文档
    if before.strip():
        current_doc.refined_segments.append(RefinedResult(markdown=before, gaps=result.gaps))
    current_doc.page_names = merger.get_page_names_up_to(boundary.after_page)
    current_doc.images = merger.get_images_for_pages(set(current_doc.page_names))
    assigned_pages.update(current_doc.page_names)
    if not current_doc.title:
        assembled = "\n".join(r.markdown for r in current_doc.refined_segments)
        current_doc.title = extract_first_heading(assembled)

    # 后台终结化
    task = asyncio.create_task(
        self._finalize_document(current_doc, output_dir, llm_override, gpu_lock, report_fn, entity_lexicon)
    )
    finalize_tasks.append(task)
    report_fn("finalize", current_doc.doc_index + 1, 0, f"终结化文档 {current_doc.doc_index + 1}")

    # 创建新文档
    new_doc = DocumentState(doc_index=current_doc.doc_index + 1, title=boundary.new_title)
    extractor.reset()  # 清除 overlap 历史
    if after.strip():
        new_doc.refined_segments.append(RefinedResult(markdown=after))
    return new_doc
```

### 5.6 _split_refined_at_boundary()

```python
@staticmethod
def _split_refined_at_boundary(
    cleaned_md: str,
    boundary: DocBoundary,
) -> tuple[str, str]:
    """将已精修的 segment 在 boundary 处一分为二。

    策略：找到 boundary.after_page 对应的 page marker，
    再找到紧随其后的下一个 page marker 位置，在该位置切分。

    返回 (before_text, after_text)。
    如果找不到对应 page marker，返回 (cleaned_md, "")。
    """
    markers = list(_PAGE_MARKER_RE.finditer(cleaned_md))
    # 找 after_page 对应的 marker
    after_idx = None
    for i, m in enumerate(markers):
        if m.group(1).strip() == boundary.after_page:
            after_idx = i
    if after_idx is None:
        return cleaned_md, ""

    # 找下一个 page marker
    if after_idx + 1 < len(markers):
        split_pos = markers[after_idx + 1].start()
        return cleaned_md[:split_pos], cleaned_md[split_pos:]

    # after_page 是最后一页 → 全部属于当前文档
    return cleaned_md, ""
```

### 5.7 _finalize_document()

```python
async def _finalize_document(
    self, doc_state: DocumentState, output_dir: Path,
    llm_override, gpu_lock, report_fn, entity_lexicon,
) -> PipelineResult:
    """终结化单篇文档：reassemble → gap fill → final refine → render。

    可在后台 asyncio.Task 中执行（与 OCR/LLM 并发）。
    gap fill 的 reocr_page 使用 gpu_lock 与 OCR Producer 安全竞争。
    """
    # 重组
    reassembled_md = "\n".join(r.markdown for r in doc_state.refined_segments)
    sub_doc = MergedDocument(markdown=reassembled_md, images=doc_state.images)

    # 输出目录（始终写子目录，单文档兼容由 process_many 最后处理）
    dirname = sanitize_dirname(doc_state.title) or f"文档_{doc_state.doc_index + 1}"
    sub_output = output_dir / dirname
    await asyncio.to_thread(sub_output.mkdir, parents=True, exist_ok=True)

    # Gap fill + final refine（复用现有方法）
    pages_for_gap = ...  # 从 doc_state.page_names 构造 sub_pages
    sub_doc = await self._maybe_fill_gaps(sub_doc, doc_state.gaps, pages_for_gap, ...)
    sub_doc, truncated = await self._do_final_refine(sub_doc, sub_output, ...)

    # 渲染
    renderer = Renderer(self._config.output)
    doc_path = await renderer.render(sub_doc, sub_output)
    final_md = await asyncio.to_thread(doc_path.read_text, encoding="utf-8")

    doc_dir = sub_output.name
    return PipelineResult(
        output_path=doc_path, markdown=final_md,
        images=sub_doc.images, gaps=doc_state.gaps,
        doc_title=doc_state.title, doc_dir=doc_dir,
        warnings=self._collect_warnings(doc_state.refined_segments, doc_state.gaps, truncated),
        _doc_index=doc_state.doc_index,  # 排序用，不暴露给 API
    )
```

### 5.8 _move_to_root()（单文档兼容）

```python
async def _move_to_root(self, result: PipelineResult, output_dir: Path) -> PipelineResult:
    """将单文档从子目录移到根目录（兼容旧输出结构）。

    移动 document.md 和 images/ 到 output_dir，删除空子目录。
    更新 PipelineResult 的 output_path 和 doc_dir。
    """
```

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

## 7. PipelineResult 临时排序字段

`PipelineResult` 新增内部字段用于终结化后排序：

```python
@dataclass
class PipelineResult:
    # ... 现有字段
    _doc_index: int = 0  # 内部排序用，不序列化到 API
```

或者用 `dataclasses.field(repr=False, compare=False)` 隐藏。
API schema 不暴露此字段。

## 8. 进度报告

保持单通道 `TaskProgress`，stage 交替出现：

| stage | current | total | 时机 |
|-------|---------|-------|------|
| `ocr` | i | N（已知） | 每张 OCR 完成 |
| `refine` | seg_idx | 0（未知） | 每段精修完成 |
| `finalize` | doc_idx | 0（未知） | 启动文档终结化 |
| `gap_fill` | gi | len(gaps) | gap fill 中（复用现有） |
| `final_refine` | 0 | 1 | 整篇精修（复用现有） |
| `render` | 1 | 1 | 渲染完成（复用现有） |

前端只显示最新 stage，无需改动。

## 9. 被删除/替代的代码

| 旧方法 | 处理 |
|--------|------|
| `_ocr_and_clean()` | 由 `_ocr_producer()` 替代 |
| `_refine_segments()` | 由 `_stream_process` 内循环替代 |
| `_reassemble()` | 由 `_finalize_document` 内联 `"\n".join()` 替代 |
| `_split_by_doc_boundaries()` | **保留**（已有测试依赖），但 process_many 不再调用 |

保留 `_split_by_doc_boundaries` 的原因：6 个单元测试依赖它，且它可作为非流式 fallback。

## 10. 需要修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/docrestore/processing/dedup.py` | 新增类 | `IncrementalMerger` |
| `backend/docrestore/processing/segmenter.py` | 新增类 | `StreamSegmentExtractor` |
| `backend/docrestore/models.py` | 新增 | `DocumentState`；`PipelineResult` 加 `_doc_index` |
| `backend/docrestore/pipeline/pipeline.py` | **重构** | `process_many` → 流式；新增 5+ 方法 |
| `backend/docrestore/privacy/redactor.py` | 新增方法 | `redact_regex_only()` |
| `tests/processing/test_incremental_merger.py` | 新增 | 一致性测试 |
| `tests/llm/test_stream_segmenter.py` | 新增 | 切点/overlap/边界 |
| `tests/pipeline/test_streaming_pipeline.py` | 新增 | 流式集成测试 |
| `docs/modules/pipeline.md` | 更新 | 流式架构描述 |
| `docs/progress.md` | 更新 | — |

## 11. 实施顺序

| 步骤 | 内容 | 测试要求 |
|------|------|---------|
| 1 | `IncrementalMerger` | 逐页 add 后 get_markdown() 与 merge_all_pages 结果完全一致 |
| 2 | `StreamSegmentExtractor` | 切点优先级正确；overlap 正确；< max 返回 None |
| 3 | `DocumentState` + `PipelineResult._doc_index` | 随 pipeline 测试 |
| 4 | `PIIRedactor.redact_regex_only()` | 单元测试 |
| 5 | Pipeline 重构（_ocr_producer + _stream_process + _finalize_document） | 集成测试 |
| 6 | 单文档 _move_to_root 兼容 | 测试单/多文档输出目录结构 |
| 7 | 现有 236 个测试全部通过 | 回归验证 |
| 8 | 文档更新 | — |

## 12. 风险与应对

| 风险 | 应对 |
|------|------|
| 增量合并与批量合并结果不一致 | 严格复用 `merge_two_pages` + 一致性对比测试 |
| DOC_BOUNDARY 跨 segment 边界（LLM 只看到一半上下文） | 切点优先选 page marker，降低截断概率；overlap 覆盖转折处 |
| 终结化与 OCR 竞争 gpu_lock | 两者都用 `async with gpu_lock` 安全串行 |
| 单文档兼容（目录结构） | 最后 `_move_to_root` 调整 |
| 后台终结化异常 | `asyncio.gather` 收集异常，不影响其他文档 |
| OCR Producer 异常 | 需确保哨兵仍被放入队列（用 try/finally） |
