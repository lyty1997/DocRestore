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

"""测试用 PDF fixture 构造：用 Pillow 生成多页 PDF。

不可用 reportlab（非依赖）/ pypdfium2（只渲不造）；Pillow 是 runtime 依赖。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def make_pdf(
    path: Path,
    page_labels: list[str],
    size: tuple[int, int] = (300, 400),
) -> None:
    """用 Pillow 构造多页 PDF（每页画一个标签文字，便于派生断言）。

    显式 ``Image.init()`` 注册 JPEG SAVE 处理器——Pillow 懒加载下首次 RGB→PDF
    保存会因 ``Image.SAVE['JPEG']`` 未注册而 KeyError，见 docs/zh/known-issues.md。
    """
    Image.init()
    pages: list[Image.Image] = []
    for label in page_labels:
        im = Image.new("RGB", size, "white")
        ImageDraw.Draw(im).text((20, size[1] // 2), label, fill="black")
        pages.append(im)
    pages[0].save(path, save_all=True, append_images=pages[1:])
