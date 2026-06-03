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

"""ppt_renderer（S4 单页组装 + 多页合并）单测

合成 PageOCR 做确定性断言；断言从构造输入派生（页内关键短语 / stem / 文件名），
不写死数据集标识符。OCR 目录 {stem}_OCR 建在 output_dir 下（与生产一致：
engine.ocr 把每页产物写到 output_dir/{stem}_OCR/）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docrestore.models import PageOCR
from docrestore.output.ppt_renderer import render_ppt_document


def _make_page(
    output_dir: Path, stem: str, raw_text: str, n_images: int = 1,
) -> PageOCR:
    """构造一页 PageOCR，并在 output_dir/{stem}_OCR/images 下落地假图。"""
    ocr_dir = output_dir / f"{stem}_OCR"
    (ocr_dir / "images").mkdir(parents=True, exist_ok=True)
    for i in range(n_images):
        (ocr_dir / "images" / f"{i}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return PageOCR(
        image_path=output_dir / f"{stem}.jpg",
        image_size=(800, 600),
        raw_text=raw_text,
        output_dir=ocr_dir,
    )


async def test_render_preserves_page_order(tmp_path: Path) -> None:
    """多页按输入顺序合并，页内关键短语顺序与输入一致。"""
    out = tmp_path / "out"
    pages = [
        _make_page(out, "pageA", "甲页独有正文ALPHA", n_images=0),
        _make_page(out, "pageB", "乙页独有正文BETA", n_images=0),
    ]
    _, memory_md = await render_ppt_document(pages, out)
    assert "甲页独有正文ALPHA" in memory_md
    assert "乙页独有正文BETA" in memory_md
    assert memory_md.index("ALPHA") < memory_md.index("BETA")


async def test_markers_in_memory_stripped_on_disk(tmp_path: Path) -> None:
    """page marker 在内存版保留、磁盘 document.md 中被剥除。"""
    out = tmp_path / "out"
    pages = [_make_page(out, "pageA", "正文", n_images=0)]
    doc_path, memory_md = await render_ppt_document(pages, out)
    assert "<!-- page: pageA.jpg -->" in memory_md
    disk_md = doc_path.read_text(encoding="utf-8")
    assert "<!-- page:" not in disk_md


async def test_html_img_rewritten_and_copied(tmp_path: Path) -> None:
    """VL 的 HTML img（images/0.jpg）→ images/{stem}_0.jpg 并复制裁图。"""
    out = tmp_path / "out"
    raw = '正文\n<img src="images/0.jpg" alt="Image" width="50%" />'
    pages = [_make_page(out, "pageA", raw, n_images=1)]
    doc_path, _ = await render_ppt_document(pages, out)
    disk_md = doc_path.read_text(encoding="utf-8")
    assert 'src="images/pageA_0.jpg"' in disk_md
    assert (out / "images" / "pageA_0.jpg").exists()


async def test_no_cross_page_dedup(tmp_path: Path) -> None:
    """两页含相同正文：均保留，不被跨页去重删除。"""
    out = tmp_path / "out"
    shared = "两页都出现的相同版式标题ZZZ"
    pages = [
        _make_page(out, "pageA", shared, n_images=0),
        _make_page(out, "pageB", shared, n_images=0),
    ]
    _, memory_md = await render_ppt_document(pages, out)
    assert memory_md.count("两页都出现的相同版式标题ZZZ") == 2


async def test_pages_separated(tmp_path: Path) -> None:
    """多页之间有分隔线。"""
    out = tmp_path / "out"
    pages = [
        _make_page(out, "pageA", "甲", n_images=0),
        _make_page(out, "pageB", "乙", n_images=0),
    ]
    _, memory_md = await render_ppt_document(pages, out)
    assert "---" in memory_md


async def test_bodies_override_used_verbatim(tmp_path: Path) -> None:
    """提供 bodies（按页预精修正文）时按页使用，不再内部 rewrite 原始正文。"""
    out = tmp_path / "out"
    pages = [
        _make_page(out, "pageA", "原始甲", n_images=0),
        _make_page(out, "pageB", "原始乙", n_images=0),
    ]
    bodies = ["精修甲BODYA", "精修乙BODYB"]
    _, memory_md = await render_ppt_document(pages, out, bodies=bodies)
    assert "精修甲BODYA" in memory_md
    assert "精修乙BODYB" in memory_md
    assert "原始甲" not in memory_md  # 原始正文被 bodies 覆盖
    assert memory_md.index("BODYA") < memory_md.index("BODYB")  # 保序
    # marker 仍取自 page（文件名），与 body 来源无关
    assert "<!-- page: pageA.jpg -->" in memory_md


async def test_bodies_length_mismatch_raises(tmp_path: Path) -> None:
    """bodies 与 pages 长度不一致直接报错（防错位拼装）。"""
    out = tmp_path / "out"
    pages = [_make_page(out, "pageA", "正文", n_images=0)]
    with pytest.raises(ValueError, match="长度"):
        await render_ppt_document(pages, out, bodies=["a", "b"])
