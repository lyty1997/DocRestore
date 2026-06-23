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

"""PDF 导出器（D3）：``document.md`` → PDF。

链路 ``md → HTML(pandoc -t html5 --mathml) → PDF(weasyprint)``：

- pandoc 把 ``$...$`` 转 **MathML**、保留 OCR 产出的 HTML ``<table>``（html5 透传）。
- weasyprint 把 HTML(+MathML+CSS) 排版成 PDF，对内联 HTML 表格友好、免 TeX 依赖。
- 图片相对引用 ``images/{stem}_N.jpg`` 由 weasyprint 的 ``base_url`` 解析。

依赖 fail-closed：pandoc 缺失 / weasyprint 不可导入（缺 cairo/pango 系统库）→
:class:`ExportToolUnavailable`。weasyprint **惰性导入**：注册表启动时导入本模块，
顶层 import 会让缺该依赖的部署整体起不来。公式机制取舍见 ``docs/zh/export-mode.md`` §7。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from docrestore.output.exporters.base import (
    ExportFailed,
    ExportToolUnavailable,
    resolve_tool,
    run_export_command,
)
from docrestore.output.exporters.mathrender import (
    ensure_katex,
    has_math,
    katex_css_path,
    prerender_math,
)

#: pandoc 输入格式：GFM + 美元号 TeX 数学
_PANDOC_FROM = "gfm+tex_math_dollars"


def _ensure_weasyprint() -> None:
    """惰性探测 weasyprint 可导入；不可用（缺包 / 缺系统库）→ fail-closed。"""
    try:
        import weasyprint  # noqa: F401, PLC0415 — 惰性导入仅做可用性探测
    except (ImportError, OSError) as exc:
        raise ExportToolUnavailable("weasyprint") from exc


def _render_html_to_pdf(
    html_text: str, base_url: str, out_path: Path, css_path: Path | None,
) -> None:
    """weasyprint 把 HTML 字符串渲染成 PDF；渲染异常统一 fail-closed。

    ``css_path`` 非空时挂为附加样式表（公式文档挂 KaTeX CSS，weasyprint 以其所在
    目录解析 @font-face 字体）。
    """
    try:
        import weasyprint  # noqa: PLC0415 — 惰性导入，避免缺依赖时整体起不来
    except (ImportError, OSError) as exc:
        raise ExportToolUnavailable("weasyprint") from exc
    stylesheets = (
        [weasyprint.CSS(filename=str(css_path))] if css_path is not None else []
    )
    try:
        weasyprint.HTML(string=html_text, base_url=base_url).write_pdf(
            str(out_path), stylesheets=stylesheets,
        )
    except Exception as exc:  # noqa: BLE001 — weasyprint 渲染异常统一 fail-closed
        raise ExportFailed("weasyprint", "pdf", str(exc)[:500]) from exc


class PdfExporter:
    """``document.md`` → PDF（pandoc HTML + weasyprint）。"""

    suffix = "pdf"
    tool = "weasyprint"

    def ensure_available(self) -> None:
        """pandoc 与 weasyprint 任一缺失 → fail-closed。"""
        if resolve_tool("pandoc") is None:
            raise ExportToolUnavailable("pandoc")
        _ensure_weasyprint()

    def export(
        self,
        doc_md: Path,
        assets_dir: Path,  # noqa: ARG002 — 图片走 base_url 解析相对引用
        out_path: Path,
    ) -> None:
        """md → HTML(pandoc) → PDF(weasyprint)。"""
        pandoc = resolve_tool("pandoc")
        if pandoc is None:
            raise ExportToolUnavailable("pandoc")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc_dir = doc_md.parent

        # 中间 HTML 落系统临时目录（不污染 doc_dir / 不进 zip）；图片由 base_url 解析。
        with tempfile.NamedTemporaryFile(
            "w", suffix=".html", delete=False, encoding="utf-8",
        ) as tmp:
            html_path = Path(tmp.name)
        try:
            # pandoc --mathjax 保留 \(..\)/\[..\] 原始 TeX，供 KaTeX 预渲染。
            cmd = [
                pandoc, str(doc_md),
                "-f", _PANDOC_FROM,
                "-t", "html5",
                "--mathjax",
                "--standalone",
                "-o", str(html_path),
            ]
            run_export_command(cmd, cwd=doc_dir, tool="pandoc", fmt=self.suffix)

            html_text = html_path.read_text(encoding="utf-8")
            css_path: Path | None = None
            # 仅含公式的文档才需要 KaTeX/Node（无公式的纯文/表格文档不需）。
            if has_math(html_text):
                ensure_katex()
                html_text = prerender_math(html_text)
                css_path = katex_css_path()
            _render_html_to_pdf(html_text, f"{doc_dir}/", out_path, css_path)
        finally:
            html_path.unlink(missing_ok=True)
