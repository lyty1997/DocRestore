# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Epic D · D2 docx 导出（pandoc）测试。

- 缺 pandoc → ``ensure_available`` fail-closed（不依赖外部工具，恒可跑）。
- 真转换（需 pandoc + python-docx，缺则 skip）：document.md → docx round-trip，
  断言含**从输入派生**的标题 / 表格单元格（CLAUDE.md：不写死数据集关键词），
  公式转 OMML、图片嵌入。
"""

from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest

from docrestore.output.exporters.base import ExportToolUnavailable
from docrestore.output.exporters.docx import DocxExporter

pandoc_required = pytest.mark.skipif(
    shutil.which("pandoc") is None, reason="pandoc 未安装（导出外部依赖）",
)

#: 从输入派生的关键内容（断言据此，不写死数据集关键词）
_HEADING = "季度营收汇总报告"
_CELL = "收入明细条目甲"
# 关键：用 **HTML <table>** + **HTML <img>**（真实 document.md 的格式）。
# 早期单遍 ``gfm → docx`` 会丢弃原始 HTML（表格 / <img> 消失），用 GFM 管道表
# 测不出该回归；这里据实构造以锁住「两遍 HTML 中转」修复。
_MARKDOWN = f"""# {_HEADING}

正文一段，含行内公式 $E=mc^2$。

<table border="1"><tr><td>项目</td><td>金额</td></tr>\
<tr><td>{_CELL}</td><td>100</td></tr></table>

![配图](images/p1.jpg)

<img src="images/p1.jpg" alt="HTML 图" />
"""

#: 最小有效 1x1 PNG（pandoc 嵌图需可读图）
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
)


def _build_doc(doc_dir: Path) -> Path:
    """构造最小 document.md + images/，返回 document.md 路径。"""
    (doc_dir / "document.md").write_text(_MARKDOWN, encoding="utf-8")
    images = doc_dir / "images"
    images.mkdir()
    (images / "p1.jpg").write_bytes(_PNG_1X1)
    return doc_dir / "document.md"


class TestDocxEnsureAvailable:
    """缺 pandoc → fail-closed（不依赖真实工具，恒可跑）。"""

    def test_missing_pandoc_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "docrestore.output.exporters.docx.resolve_tool", lambda _t: None,
        )
        with pytest.raises(ExportToolUnavailable):
            DocxExporter().ensure_available()


@pandoc_required
class TestDocxExport:
    """真转换（需 pandoc）：round-trip 派生关键内容。"""

    def test_roundtrip_derived_content(self, tmp_path: Path) -> None:
        docx = pytest.importorskip("docx")
        doc_md = _build_doc(tmp_path)
        out = tmp_path / ".exports" / "out.docx"

        DocxExporter().export(doc_md, tmp_path / "images", out)

        assert out.stat().st_size > 0
        document = docx.Document(str(out))
        text = "\n".join(p.text for p in document.paragraphs)
        # 派生标题落在正文
        assert _HEADING in text
        # 派生单元格落在表格
        cells = [
            c.text for tb in document.tables for r in tb.rows for c in r.cells
        ]
        assert any(_CELL in c for c in cells)
        # 公式转 OMML（Word 数学）
        assert "oMath" in document.element.xml
        # md ``![]()`` 与 HTML ``<img>`` 两张图都嵌入（锁住 HTML 图片不再被丢）
        assert len(document.inline_shapes) >= 2
