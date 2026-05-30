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

# Processing Layer (processing/)

## 1. Responsibilities

Post-process OCR output. The plain document mode covers intra-page text cleaning, adjacent-page deduplication and merging, and long-document segmentation; code mode additionally covers IDE layout detection, code column assembly, cross-page source file grouping, reference source lookup, and lightweight diagnostics. Each module stays a pure processing routine; Pipeline orchestrates them according to task configuration.

## 2. File List

| File | Responsibility |
|---|---|
| `processing/cleaner.py` | OCR output cleaning (intra-page deduplication, garbled text removal, whitespace normalization) |
| `processing/dedup.py` | Adjacent-page overlap detection and merging: `IncrementalMerger` (streaming per-page incremental) + `PageDeduplicator` (batch reference implementation) |
| `processing/segmenter.py` | Document segmentation: `StreamSegmentExtractor` (streaming incremental extraction, production path) + `DocumentSegmenter` (batch full split, reference implementation) |
| `processing/ide_layout.py` | IDE screenshot layout analysis, producing code-region, sidebar, and meta-info candidates |
| `processing/code_assembly.py` | Assemble OCR `text_lines` into code columns using line-number anchors |
| `processing/code_column_ocr.py` | Crop / enhance the detected code column and re-run OCR (`secondary_column_ocr`, off by default) |
| `processing/code_file_grouping.py` | Aggregate cross-image `PageColumn`s into `SourceFile`s by path / filename |
| `processing/code_context.py` | Retrieve relevant snippets from a read-only reference source tree to assist scoped repair |
| `processing/code_diagnostics.py` | Lightweight multi-language diagnostics emitting syntax / semantic / dependency annotations |
| `processing/ide_meta_extract.py` | Extract path, filename, and language candidates from IDE title bar / tab / breadcrumb |
| `processing/ocr_postfix.py` | OCR character-level post-processing and common-confusion fixes |

> `preprocessor.py` / `ngram_filter.py` reside under `ocr/` (used inside the worker); see [ocr.md](ocr.md).

## 3. Public Interface

### 3.1 OCRCleaner (processing/cleaner.py)

```python
class OCRCleaner:
    """OCR 输出清洗器"""

    async def clean(self, page: PageOCR) -> PageOCR:
        """
        读取 page.output_dir/result.mmd，清洗后填充 cleaned_text。
        步骤：remove_repetitions → remove_garbage → normalize_whitespace
        返回同一个 PageOCR 对象（cleaned_text 已填充）
        """
```

**Calling conventions**:
- Input: `PageOCR` produced by the OCR layer (`raw_text` populated, `cleaned_text` empty)
- Output: the same `PageOCR` object with `cleaned_text` populated
- Async interface: internal file I/O uses `aiofiles` to read `result.mmd`
- Based on `result.mmd` (grounding has already been processed inside the OCR engine)

### 3.2 IncrementalMerger / PageDeduplicator (processing/dedup.py)

The streaming production path uses **`IncrementalMerger`**: the consumer calls `add_page(page)` for each
page it receives to roll the incremental merge forward, internally reusing `merge_two_pages`'
suffix-prefix anchored deduplication, append-only without rewriting already-merged text.
`PageDeduplicator.merge_all_pages()` is the equivalent **batch reference implementation**, kept as the
consistency test baseline for `IncrementalMerger` (`tests/processing/test_incremental_merger.py` asserts
the two outputs are character-for-character equal).

```python
class IncrementalMerger:
    """流式逐页增量合并去重。"""

    def __init__(self, config: DedupConfig) -> None: ...
    def add_page(self, page: PageOCR) -> None: ...   # 逐页喂入
    def get_markdown(self) -> str: ...               # 当前已合并文本
    def get_all_images(self) -> list[Region]: ...
    @property
    def page_count(self) -> int: ...


class PageDeduplicator:
    """批量相邻页重叠检测与合并（参考实现 / 测试基准）"""

    def __init__(self, config: DedupConfig) -> None: ...

    def merge_two_pages(self, text_a: str, text_b: str) -> MergeResult:
        """
        合并两页文本，返回合并结果。
        检测重叠区域并只保留一份。
        """

    def merge_all_pages(
        self,
        pages: list[PageOCR],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> MergedDocument:
        """
        滚动合并所有页面：
        merged = page[0] → 逐页 merge_two_pages(merged, page[i])
        同时收集所有页的 regions 汇总到 MergedDocument.images

        页边界标记：
        - 每页文本头部插入 <!-- page: page1.jpg --> 标记
        - 供 LLM 精修时定位 gap 所在的照片

        图片引用重写：
        - OCR 产出的引用格式为 ![](images/0.jpg)（相对于 {stem}_OCR/ 目录）
        - 合并时重写为 ![](page1_OCR/images/0.jpg)（相对于 output_dir）
        - 确保 Renderer 能根据路径找到源文件
        """
```

**Calling conventions**:
- The constructor receives a `DedupConfig` (consistent with the style of OCR engines receiving `OCRConfig`)
- `merge_two_pages()` takes two plain text strings (`cleaned_text`) and returns a `MergeResult`
- `merge_all_pages()` takes a list of `PageOCR` (must have `cleaned_text` populated) and returns a `MergedDocument`
- `merge_all_pages()` collects each page's `PageOCR.regions` into `MergedDocument.images`
- A `<!-- page: {image_filename} -->` marker is inserted at the head of each page's text for LLM gap location
- Image references are rewritten from `![](images/N.jpg)` to `![]({stem}_OCR/images/N.jpg)`
- Overlapping regions are detected and deduplicated (only one copy is kept); no extra markers are inserted

### 3.3 Document Segmentation (processing/segmenter.py)

The streaming production path uses **`StreamSegmentExtractor`**: it cuts one segment at a time from the
growing markdown (`try_extract`); the segment length `max_chars` is passed in on each call (the
runtime-adaptive L* from `RateController`), and it only does backward overlap (future text is unknown in
streaming). `DocumentSegmenter` is the non-streaming full-split variant (splits the entire text at once),
kept as a reference implementation.

Their shared splitting logic: heading-first splitting (`#`/`##`/`###`) -> secondary splitting at blank
lines if too long -> merging small fragments. Overlap is embedded into the segment text as context
(introducing "visible duplication," removed by the LLM during refinement); Pipeline's `_reassemble()`
phase only joins each segment's `markdown` with `"\n".join(...)` and does not rely on any special markers.

```python
class StreamSegmentExtractor:
    def __init__(self, overlap_lines: int = 5) -> None: ...

    def try_extract(
        self, full_text: str, offset: int, max_chars: int,
    ) -> tuple[str, int] | None:
        """文本够长则切出一段，返回 (段文本, 新 offset)；不够长返回 None。"""
        ...

    def extract_remaining(
        self, full_text: str, offset: int,
    ) -> tuple[str, int]:
        """哨兵后处理尾段（剩余全部文本）。"""
        ...
```

**Calling conventions**:
- Input: the current merged complete markdown + the last `offset` cut to + this call's segment-length cap
- Output: `(seg_text, new_offset)` or `None` (insufficient text, wait for more pages)
- Context (`overlap_before`/`overlap_after`) is constructed separately by Pipeline in `RefineContext`

### 3.4 Code Mode Processing Chain

When `PipelineConfig.code.enable` is on, Pipeline no longer feeds OCR text into the plain markdown segmenter; instead it consumes `PageOCR.text_lines` through the IDE-specific chain:

1. `ide_layout.py` detects the code region and meta-info regions (sidebar / breadcrumb) in the IDE screenshot.
2. `code_assembly.py` assembles each image's code columns from line-level bboxes and line-number anchors, retaining source page, column index, bbox, line-number range, and quality flags.
3. `ide_meta_extract.py` extracts `filename` / `path` / `language` / `path_candidates` / `path_confidence`.
4. `code_file_grouping.py` aggregates `PageColumn`s into `SourceFile`s, merging across pages by path / filename and keeping only the first copy when line numbers overlap; unresolved gaps surface as flags to the quality report.
5. `llm/code_refine.py` and `llm/code_repair.py` perform character-level refinement and diagnostic-driven scoped repair on `SourceFile.merged_text`.
6. `code_diagnostics.py` runs lightweight diagnostics either before writing out or live in the editor. Standard-library parsers take priority; missing external tools degrade to `tool_unavailable` rather than failing the task.

`CodeDiagnosticRunner` currently supports standard-library parsing for Python / JSON / TOML / XML / YAML, and external-tool checks for JavaScript / TypeScript / C / C++ / Go / Rust. The diagnoser masks already-located syntax errors in a temporary copy, generates stub headers for missing includes and re-runs to surface subsequent independent errors, and scans the code region for CJK / full-width OCR noise.

### 3.5 Code Mode Design Rationale (v1 → v2 → v3)

> This section condenses the key design reversal and multi-dataset validation behind IDE code-mode layout detection, so maintainers understand why line-number anchors are used. Detailed per-dataset statistics were retired with the historical design docs and can be retrieved from git history.

**v1 (abandoned) — pixel-variance geometric splitting**: detected and stripped IDE UI geometrically, then split columns by fixed ratios. The fundamental error was assuming fixed proportions for sidebar/tab/terminal — real IDEs are freely draggable (resizable splits, collapsible sidebar, font zoom, varying resolution), so every fixed threshold breaks. Across 8 spike images, 7/8 fell back to sidebar handling and 1/8 to a hard column cut — unusable. Alternatives surveyed (PaddleOCR-VL `merge_layout_blocks=False`, PP-DocBlockLayout lowered threshold, PP-StructureV3 reading-order) were all unusable; the last is directionally opposite and wrongly merges multi-column code into a single column.

**v2 (current direction) — line-number column anchors**: uses the editor's intrinsic invariant — the line-number column — as a layout anchor (`text=^\d{1,4}$` + score≥0.8 → x1 clustering → monotonicity filtering → `LineNumberAnchor`). It is fully independent of font/zoom/drag/sidebar state, data-driven, and inherits OCR fault tolerance.

**v3 correction (key lesson)**: the v2 first cut added "unpaired_codes inference insertion," seemingly recovering 6396 lines of code. After the user questioned "are we recovering garbage," a sampling audit found ~50% were OCR-fragmented shards plus breadcrumb / git-blame / status-bar UI noise — the "6396 lines" was a misleading metric. v3 therefore: (1) rolled back forced insertion, leaving unpaired lines flagged only and deferred to LLM refinement; (2) changed `ide_layout` region classification from bbox edges to **bbox center point** for above/below_code, keeping UI noise out of columns at the source (root fix); (3) capped `anchor.num_range` at 3000 to filter extreme-noise anchors (e.g. stack-trace PIDs) while still passing genuinely long files.

**Multi-dataset robustness (1259 images / 6 datasets, v3 final)**:

| Scenario | Detection / success | Notes |
|---|---|---|
| IDE code scenes (1137 imgs, 4 datasets) | **99.82%** (1135/1137) | 2 misses are binary/image diffs with no line-number column |
| Column-count adaptivity | 1 / 2 / 3+ columns all covered | First single- and 3-column cases seen in ide_diff (git diff old/new line numbers + right-side file) |
| False positives | **0%** | 73 non-code images (plain docs + Lark docs) never misidentified as code |

Core lessons: (1) a "good-looking metric" ≠ actual quality — multi-dataset audits are indispensable; (2) fixing upstream boundary classification is robust, forced insertion only treats symptoms; (3) conservative code (no forced unpaired insertion) gives LLM refinement a clean base, which beats patching from polluted data.

## 4. Dependencies

| Source | Usage |
|---|---|
| `models.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `Region`, `Segment` |
| `pipeline/config.py` | `DedupConfig`, `CodeRestoreConfig` |
| `processing/code_file_grouping.py` | `PageColumn`, `SourceFile` |

Does not depend on the OCR layer, LLM layer, or output layer.

## 5. Internal Implementation

### 5.1 OCRCleaner Cleaning Flow

```python
def remove_repetitions(self, text: str) -> str:
    """按空行分段，SequenceMatcher 比较相邻段落，相似度 > 0.9 的只保留第一个"""

def remove_garbage(self, text: str, threshold: int = 20) -> str:
    """移除连续非 CJK/ASCII 字符超过 threshold 的片段"""

def normalize_whitespace(self, text: str) -> str:
    """压缩连续 3+ 空行为 2 个"""
```

### 5.2 PageDeduplicator Dedup Algorithm

```
Photo A OCR output:              Photo B OCR output:
┌──────────────────┐         ┌──────────────────┐
│ A-only content    │         │ Overlapping region│
│ Overlapping region│         │ B-only content    │
└──────────────────┘         └──────────────────┘

Merge result: A-only + Overlap (one copy) + B-only
```

Algorithm steps:
1. Take the trailing `search_ratio` lines of A and the leading `search_ratio` lines of B
2. `SequenceMatcher.find_longest_match()` for fuzzy matching
3. Matches above `similarity_threshold` are considered overlapping
4. Concatenate (keep A's version of the overlapping region, trim B's duplicate portion)

## 6. Data Flow

```
PageOCR(raw_text, cleaned_text="")
    │
    ▼ OCRCleaner.clean()  [async]
PageOCR(raw_text, cleaned_text="cleaned text")
    │
    ▼ IncrementalMerger.add_page(page)  per-page feed (streaming production path)
    ▼ merger.get_markdown()  →  current merged/deduped text
(batch equivalent: PageDeduplicator.merge_all_pages([...]) → MergedDocument, as test baseline)
    markdown contains:
    - <!-- page: {image_filename} --> page boundary markers
    - ![](page1_OCR/images/0.jpg) rewritten image references
    - Overlapping regions deduplicated (only one copy retained)
```
