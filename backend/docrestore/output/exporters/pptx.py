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

"""pptx 导出器（D5）：``document.md`` → PowerPoint，走 python-pptx 逐页自拼。

**为何不用 ``pandoc -t pptx``**（4 轮 spike 实证，2026-06-23）：pandoc 的
pptx 写法**无法让文字与图片同处一张 slide**（块级图片必单独成页、内联图片被丢）。
屏摄 PPT 页页有图，纯 pandoc 只能「图各自成页（膨胀）」或「丢图」，
均不满足 `#82`「每页一 slide、图文在一起」。故改用 python-pptx 自拼页。

链路：按 ``\\n\\n---\\n\\n``（PPT 模式页分隔）切页（doc 模式无 ``---`` 时
回退按顶层 ``#`` 切）→ 每页一 slide：首个标题→标题框、其余正文→文本框
（``$..$`` 公式留 TeX 文本、不渲染——lite 取舍）、图片（``![]()`` 与
``<img>`` 都解析）→ PIL 读尺寸缩放、线性排版。

python-pptx 是纯 Python 依赖：**惰性导入** fail-closed
（:class:`ExportToolUnavailable`）——注册表启动导入本模块，顶层不 import pptx。
详见 ``docs/zh/export-mode.md`` §9.2。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any  # python-pptx 运行期对象无类型存根，边界处标 Any

from docrestore.output.exporters.base import (
    ExportFailed,
    ExportToolUnavailable,
)

#: 水平线行（页分隔）：``---`` / ``***`` / ``___`` 三连及以上
_HR_SPLIT = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")
#: 顶层 H1（doc 模式无 ``---`` 时的回退切页锚）
_H1 = re.compile(r"^#\s+")
#: ATX 标题（任意级）
_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
#: markdown 图片 ``![alt](src)``
_IMG_MD = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
#: HTML 图片 ``<img src="...">``
_IMG_HTML = re.compile(r"""<img\b[^>]*\bsrc\s*=\s*["']([^"']+)["'][^>]*>""", re.I)

# ── 版式常量（EMU；1 inch = 914400 EMU；16:9 = 13.333in × 7.5in）──────────
_SLIDE_W = 12192000
_SLIDE_H = 6858000
_MARGIN = 365760       # 0.4in
_TITLE_TOP = 274320    # 0.3in
_TITLE_H = 822960      # 0.9in
_CONTENT_TOP = 1188720  # 1.3in
_GAP = 182880          # 0.2in
_TITLE_PT = 28
_BODY_PT = 14
#: 有图时正文占内容区宽度比例（其余留给图片栏）
_TEXT_FRACTION = 0.55


def _split_pages(markdown: str) -> list[str]:
    """切页：优先按水平线分隔；无分隔则回退按顶层 H1；都没有则整篇一页。"""
    pages = [p.strip() for p in _HR_SPLIT.split(markdown) if p.strip()]
    if len(pages) > 1:
        return pages
    return _split_by_headings(markdown)


def _split_by_headings(markdown: str) -> list[str]:
    """回退切页：每个顶层 ``#`` 起一页；首个标题前的引言并入第一页。"""
    lines = markdown.splitlines()
    idxs = [i for i, line in enumerate(lines) if _H1.match(line)]
    if len(idxs) <= 1:
        whole = markdown.strip()
        return [whole] if whole else []
    pages: list[str] = []
    for k, start in enumerate(idxs):
        end = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
        pages.append("\n".join(lines[start:end]).strip())
    if idxs[0] > 0:
        preamble = "\n".join(lines[: idxs[0]]).strip()
        if preamble:
            pages[0] = f"{preamble}\n\n{pages[0]}"
    return [p for p in pages if p]


def _extract_page(page: str) -> tuple[str | None, list[str], list[str]]:
    """拆一页 → ``(标题, 正文行, 图片 src 列表)``。

    首个 ATX 标题升为 slide 标题，其余标题降为正文文本行；图片从文本剥离单独收集；
    ``$..$`` 公式作为普通文本保留（lite 不渲染）。
    """
    title: str | None = None
    body: list[str] = []
    images: list[str] = []
    for raw_line in page.splitlines():
        line = raw_line.rstrip()
        images.extend(m.group(1) for m in _IMG_MD.finditer(line))
        images.extend(m.group(1) for m in _IMG_HTML.finditer(line))
        text = _IMG_HTML.sub("", _IMG_MD.sub("", line)).strip()
        heading = _ATX.match(text)
        if heading is not None:
            if title is None:
                title = heading.group(2).strip()
            else:
                body.append(heading.group(2).strip())
            continue
        if text:
            body.append(text)
    return title, body, images


def _resolve_image(doc_dir: Path, src: str) -> Path | None:
    """把图片相对引用解析为 doc_dir 内的真实文件；越界 / 远程 / 缺失 → ``None``。"""
    if "://" in src or src.startswith("data:"):
        return None
    base = doc_dir.resolve()
    candidate = (base / src).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _add_title(slide: Any, title: str) -> None:
    """顶部标题框（加粗大字）。"""
    from pptx.util import Emu, Pt  # noqa: PLC0415 — pptx 已加载，懒导入保持模块轻量

    box = slide.shapes.add_textbox(
        Emu(_MARGIN), Emu(_TITLE_TOP), Emu(_SLIDE_W - 2 * _MARGIN), Emu(_TITLE_H),
    )
    frame = box.text_frame
    frame.word_wrap = True
    run = frame.paragraphs[0].add_run()
    run.text = title
    run.font.size = Pt(_TITLE_PT)
    run.font.bold = True


def _add_body(slide: Any, body: list[str], text_w: int) -> None:
    """正文文本框（每行一段，``$..$`` 公式作为普通文本）。"""
    from pptx.util import Emu, Pt  # noqa: PLC0415

    height = _SLIDE_H - _CONTENT_TOP - _MARGIN
    box = slide.shapes.add_textbox(
        Emu(_MARGIN), Emu(_CONTENT_TOP), Emu(text_w), Emu(height),
    )
    frame = box.text_frame
    frame.word_wrap = True
    for i, line in enumerate(body):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        run = para.add_run()
        run.text = line
        run.font.size = Pt(_BODY_PT)


def _place_image(
    slide: Any, path: Path, left: int, top: int, max_w: int, max_h: int,
) -> None:
    """按 ``max_w × max_h`` 等比缩放后放置一张图片（读图失败则跳过）。"""
    from PIL import Image  # noqa: PLC0415 — Pillow 是硬依赖，懒导入保持模块轻量
    from pptx.util import Emu  # noqa: PLC0415

    try:
        with Image.open(path) as img:
            px_w, px_h = img.size
    except (OSError, ValueError):
        return
    if px_w <= 0 or px_h <= 0 or max_h <= 0:
        return
    scale = min(max_w / px_w, max_h / px_h)
    disp_w = max(1, int(px_w * scale))
    disp_h = max(1, int(px_h * scale))
    slide.shapes.add_picture(
        str(path), Emu(left), Emu(top), width=Emu(disp_w), height=Emu(disp_h),
    )


def _add_images(
    slide: Any, images: list[str], doc_dir: Path, left: int, width: int,
) -> None:
    """右栏竖向堆叠图片（越界 / 缺失的引用已被 :func:`_resolve_image` 过滤）。"""
    paths = [
        p for p in (_resolve_image(doc_dir, src) for src in images) if p is not None
    ]
    if not paths:
        return
    avail_h = _SLIDE_H - _CONTENT_TOP - _MARGIN
    cell_h = avail_h // len(paths)
    top = _CONTENT_TOP
    for path in paths:
        _place_image(slide, path, left, top, width, cell_h - _GAP)
        top += cell_h


def _render_slide(
    slide: Any,
    title: str | None,
    body: list[str],
    images: list[str],
    doc_dir: Path,
) -> None:
    """把一页内容画到一张 slide（有图则正文左栏、图片右栏；无图正文满宽）。"""
    has_image_col = bool(images)
    content_w = _SLIDE_W - 2 * _MARGIN
    text_w = int(content_w * _TEXT_FRACTION) if has_image_col else content_w
    if title:
        _add_title(slide, title)
    if body:
        _add_body(slide, body, text_w)
    if has_image_col:
        img_left = _MARGIN + text_w + _GAP
        img_w = _SLIDE_W - _MARGIN - img_left
        _add_images(slide, images, doc_dir, img_left, img_w)


class PptxExporter:
    """``document.md`` → pptx（python-pptx，逐页一 slide）。"""

    suffix = "pptx"
    tool = "python-pptx"

    def ensure_available(self) -> None:
        """python-pptx 不可导入 → fail-closed。"""
        try:
            import pptx  # noqa: F401, PLC0415 — 惰性导入仅做可用性探测
        except ImportError as exc:
            raise ExportToolUnavailable(self.tool) from exc

    def export(
        self,
        doc_md: Path,
        assets_dir: Path,  # noqa: ARG002 — 图片走 document.md 相对引用解析
        out_path: Path,
    ) -> None:
        """切页 → 每页一 slide → 保存 pptx。"""
        try:
            from pptx import Presentation  # noqa: PLC0415 — 惰性导入，缺依赖不阻塞启动
        except ImportError as exc:
            raise ExportToolUnavailable(self.tool) from exc

        markdown = doc_md.read_text(encoding="utf-8")
        pages = _split_pages(markdown)
        doc_dir = doc_md.parent
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            prs = Presentation()
            prs.slide_width = _SLIDE_W
            prs.slide_height = _SLIDE_H
            blank = prs.slide_layouts[6]  # 默认模板的 Blank 版式
            rendered = 0
            for page in pages:
                slide = prs.slides.add_slide(blank)
                title, body, images = _extract_page(page)
                _render_slide(slide, title, body, images, doc_dir)
                rendered += 1
            if rendered == 0:  # 空文档兜底：至少一张空 slide
                prs.slides.add_slide(blank)
            prs.save(str(out_path))
        except Exception as exc:  # noqa: BLE001 — python-pptx 异常统一 fail-closed
            raise ExportFailed(self.tool, self.suffix, str(exc)[:500]) from exc
