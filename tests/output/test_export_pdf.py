# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Epic D · D3 PDF 导出（weasyprint + KaTeX）测试。

- ``has_math`` / 缺依赖 fail-closed：不依赖外部工具，恒可跑。
- KaTeX 预渲染（需 node + katex）：公式 span → KaTeX HTML（含 katex 类、无原始 TeX）。
- 真导出（需 pandoc + weasyprint + node + katex）：document.md → PDF round-trip，
  断言含**从输入派生**的标题 / 表格单元格，且无原始 TeX 泄漏（证明公式已渲染）。
  公式视觉保真由开发期 spike 目视确认（见 docs/zh/export-mode.md §7）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from docrestore.output.exporters.base import ExportToolUnavailable
from docrestore.output.exporters.mathrender import (
    has_math,
    katex_css_path,
    prerender_math,
)
from docrestore.output.exporters.pdf import PdfExporter


def _node_katex_ready() -> bool:
    return shutil.which("node") is not None and katex_css_path().is_file()


def _pdf_stack_ready() -> bool:
    if shutil.which("pandoc") is None or not _node_katex_ready():
        return False
    try:
        import weasyprint  # noqa: F401, PLC0415
    except (ImportError, OSError):
        return False
    return True


node_katex_required = pytest.mark.skipif(
    not _node_katex_ready(), reason="node / katex 不可用",
)
pdf_stack_required = pytest.mark.skipif(
    not _pdf_stack_ready(), reason="pandoc / weasyprint / node / katex 不全",
)

#: 从输入派生的关键内容
_HEADING = "公式渲染验收报告"
_CELL = "收入明细条目乙"
_MARKDOWN = f"""# {_HEADING}

行内公式 $E=mc^2$，独立公式：

$$ \\frac{{a}}{{b}} = \\sqrt{{c+d}} $$

| 项目 | 金额 |
| --- | --- |
| {_CELL} | 100 |
"""


def _build_doc(doc_dir: Path) -> Path:
    (doc_dir / "document.md").write_text(_MARKDOWN, encoding="utf-8")
    (doc_dir / "images").mkdir()
    return doc_dir / "document.md"


class TestHasMath:
    """has_math：检测 pandoc 数学 span（纯函数，无依赖）。"""

    def test_detects_math_span(self) -> None:
        assert has_math('<p><span class="math inline">\\(x\\)</span></p>')

    def test_no_math(self) -> None:
        assert not has_math("<p>纯文本，无公式</p>")


class TestPdfFailClosed:
    """缺 pandoc → ensure_available fail-closed（不依赖真实工具）。"""

    def test_missing_pandoc_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "docrestore.output.exporters.pdf.resolve_tool", lambda _t: None,
        )
        with pytest.raises(ExportToolUnavailable):
            PdfExporter().ensure_available()


@node_katex_required
class TestKatexPrerender:
    """KaTeX 预渲染：公式 span → KaTeX HTML，无原始 TeX 残留。"""

    def test_renders_formula_span(self) -> None:
        html = '<p><span class="math inline">\\(E=mc^2\\)</span></p>'
        out = prerender_math(html)
        assert "katex" in out
        assert "\\(" not in out  # 原始 TeX 定界已被替换

    def test_no_math_passthrough(self) -> None:
        html = "<p>纯文本</p>"
        assert prerender_math(html) == html


@pdf_stack_required
class TestPdfExport:
    """真导出（需全栈）：round-trip 派生关键内容 + 无原始 TeX 泄漏。"""

    def test_roundtrip_derived_content(self, tmp_path: Path) -> None:
        pypdf = pytest.importorskip("pypdf")
        doc_md = _build_doc(tmp_path)
        out = tmp_path / ".exports" / "out.pdf"

        PdfExporter().export(doc_md, tmp_path / "images", out)

        assert out.stat().st_size > 0
        text = "\n".join(
            (p.extract_text() or "") for p in pypdf.PdfReader(str(out)).pages
        )
        assert _HEADING in text
        assert _CELL in text
        # KaTeX 已渲染 → 抽取文本里不应残留原始 TeX 命令
        assert "\\frac" not in text
        assert "\\sqrt" not in text
