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

"""输出渲染器

将精修后的文档渲染为最终输出：汇总插图、重写引用、写入 document.md。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import aiofiles

from docrestore.models import MergedDocument
from docrestore.pipeline.config import OutputConfig


class Renderer:
    """将精修后的文档渲染为最终输出文件"""

    def __init__(self, config: OutputConfig) -> None:
        self._config = config

    async def render(
        self,
        document: MergedDocument,
        output_dir: Path,
        ocr_root_dir: Path | None = None,
    ) -> tuple[Path, str]:
        """渲染流程：

        1. 扫描 markdown 中 ![]({stem}_OCR/images/0.jpg) 引用
        2. 复制插图到 output_dir/images/，重命名为 {stem}_{idx}.jpg
        3. 重写 markdown 引用
        4. **写入磁盘时**剥除页边界 marker（下载版 / 最终交付版）
        5. **返回的内存 markdown 保留 marker**（前端预览用，供左右同步滚动
           hook 按 `<!-- page: xxx.jpg -->` 定位锚点）
        6. 返回 `(document.md 路径, 带 marker 的 markdown 原文)`

        ocr_root_dir: OCR 输出所在的根目录（多文档时与 output_dir 不同）。
                      为 None 时回退到 output_dir。
        """
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # OCR 子目录在根目录下，多文档时 output_dir 是子目录
        root = ocr_root_dir if ocr_root_dir is not None else output_dir

        # 扫描并重写图片引用（此时 markers 还在）
        markdown_with_markers = self._rewrite_and_copy_images(
            document.markdown, root, images_dir
        )
        # 清理多余空行（保留 markers）
        markdown_with_markers = re.sub(
            r"\n{3,}", "\n\n", markdown_with_markers,
        ).strip() + "\n"

        # 磁盘版：去掉 page markers（下载用户不需要看到 HTML 注释）
        markdown_for_disk = re.sub(
            r"<!--\s*page:\s*[^>]*-->\n?", "", markdown_with_markers,
        )
        markdown_for_disk = re.sub(
            r"\n{3,}", "\n\n", markdown_for_disk,
        ).strip() + "\n"

        # 写入 document.md（剥除版）
        doc_path = output_dir / "document.md"
        async with aiofiles.open(
            doc_path, "w", encoding="utf-8"
        ) as f:
            await f.write(markdown_for_disk)

        return doc_path, markdown_with_markers

    def _rewrite_and_copy_images(
        self,
        markdown: str,
        ocr_root_dir: Path,
        images_dir: Path,
    ) -> str:
        """扫描图片引用，复制文件并重写路径。

        支持两种格式：
        - markdown: ![alt]({stem}_OCR/images/0.jpg)
        - HTML: <img src="{stem}_OCR/images/0.jpg" ...>

        ocr_root_dir: OCR 输出 ({stem}_OCR/) 所在的根目录。
        """
        def _copy_image(
            stem: str, idx: str, ext: str,
        ) -> str:
            """复制图片到输出目录，返回新文件名。"""
            src = (
                ocr_root_dir / f"{stem}_OCR" / "images" / f"{idx}.{ext}"
            )
            new_name = f"{stem}_{idx}.{ext}"
            dst = images_dir / new_name

            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)

            return new_name

        # markdown 格式：![alt]({stem}_OCR/images/0.jpg)
        md_pattern = re.compile(
            r"!\[([^\]]*)\]\("
            r"([A-Za-z0-9_]+)_OCR/images/"
            r"(\d+)\.(\w+)"
            r"\)"
        )

        def replace_md(m: re.Match[str]) -> str:
            """替换 markdown 图片引用。"""
            alt, stem, idx, ext = (
                m.group(1), m.group(2), m.group(3), m.group(4),
            )
            new_name = _copy_image(stem, idx, ext)
            return f"![{alt}](images/{new_name})"

        markdown = md_pattern.sub(replace_md, markdown)

        # HTML 格式：src="{stem}_OCR/images/0.jpg"
        html_pattern = re.compile(
            r'src="'
            r"([A-Za-z0-9_]+)_OCR/images/"
            r"(\d+)\.(\w+)"
            r'"'
        )

        def replace_html(m: re.Match[str]) -> str:
            """替换 HTML img src 引用。"""
            stem, idx, ext = m.group(1), m.group(2), m.group(3)
            new_name = _copy_image(stem, idx, ext)
            return f'src="images/{new_name}"'

        return html_pattern.sub(replace_html, markdown)
