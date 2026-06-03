# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PPT 模式输出渲染（S4 / AGE-88）

多页 ``PageOCR`` → 单页按 VL 阅读序组装 → 多页按输入文件序合并为单个
``document.md``。与文档模式的本质差异：**不跨页去重**（每页是独立幻灯片，
照片序即页序）。图片复制 / 引用重写 / page-marker 处理复用文档模式 ``Renderer``。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.models import MergedDocument, PageOCR, Region
from docrestore.output.renderer import Renderer
from docrestore.pipeline.config import OutputConfig
from docrestore.processing.dedup import rewrite_image_refs_to_ocr_dir

#: 页间分隔：markdown 分隔线（前后空行由 Renderer 统一清理）
_PAGE_SEPARATOR = "\n\n---\n\n"


async def render_ppt_document(
    pages: list[PageOCR],
    output_dir: Path,
    *,
    output_config: OutputConfig | None = None,
) -> tuple[Path, str]:
    """单页保序组装 + 多页按文件序合并为单个 ``document.md``。

    - 每页前插 ``<!-- page: {filename} -->`` 锚点；图片引用加 ``{stem}_OCR`` 前缀
      （复用 ``rewrite_image_refs_to_ocr_dir``，与文档模式同一真相源）。
    - 页间用 markdown 分隔线分隔，**不跨页去重**（每页独立幻灯片，保序）。
    - 复用 ``Renderer.render``：裁图复制到 ``images/{stem}_N``、磁盘版去锚点、
      内存版保留锚点（前端滚动定位）。

    ``pages`` 须已按输入文件顺序（``scan_images`` 序）排列。
    返回 ``(document.md 路径, 含 page-marker 的内存版 markdown)``。
    """
    sections: list[str] = []
    all_regions: list[Region] = []
    for page in pages:
        marker = f"<!-- page: {page.image_path.name} -->"
        body = rewrite_image_refs_to_ocr_dir(page).strip()
        sections.append(f"{marker}\n{body}")
        all_regions.extend(page.regions)

    document = MergedDocument(
        markdown=_PAGE_SEPARATOR.join(sections),
        images=all_regions,
    )
    renderer = Renderer(output_config or OutputConfig())
    return await renderer.render(
        document, output_dir, ocr_root_dir=output_dir,
    )
