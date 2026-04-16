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

Post-process OCR output: intra-page text cleaning + adjacent-page deduplication and merging + long-document segmentation. The three submodules are independent; Pipeline calls them in sequence.

## 2. File List

| File | Responsibility |
|---|---|
| `processing/cleaner.py` | OCR output cleaning (intra-page deduplication, garbled text removal, whitespace normalization) |
| `processing/dedup.py` | Adjacent-page overlap detection and merging, plus cross-page frequency filtering via `strip_repeated_lines()` |
| `processing/segmenter.py` | Document segmenter (splits at semantic boundaries with line-level context) |

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

### 3.2 strip_repeated_lines (module function in processing/dedup.py)

```python
def strip_repeated_lines(pages: list[PageOCR], config: DedupConfig) -> None:
    """跨页频率过滤：移除在多数页面中重复出现的侧栏噪声行。

    原地修改每页 cleaned_text。仅在总页数 ≥ config.repeated_line_min_pages 时启用；
    行在多少个不同页面出现达到 threshold_count = max(1, total × repeated_line_threshold)
    即视为噪声；实际移除时仅移除 ≥ repeated_line_min_block 的连续噪声块（防误删孤立行）。
    """
```

`PageDeduplicator.merge_all_pages()` calls this function internally to perform text-level sidebar removal (`enable_column_filter` is primarily for grounding-coordinate-level filtering; the two mechanisms work independently).

### 3.3 PageDeduplicator (processing/dedup.py)

```python
class PageDeduplicator:
    """相邻页重叠检测与合并"""

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

### 3.4 DocumentSegmenter (processing/segmenter.py)

When the document is long, Pipeline segments it first and sends each segment to LLM refinement individually. The segmenter does not depend on an LLM; it is pure text processing.

Core behavior of the segmenter:

- **Splitting strategy**: Heading-first splitting (`#`/`##`/`###`, etc.) -> secondary splitting at blank lines if a segment is too long -> merging small fragments to avoid fragmentation.
- **Context strategy**: To improve cross-segment coherence, the segmenter prepends/appends lines from adjacent segments (`overlap_lines`) **directly into `Segment.text`** (before/after).
  - This introduces "visible duplication," which the LLM is expected to remove during refinement.
  - Pipeline's `_reassemble()` phase simply joins each segment's `markdown` with `"\n".join(...)` -- it does not rely on any special markers.

```python
class DocumentSegmenter:
    def __init__(
        self,
        max_chars_per_segment: int = 12000,
        overlap_lines: int = 5,
    ) -> None:
        ...

    def segment(self, markdown: str) -> list[Segment]:
        """标题切分 → 空行二次切分 → 合并小块 → 拼入上下文行。"""
        ...
```

**Calling conventions**:
- Input: the complete markdown text after merging and deduplication
- Output: `list[Segment]`, each containing `text`/`start_line`/`end_line`
- Context (`overlap_before`/`overlap_after`) is constructed separately by Pipeline in `RefineContext`; `Segment` itself only contains text and line numbers

## 4. Dependencies

| Source | Usage |
|---|---|
| `models.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `Region`, `Segment` |
| `pipeline/config.py` | `DedupConfig` |

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
    ▼ PageDeduplicator.merge_all_pages([page1, page2, ...])
MergedDocument(markdown="complete merged/deduped text", images=[...], gaps=[])
    markdown contains:
    - <!-- page: {image_filename} --> page boundary markers
    - ![](page1_OCR/images/0.jpg) rewritten image references
    - Overlapping regions deduplicated (only one copy retained)
```
