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

对 OCR 输出进行后处理：页内文本清洗 + 相邻页去重合并。两个子模块各自独立，由 Pipeline 按顺序调用。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `processing/cleaner.py` | OCR 输出清洗（页内去重、乱码移除、空行规范化） |
| `processing/dedup.py` | 相邻页重叠检测与合并 |

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

### 3.2 PageDeduplicator（processing/dedup.py）

```python
class PageDeduplicator:
    """相邻页重叠检测与合并"""

    def __init__(self, config: DedupConfig) -> None: ...

    def merge_two_pages(self, text_a: str, text_b: str) -> MergeResult:
        """
        合并两页文本，返回合并结果。
        重叠区域用 <!-- overlap-start/end --> 标注。
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
        - 每页文本头部插入 <!-- page: DSC04654.jpg --> 标记
        - 供 LLM 精修时定位 gap 所在的照片

        图片引用重写：
        - OCR 产出的引用格式为 ![](images/0.jpg)（相对于 {stem}_OCR/ 目录）
        - 合并时重写为 ![](DSC04654_OCR/images/0.jpg)（相对于 output_dir）
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
- 重叠区域保留 `config.overlap_context_lines` 行上下文，用 HTML 注释标注给 LLM

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `PageOCR`, `MergeResult`, `MergedDocument`, `Region` |
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

合并结果：A 独有 + 重叠(一份, 加 overlap 注释) + B 独有
```

算法步骤：
1. 取 A 尾部 `search_ratio` 和 B 头部 `search_ratio` 的行
2. `SequenceMatcher.find_longest_match()` 做模糊匹配
3. 匹配度 > `similarity_threshold` 视为重叠
4. 拼接并用 `<!-- overlap-start -->` / `<!-- overlap-end -->` 标注重叠区域

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
    - <!-- page: DSC04654.jpg --> 页边界标记
    - ![](DSC04654_OCR/images/0.jpg) 重写后的图片引用
    - <!-- overlap-start/end --> 重叠区域标注
```