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

# 处理层（processing/）

## 1. 职责

对 OCR 输出进行后处理：页内文本清洗 + 相邻页去重合并 + 长文档分段。三个子模块各自独立，由 Pipeline 按顺序调用。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `processing/cleaner.py` | OCR 输出清洗（页内去重、乱码移除、空行规范化） |
| `processing/dedup.py` | 相邻页重叠检测与合并，附跨页频率过滤 `strip_repeated_lines()` |
| `processing/segmenter.py` | 文档分段器（按语义边界切分，附加行级上下文） |

> `preprocessor.py` / `ngram_filter.py` 位于 `ocr/` 下（worker 内部使用），见 [ocr.md](ocr.md)。

## 3. 对外接口

### 3.1 OCRCleaner（processing/cleaner.py）

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

**调用约定**：
- 输入：OCR 层产出的 `PageOCR`（`raw_text` 已填充，`cleaned_text` 为空）
- 输出：同一个 `PageOCR`，`cleaned_text` 被填充
- 异步接口：内部文件 IO 使用 `aiofiles` 读取 `result.mmd`
- 基于 `result.mmd`（grounding 已在 OCR 引擎内部处理完毕）

### 3.2 strip_repeated_lines（processing/dedup.py 模块函数）

```python
def strip_repeated_lines(pages: list[PageOCR], config: DedupConfig) -> None:
    """跨页频率过滤：移除在多数页面中重复出现的侧栏噪声行。

    原地修改每页 cleaned_text。仅在总页数 ≥ config.repeated_line_min_pages 时启用；
    行在多少个不同页面出现达到 threshold_count = max(1, total × repeated_line_threshold)
    即视为噪声；实际移除时仅移除 ≥ repeated_line_min_block 的连续噪声块（防误删孤立行）。
    """
```

`PageDeduplicator.merge_all_pages()` 内部会调用本函数做文本级侧栏去除（`enable_column_filter` 主要用于 grounding 坐标级过滤，两者独立生效）。

### 3.3 PageDeduplicator（processing/dedup.py）

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

**调用约定**：
- 构造函数接收 `DedupConfig`（与 OCR 引擎接收 `OCRConfig` 风格一致）
- `merge_two_pages()` 输入两段纯文本（`cleaned_text`），返回 `MergeResult`
- `merge_all_pages()` 输入 `PageOCR` 列表（需已填充 `cleaned_text`），返回 `MergedDocument`
- `merge_all_pages()` 负责收集各页 `PageOCR.regions` 汇总到 `MergedDocument.images`
- 每页文本头部插入 `<!-- page: {image_filename} -->` 标记，供 LLM 定位 gap
- 图片引用从 `![](images/N.jpg)` 重写为 `![]({stem}_OCR/images/N.jpg)`
- 重叠区域检测后只保留一份，不插入额外标记

### 3.4 DocumentSegmenter（processing/segmenter.py）

当文档较长时，Pipeline 会先分段再逐段送入 LLM 精修。分段器不依赖 LLM，纯文本处理。

分段器的核心行为：

- **切分策略**：标题优先切分（`#`/`##`/`###` 等） → 过长则在空行处二次切分 → 合并过小片段避免碎片化。
- **上下文策略**：为提升跨段连贯性，分段器会把相邻段的部分行（`overlap_lines`）作为上下文**直接拼进 `Segment.text`**（前置/后置）。
  - 这会引入“可见重复”，期望由 LLM 在精修时删除明显重复。
  - Pipeline 在 `_reassemble()` 阶段仅对各段 `markdown` 做简单 `"\n".join(...)` 重组，不再依赖任何特殊标记。

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

**调用约定**：
- 输入：合并去重后的完整 markdown 文本
- 输出：`list[Segment]`，每段含 `text`/`start_line`/`end_line`
- 上下文（`overlap_before`/`overlap_after`）在 `RefineContext` 中由 Pipeline 单独构造，`Segment` 本身只含文本与行号

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `Region`, `Segment` |
| `pipeline/config.py` | `DedupConfig` |

不依赖 OCR 层、LLM 层或输出层。

## 5. 内部实现

### 5.1 OCRCleaner 清洗流程

```python
def remove_repetitions(self, text: str) -> str:
    """按空行分段，SequenceMatcher 比较相邻段落，相似度 > 0.9 的只保留第一个"""

def remove_garbage(self, text: str, threshold: int = 20) -> str:
    """移除连续非 CJK/ASCII 字符超过 threshold 的片段"""

def normalize_whitespace(self, text: str) -> str:
    """压缩连续 3+ 空行为 2 个"""
```

### 5.2 PageDeduplicator 去重算法

```
照片 A 的 OCR 输出：          照片 B 的 OCR 输出：
┌──────────────────┐         ┌──────────────────┐
│ A 独有内容        │         │ 重叠区域          │
│ 重叠区域          │         │ B 独有内容        │
└──────────────────┘         └──────────────────┘

合并结果：A 独有 + 重叠(一份) + B 独有
```

算法步骤：
1. 取 A 尾部 `search_ratio` 和 B 头部 `search_ratio` 的行
2. `SequenceMatcher.find_longest_match()` 做模糊匹配
3. 匹配度 > `similarity_threshold` 视为重叠
4. 拼接（保留 A 版本的重叠区域，裁剪 B 的重复部分）

## 6. 数据流

```
PageOCR(raw_text, cleaned_text="")
    │
    ▼ OCRCleaner.clean()  [async]
PageOCR(raw_text, cleaned_text="清洗后文本")
    │
    ▼ PageDeduplicator.merge_all_pages([page1, page2, ...])
MergedDocument(markdown="合并去重后的完整文本", images=[...], gaps=[])
    markdown 中包含：
    - <!-- page: {image_filename} --> 页边界标记
    - ![](page1_OCR/images/0.jpg) 重写后的图片引用
    - 重叠区域已去重（只保留一份）
```