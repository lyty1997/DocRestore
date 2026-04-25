# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""IDE 元数据提取（AGE-8 Phase 2.2）

从 ``IDELayout.above_code`` 中提取每栏对应的 tab/breadcrumb 文本，正则
解析出**当前文件名 + 完整路径 + 语言 hint**，供 AGE-46 跨张归类。

关键观察（基于 spike 实测 above_code 内容）：
  - VSCode tab 行文字含文件扩展名（``.cc`` / ``.h`` / ``.gn`` 等），通常无 ``>``
  - breadcrumb 行用 `` > `` 分隔多段（如 ``media > gpu > openmax > foo.cc``），
    末段是当前文件，前段是路径
  - breadcrumb 后段可能跟 symbol path（``... > foo.cc > {} media > Allocate``），
    要找**最后一个含文件扩展名的段**为锚点
  - OCR 噪声：tab 含 `` C `` / ``C+`` / ``Cgl_`` 等图标误识前缀，需清洗
  - 双栏场景：每栏的 tab/breadcrumb 在 above_code 内按 x 范围分布

输入/输出：
  ``extract_ide_metas(layout) -> list[IDEMeta]``，与 ``layout.anchors`` 一一对应。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from docrestore.models import TextLine
from docrestore.processing.ide_layout import IDELayout

# 文件扩展名 → 语言 hint
EXT_TO_LANG: dict[str, str] = {
    "cc": "cpp", "cpp": "cpp", "cxx": "cpp", "c": "c",
    "h": "cpp", "hpp": "cpp", "hh": "cpp",
    "py": "python",
    "gn": "gn", "gni": "gn",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "rb": "ruby",
    "sh": "shell", "bash": "shell", "zsh": "shell",
    "yaml": "yaml", "yml": "yaml",
    "json": "json", "jsonc": "json",
    "toml": "toml",
    "xml": "xml",
    "html": "html", "htm": "html",
    "css": "css", "scss": "scss", "sass": "scss",
    "md": "markdown", "markdown": "markdown",
    "proto": "protobuf",
    "swift": "swift",
    "dart": "dart",
    "lua": "lua",
}

_EXT_PATTERN = "|".join(re.escape(e) for e in EXT_TO_LANG)
#: 匹配 "文件名.ext"。允许文件名段含 `_` `-` `.`（如 ``av1_decoder.cc``、
#: ``my.config.yaml``）；扩展名严格在白名单内
FILENAME_RE = re.compile(
    rf"([\w][\w\-]*(?:\.[\w\-]+)*\.(?:{_EXT_PATTERN}))(?=[^\w]|$)",
    re.IGNORECASE,
)

#: breadcrumb 段分隔符（容忍前后空格 + 重复 `>`）
_BREADCRUMB_SPLIT_RE = re.compile(r"\s*>+\s*")

#: tab/breadcrumb 段头部图标 OCR 误识噪声前缀（需要清洗）
#: spike 实测有两种贴法：
#:   1. ``C openmax_status.h``（带空格）
#:   2. ``Cgles2_..._translator.h``（无空格紧贴）
#: 第 2 种容易误吞文件名首字符，需要 cross-validate"去前缀后是否更像有效文件名"
_ICON_PREFIX_WITH_SPACE_RE = re.compile(
    r"^[\W_]*(?:C\+?|G\+?|H|J|S|T|TS|JS)\s+",
)

#: 紧贴 icon 前缀候选（C/G/H/Cgl 等，后面紧跟 lowercase 字母+下划线/数字）
#: 仅在 cross-validate 通过时清洗
_ATTACHED_ICON_RE = re.compile(
    r"^(C\+?|G\+?|Cgl|H|J|S|T|TS|JS)([a-z][a-z0-9_])",
)


@dataclass
class IDEMeta:
    """单个编辑器栏的元数据"""

    column_index: int
    filename: str | None = None     # 如 "openmax_status.h"
    path: str | None = None         # 如 "media/gpu/openmax/openmax_status.h"
    language: str | None = None
    tab_readable: bool = False      # 是否找到 tab 文件名
    breadcrumb_readable: bool = False
    raw_tab_lines: list[str] = field(default_factory=list)
    raw_breadcrumb_lines: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def extract_ide_metas(layout: IDELayout) -> list[IDEMeta]:
    """对 layout 的每个 anchor 提取一份 IDEMeta。

    没有 anchor 返回空列表。
    """
    if not layout.anchors:
        return []

    anchors = layout.anchors
    above = layout.above_code
    metas: list[IDEMeta] = []
    for i, anchor in enumerate(anchors):
        col_left = anchor.x1_min
        col_right = (
            anchors[i + 1].x1_min - 1
            if i + 1 < len(anchors)
            else max(
                (ln.bbox[2] for ln in above), default=col_left + 10000,
            )
        )
        col_lines = [
            ln for ln in above
            if col_left <= ((ln.bbox[0] + ln.bbox[2]) // 2) <= col_right
        ]
        metas.append(_extract_for_column(i, col_lines))
    return metas


def _extract_for_column(idx: int, lines: list[TextLine]) -> IDEMeta:
    """从一栏的 above_code line 中解析 tab/breadcrumb"""
    breadcrumb_lines, tab_lines = _split_lines_by_kind(lines)

    # 1. 优先从 breadcrumb 拿 (path, filename)
    path, filename = _first_breadcrumb_meta(breadcrumb_lines)

    # 2. tab fallback：补全或纠正 filename + path
    tab_filename = _pick_best_tab_filename(tab_lines)
    filename, path = _reconcile_with_tab(filename, path, tab_filename)

    # 3. 语言 hint
    language = _filename_to_language(filename) if filename else None

    # 4. quality flags
    flags: list[str] = []
    if not filename:
        flags.append("code.tab_unreadable")
    if not breadcrumb_lines:
        flags.append("code.breadcrumb_missing")

    return IDEMeta(
        column_index=idx,
        filename=filename,
        path=path,
        language=language,
        tab_readable=bool(filename),
        breadcrumb_readable=bool(breadcrumb_lines),
        raw_tab_lines=[ln.text for ln in tab_lines],
        raw_breadcrumb_lines=[ln.text for ln in breadcrumb_lines],
        flags=flags,
    )


def _split_lines_by_kind(
    lines: list[TextLine],
) -> tuple[list[TextLine], list[TextLine]]:
    """把 lines 拆成 (breadcrumb, tab) 两组"""
    breadcrumb: list[TextLine] = []
    tab: list[TextLine] = []
    for ln in lines:
        text = ln.text.strip()
        if not text:
            continue
        if text.count(">") >= 2:
            breadcrumb.append(ln)
        elif FILENAME_RE.search(text):
            tab.append(ln)
    return breadcrumb, tab


def _first_breadcrumb_meta(
    breadcrumb_lines: list[TextLine],
) -> tuple[str | None, str | None]:
    """从多条 breadcrumb 取第一条解析成功的 (path, filename)"""
    for bc in breadcrumb_lines:
        path, filename = _parse_breadcrumb(bc.text)
        if filename:
            return path, filename
    return None, None


def _reconcile_with_tab(
    filename: str | None,
    path: str | None,
    tab_filename: str | None,
) -> tuple[str | None, str | None]:
    """tab 与 breadcrumb 的 filename 对齐：
    - breadcrumb 缺失 → 用 tab.filename
    - breadcrumb 末段被截（开头匹配但缺扩展名）→ 用 tab 替换并修 path
    """
    if not filename and tab_filename:
        return tab_filename, path
    if (
        filename
        and tab_filename
        and tab_filename != filename
        and (
            filename in tab_filename
            or tab_filename.startswith(filename.rsplit(".", 1)[0])
        )
    ):
        if path and "/" in path:
            path = f"{path.rsplit('/', 1)[0]}/{tab_filename}"
        elif path:
            path = tab_filename
        filename = tab_filename
    return filename, path


def _filename_to_language(filename: str) -> str | None:
    """文件后缀 → 语言 hint"""
    ext = filename.rsplit(".", 1)[-1].lower()
    return EXT_TO_LANG.get(ext)


def _parse_breadcrumb(text: str) -> tuple[str | None, str | None]:
    """从一行 breadcrumb 拆出 ``(path, filename)``。

    例：
      - ``media >gpu >openmax > C openmax_status.h`` →
        ``("media/gpu/openmax/openmax_status.h", "openmax_status.h")``
      - ``media>gpu>openmax>C+ foo.cc>{}media>Symbol`` →
        最后一个含扩展名的段 ``C+ foo.cc`` 是文件锚点，``{}media>Symbol``
        是 symbol path，丢弃 → ``("media/gpu/openmax/foo.cc", "foo.cc")``
      - 末段被截（``... > foo_video_decode_ac``）→ filename=None，
        让上层用 tab 兜底
    """
    parts = [p.strip() for p in _BREADCRUMB_SPLIT_RE.split(text) if p.strip()]
    if len(parts) < 2:
        return None, None

    # 找最后一个含文件扩展名的段
    file_idx = -1
    filename: str | None = None
    for i in range(len(parts) - 1, -1, -1):
        m = FILENAME_RE.search(parts[i])
        if m:
            file_idx = i
            filename = _strip_attached_icon(m.group(1))
            break
    if file_idx < 0 or filename is None:
        return None, None

    # 路径段 = file_idx 之前的所有段（清洗图标前缀） + filename
    path_segments: list[str] = []
    for seg in parts[:file_idx]:
        cleaned = _clean_segment(seg)
        if cleaned:
            path_segments.append(cleaned)
    path_segments.append(filename)
    return "/".join(path_segments), filename


def _clean_segment(seg: str) -> str:
    """去掉 VSCode 文件类型图标 OCR 误识的前缀（如 ``C+ ``、``Cgl ``）"""
    return _ICON_PREFIX_WITH_SPACE_RE.sub("", seg).strip()


def _strip_attached_icon(filename: str) -> str:
    """紧贴的 icon 前缀（``Cgles2_x.h``）若去前缀后仍是合法文件名 → 清洗"""
    m = _ATTACHED_ICON_RE.match(filename)
    if not m:
        return filename
    stripped = filename[len(m.group(1)):]
    # cross-validate：去前缀后仍含扩展名才接受清洗（避免误伤 `Cmake.cc` 等）
    if FILENAME_RE.match(stripped):
        return stripped
    return filename


def _pick_best_tab_filename(tab_lines: list[TextLine]) -> str | None:
    """从多个 tab line 中挑出最可信的 filename。

    启发式：取 y 最小（最上面的 tab bar 行）+ 第一个匹配文件扩展名的。
    """
    if not tab_lines:
        return None
    sorted_by_y = sorted(tab_lines, key=lambda ln: ln.bbox[1])
    for ln in sorted_by_y:
        m = FILENAME_RE.search(ln.text)
        if m:
            return _strip_attached_icon(m.group(1))
    return None
