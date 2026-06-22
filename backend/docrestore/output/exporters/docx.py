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

"""docx 导出器（D2）：``document.md`` → Word，走 pandoc。

链路 ``pandoc <md> -f gfm+tex_math_dollars -o <docx> --resource-path=<doc_dir>``：

- ``gfm`` 按 GitHub-Flavored Markdown 解析，吃 OCR 产出的 HTML ``<table>``。
- ``tex_math_dollars`` 把 ``$...$`` 识别为 TeX 数学 → pandoc 原生转 OMML（Word 公式）。
- ``--resource-path`` 让 ``images/{stem}_N.jpg`` 相对引用能被解析嵌入。

pandoc 是外部二进制（~150MB）：缺失 fail-closed（:class:`ExportToolUnavailable`）。
详见 ``docs/zh/export-mode.md`` §6。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.output.exporters.base import (
    ExportToolUnavailable,
    resolve_tool,
    run_export_command,
)

#: pandoc 输入格式：GFM + 美元号 TeX 数学
_PANDOC_FROM = "gfm+tex_math_dollars"


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
        """pandoc 转换 ``doc_md`` → ``out_path``（docx）。"""
        pandoc = resolve_tool(self.tool)
        if pandoc is None:
            raise ExportToolUnavailable(self.tool)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc_dir = doc_md.parent
        cmd = [
            pandoc,
            str(doc_md),
            "-f",
            _PANDOC_FROM,
            "-o",
            str(out_path),
            "--resource-path",
            str(doc_dir),
        ]
        run_export_command(cmd, cwd=doc_dir, tool=self.tool, fmt=self.suffix)
