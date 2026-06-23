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

"""KaTeX 服务端预渲染（D3 PDF 公式）。

weasyprint 不跑 JS、其 MathML 支持不足（公式塌成行内文本），故在 pandoc(--mathjax)
md→HTML 后，把保留下来的 ``\\(..\\)`` / ``\\[..\\]`` TeX 用 **KaTeX（Node）** 预渲染成
纯 CSS HTML，weasyprint 再排版。KaTeX 是 JS-only（无 Python 移植），需 Node 运行时 +
katex 包（dev 复用 ``frontend/node_modules/katex``，生产部署见 deployment）。

详见 ``docs/zh/export-mode.md`` §7。
"""

from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess
from pathlib import Path

from docrestore.output.exporters.base import ExportFailed, ExportToolUnavailable

#: repo 根：exporters → output → docrestore → backend → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]
_NODE_MODULES = _REPO_ROOT / "frontend" / "node_modules"
_KATEX_PKG = _NODE_MODULES / "katex"
_KATEX_CSS = _KATEX_PKG / "dist" / "katex.min.css"
_KATEX_SCRIPT = Path(__file__).resolve().parent / "_katex_render.cjs"

#: pandoc --mathjax 输出：<span class="math inline">\(TeX\)</span> / display \[TeX\]
_MATH_SPAN_RE = re.compile(
    r'<span class="math (?:inline|display)">(.*?)</span>', re.DOTALL,
)

#: KaTeX 渲染超时（一篇文档的全部公式一次性渲染）
_KATEX_TIMEOUT_S = 60


def katex_css_path() -> Path:
    """KaTeX 样式表路径（weasyprint 以其所在目录解析 @font-face 字体）。"""
    return _KATEX_CSS


def has_math(html_doc: str) -> bool:
    """HTML 是否含 pandoc 数学 span（决定是否需要 KaTeX/Node）。"""
    return _MATH_SPAN_RE.search(html_doc) is not None


def ensure_katex() -> None:
    """KaTeX 依赖（Node + katex 包 + 脚本 + CSS）任一缺失 → fail-closed。"""
    if shutil.which("node") is None:
        raise ExportToolUnavailable("node")
    if not _KATEX_PKG.is_dir() or not _KATEX_CSS.is_file():
        raise ExportToolUnavailable("katex")
    if not _KATEX_SCRIPT.is_file():
        raise ExportToolUnavailable("katex")


def _strip_delims(raw: str) -> tuple[str, bool]:
    """剥 ``\\(..\\)`` / ``\\[..\\]`` 定界，返回 (tex, display)。"""
    s = raw.strip()
    if s.startswith("\\[") and s.endswith("\\]"):
        return s[2:-2], True
    if s.startswith("\\(") and s.endswith("\\)"):
        return s[2:-2], False
    return s, False


def _run_katex(items: list[dict[str, str | bool]]) -> list[str | None]:
    """把 [{tex, display}] 交给 Node KaTeX 渲染，返回等长 HTML 列表（失败为 None）。"""
    node = shutil.which("node")
    if node is None:
        raise ExportToolUnavailable("node")
    try:
        proc = subprocess.run(  # noqa: S603 — node + 固定脚本/参数，无 shell
            [node, str(_KATEX_SCRIPT), str(_KATEX_PKG)],
            input=json.dumps(items),
            capture_output=True,
            text=True,
            timeout=_KATEX_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ExportToolUnavailable("node") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExportFailed("katex", "pdf", "KaTeX 渲染超时") from exc
    if proc.returncode != 0:
        raise ExportFailed("katex", "pdf", (proc.stderr or "")[:500])
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ExportFailed("katex", "pdf", "KaTeX 输出解析失败") from exc
    if not isinstance(data, list):
        raise ExportFailed("katex", "pdf", "KaTeX 输出格式异常")
    return [x if isinstance(x, str) else None for x in data]


def prerender_math(html_doc: str) -> str:
    """把 HTML 中 pandoc 的数学 span 用 KaTeX 渲染替换；无公式则原样返回。

    单条公式渲染失败（KaTeX 返回 None）时保留原 span（不致整篇导出失败）。
    """
    matches = list(_MATH_SPAN_RE.finditer(html_doc))
    if not matches:
        return html_doc

    items: list[dict[str, str | bool]] = []
    for m in matches:
        tex, display = _strip_delims(_html.unescape(m.group(1)))
        items.append({"tex": tex, "display": display})

    rendered = _run_katex(items)

    parts: list[str] = []
    cursor = 0
    for m, html_out in zip(matches, rendered, strict=True):
        parts.append(html_doc[cursor : m.start()])
        parts.append(html_out if html_out is not None else m.group(0))
        cursor = m.end()
    parts.append(html_doc[cursor:])
    return "".join(parts)
