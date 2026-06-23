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

"""docx 导出器（D2）：``document.md`` → Word，走 pandoc **两遍转换**。

**为何两遍**（2026-06-23 修复）：``document.md`` 的表格**一律是 HTML ``<table>``**、
配图常含 HTML ``<img>``。pandoc 的 markdown(gfm) 读取器把这些原始 HTML 当
``RawBlock html`` 保留，而 **docx 写出器直接丢弃原始 HTML** —— 单遍
``gfm → docx`` 会让表格与 HTML 图片全部消失（仅 ``![]()`` 图片侥幸保留）。

故改走 HTML 中转（与 PDF 链路同源，HTML 是「能吃原始 HTML」的通用中间态）：

1. ``pandoc <md> -f gfm+tex_math_dollars -t html5 --mathml``：markdown 构造转 HTML，
   原始 ``<table>/<img>`` 内联进同一份 HTML，``$...$`` 数学转 **MathML**。
2. ``pandoc <html> -f html -t docx``：HTML 读取器把 ``<table>/<img>`` 解析成
   pandoc 原生表格 / 图片 → docx 写出器正常渲染；MathML ``<math>`` → OMML（Word 公式）。

``--mathml`` 而非 ``--mathjax`` 是关键：``--mathjax`` 产 ``\\(..\\)``，HTML 读取器
不会再解析回数学（OMML 丢失、留下字面 ``\\(..\\)``）；``--mathml`` 产 ``<math>``，
HTML 读取器原生识别 → OMML。中间 HTML 落临时目录，``--resource-path=<doc_dir>``
保证两遍都能定位 ``images/`` 相对引用。

pandoc 是外部二进制（~150MB）：缺失 fail-closed（:class:`ExportToolUnavailable`）。
详见 ``docs/zh/export-mode.md`` §6。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from docrestore.output.exporters.base import (
    ExportToolUnavailable,
    resolve_tool,
    run_export_command,
)

#: 第一遍 pandoc 输入格式：GFM + 美元号 TeX 数学
_PANDOC_FROM = "gfm+tex_math_dollars"
#: 中间 HTML 文件名（落临时目录，两遍转换之间传递）
_INTERMEDIATE_HTML = "intermediate.html"


class DocxExporter:
    """``document.md`` → docx（pandoc）。"""

    suffix = "docx"
    tool = "pandoc"

    def ensure_available(self) -> None:
        """pandoc 不在 PATH → fail-closed。"""
        if resolve_tool(self.tool) is None:
            raise ExportToolUnavailable(self.tool)

    def export(
        self,
        doc_md: Path,
        assets_dir: Path,  # noqa: ARG002 — 图片走 document.md 相对引用 + resource-path
        out_path: Path,
    ) -> None:
        """两遍 pandoc：``doc_md`` →（HTML 中转）→ ``out_path``（docx）。"""
        pandoc = resolve_tool(self.tool)
        if pandoc is None:
            raise ExportToolUnavailable(self.tool)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc_dir = doc_md.parent
        with tempfile.TemporaryDirectory(prefix="docx-export-") as tmp:
            html_path = Path(tmp) / _INTERMEDIATE_HTML
            # 第一遍：markdown → HTML5（原始 <table>/<img> 内联、$..$ → MathML）
            run_export_command(
                [
                    pandoc, str(doc_md),
                    "-f", _PANDOC_FROM,
                    "-t", "html5", "--mathml",
                    "-o", str(html_path),
                    "--resource-path", str(doc_dir),
                ],
                cwd=doc_dir, tool=self.tool, fmt=self.suffix,
            )
            # 第二遍：HTML → docx（<table>/<img> 转原生、MathML → OMML）
            run_export_command(
                [
                    pandoc, str(html_path),
                    "-f", "html",
                    "-o", str(out_path),
                    "--resource-path", str(doc_dir),
                ],
                cwd=doc_dir, tool=self.tool, fmt=self.suffix,
            )
