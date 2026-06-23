# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Epic D · D5 pptx 导出（python-pptx 逐页自拼）测试。

- 切页 / 页内容抽取 / 图片解析：纯函数，不依赖 python-pptx。
- 缺 python-pptx fail-closed：模拟 ImportError → ``ExportToolUnavailable``。
- 真导出（需 python-pptx）：2 页 md → pptx，python-pptx 读回 slide 数=页数、
  **从输入派生**的标题文字、图片嵌入（``<img>`` 与 ``![]()`` 都解析）、公式 TeX 文本在。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

from docrestore.output.exporters.base import ExportToolUnavailable
from docrestore.output.exporters.pptx import (
    PptxExporter,
    _extract_page,
    _resolve_image,
    _split_pages,
)


def _pptx_ready() -> bool:
    return importlib.util.find_spec("pptx") is not None


pptx_required = pytest.mark.skipif(
    not _pptx_ready(), reason="python-pptx 不可用",
)

#: 从输入派生的关键内容（构造输入）
_TITLE_A = "幻灯片标题甲ALPHA"
_TITLE_B = "幻灯片标题乙BETA"
_INLINE_TEX = "$E=mc^2$"
_MARKDOWN = f"""# {_TITLE_A}

第一页正文，行内公式 {_INLINE_TEX}。

<img src="images/pic_0.png" alt="" />

---

# {_TITLE_B}

第二页正文。

![配图](images/pic_0.png)

$$ \\frac{{a}}{{b}} = \\sqrt{{c}} $$
"""


def _build_doc(doc_dir: Path) -> Path:
    (doc_dir / "document.md").write_text(_MARKDOWN, encoding="utf-8")
    images = doc_dir / "images"
    images.mkdir()
    Image.new("RGB", (160, 100), (200, 120, 60)).save(images / "pic_0.png")
    return doc_dir / "document.md"


class TestSplitAndExtract:
    """纯函数：切页 / 页内容抽取 / 图片解析（无依赖）。"""

    def test_split_by_horizontal_rule(self) -> None:
        pages = _split_pages("# A\n\nx\n\n---\n\n# B\n\ny")
        assert len(pages) == 2

    def test_split_fallback_by_heading(self) -> None:
        pages = _split_pages("# A标题\n\n正文a\n\n# B标题\n\n正文b")
        assert len(pages) == 2

    def test_split_single_page(self) -> None:
        assert _split_pages("只有一段正文，无分隔") == ["只有一段正文，无分隔"]

    def test_extract_title_body_images(self) -> None:
        title, body, images = _extract_page(
            '# T标题\n\n正文行ABC\n\n<img src="images/a.png">\n\n![](images/b.png)',
        )
        assert title == "T标题"
        assert "正文行ABC" in body
        assert images == ["images/a.png", "images/b.png"]

    def test_extract_no_title(self) -> None:
        title, body, _images = _extract_page("纯正文一\n\n纯正文二")
        assert title is None
        assert body == ["纯正文一", "纯正文二"]


class TestResolveImage:
    """图片引用解析：越界 / 远程 / 缺失 → None；存在 → 真实路径。"""

    def test_traversal_blocked(self, tmp_path: Path) -> None:
        assert _resolve_image(tmp_path, "../secret.png") is None

    def test_remote_skipped(self, tmp_path: Path) -> None:
        assert _resolve_image(tmp_path, "http://x/y.png") is None

    def test_missing_skipped(self, tmp_path: Path) -> None:
        assert _resolve_image(tmp_path, "images/none.png") is None

    def test_existing_resolved(self, tmp_path: Path) -> None:
        (tmp_path / "images").mkdir()
        target = tmp_path / "images" / "pic.png"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(target)
        assert _resolve_image(tmp_path, "images/pic.png") == target


class TestPptxFailClosed:
    """缺 python-pptx → ensure_available fail-closed。"""

    def test_missing_pptx_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(sys.modules, "pptx", None)
        with pytest.raises(ExportToolUnavailable):
            PptxExporter().ensure_available()


@pptx_required
class TestPptxExport:
    """真导出（需 python-pptx）：slide 数=页数 + 派生标题 + 图片 + 公式文本。"""

    def test_roundtrip_two_slides(self, tmp_path: Path) -> None:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        doc_md = _build_doc(tmp_path)
        out = tmp_path / ".exports" / "out.pptx"

        PptxExporter().export(doc_md, tmp_path / "images", out)

        prs = Presentation(str(out))
        slides = list(prs.slides)
        assert len(slides) == 2  # 每页一 slide

        all_text = ""
        pictures = 0
        for slide in slides:
            for shape in slide.shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    pictures += 1
                elif shape.has_text_frame:
                    all_text += shape.text_frame.text
        assert _TITLE_A in all_text  # 派生：第一页标题
        assert _TITLE_B in all_text  # 派生：第二页标题
        assert _INLINE_TEX in all_text  # 公式以 TeX 文本保留
        assert "\\frac" in all_text  # 独立公式 TeX 文本保留
        assert pictures >= 2  # <img> 与 ![]() 两张图都嵌入
