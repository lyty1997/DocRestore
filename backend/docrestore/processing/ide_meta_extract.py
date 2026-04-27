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
#: ``my.config.yaml``）；扩展名严格在白名单内。
#: lookahead 额外允许 ``.cc4×`` 形式（VSCode tab 文件状态计数 + close
#: 按钮被 OCR 合并成无空格串）。
FILENAME_RE = re.compile(
    rf"([\w][\w\-]*(?:\.[\w\-]+)*\.(?:{_EXT_PATTERN}))"
    r"(?=\d*\s*[×x]|[^\w]|$)",
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

    最后做"同图栏间路径补全"：用其他栏的目录前缀补全本栏 path=None 或
    OCR 漏分隔符（如 ``gpuopenmax`` ↔ ``gpu/openmax``）的场景。

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

    _reconcile_within_image(metas)
    return metas


def _reconcile_within_image(metas: list[IDEMeta]) -> None:
    """同图栏间路径补全（in-place mutate）

    场景：
      1. 某栏 path=None 但 filename 有 → 借用其他栏的目录前缀拼路径
      2. 某栏 path 因 OCR 漏 ``>`` 把多段粘连（``media/gpuopenmax/...``），
         但其他栏 path 是 ``media/gpu/openmax/...`` → 用粘连版"去 /"后等价
         判定为相同目录，替换为细分版

    标 quality flag：
      - ``code.path_inferred_from_peer``（无 path 借用其他栏）
      - ``code.path_segments_recovered``（粘连段被还原）
    """
    if len(metas) < 2:
        return

    # 1. 收集所有有 path 的栏的目录前缀（去掉末段文件名）
    from collections import Counter
    dirs: list[str] = []
    for m in metas:
        if m.path and "/" in m.path:
            dirs.append(m.path.rsplit("/", 1)[0])
    if not dirs:
        return
    # 选众数；并列时偏好"段数更多"版本（更细分 = 更可信，OCR 漏分隔符
    # 只会让段变少，不会凭空多段。``media/gpu/openmax`` 比 ``media/gpuopenmax`` 可信）
    counter = Counter(dirs)
    max_count = max(counter.values())
    top_dirs = [d for d, c in counter.items() if c == max_count]
    most_common_dir = max(top_dirs, key=lambda d: d.count("/"))
    canonical_compact = most_common_dir.replace("/", "")

    for m in metas:
        if not m.filename:
            continue
        # 场景 1：path 缺失 → 借用
        if m.path is None:
            m.path = f"{most_common_dir}/{m.filename}"
            m.flags.append("code.path_inferred_from_peer")
            continue
        # 场景 2：粘连还原（"gpuopenmax" → "gpu/openmax"）
        if "/" not in m.path:
            continue
        m_dir = m.path.rsplit("/", 1)[0]
        if (
            m_dir != most_common_dir
            and m_dir.replace("/", "") == canonical_compact
        ):
            m.path = f"{most_common_dir}/{m.filename}"
            m.flags.append("code.path_segments_recovered")


def _extract_for_column(idx: int, lines: list[TextLine]) -> IDEMeta:  # noqa: C901 — breadcrumb 唯一真相多分支
    """从一栏的 above_code line 中解析 tab/breadcrumb。

    **真相约束**（用户决策 2026-04-26）：含 ``>`` 的 breadcrumb 行是
    IDE 的"当前打开文件路径"显示，必然是该栏当前可见的源文件——必须
    作为唯一真相。tab bar 文字会跨栏 leak（VSCode 顶部 tab 不分屏）、
    OCR 又常把多 tab 误识为 active，所以 tab 只在 breadcrumb 完全
    缺失时才作为兜底。

    **breadcrumb 片段拼接**（DSC06953/07050 回归修复）：
    OCR 经常把一行 breadcrumb 拆成多个 bbox（``openmax_`` + ``_video_
    decode_accelerator.cc`` 等），按 x 顺序拼接 + 边界字符去重后再走
    单行 ``_parse_breadcrumb``，避免单段 fragment 被误识为 tab。

    **截断补全**：拼接后若 filename 仍以 ``_`` 开头或与某 tab 候选
    suffix-match → 用 tab 完整版（DSC07050 ``_decode_accelerator.cc``
    场景）。但若 filename 已是合法独立名，禁止 tab override（防 .h/.cc
    误覆盖）。
    """
    band = _detect_breadcrumb_band(lines)
    breadcrumb_lines, tab_lines = _split_lines_by_kind(lines, band=band)

    # 1. 把 breadcrumb 行按 x 拼接成单行（处理 OCR 片段化），再常规解析
    stitched = _stitch_breadcrumb_fragments(breadcrumb_lines)
    path, filename = _parse_breadcrumb(stitched) if stitched else (None, None)
    came_from_tab = False

    # 2. 截断/被前缀吞掉的 filename（``_decode_accelerator.cc``）→ 用
    # 同栏 tab 候选 suffix-match 补全（决策 2026-04-27）
    if filename and _looks_truncated_filename(filename):
        completed = _complete_via_tab_suffix(filename, tab_lines)
        if completed:
            if path and "/" in path:
                path = path.rsplit("/", 1)[0] + "/" + completed
            elif path:
                path = completed
            filename = completed

    # 3. tab 仅在 breadcrumb 没解出 filename 时兜底；不允许 override
    if not filename:
        tab_filename = _pick_best_tab_filename(
            tab_lines, hint_lines=breadcrumb_lines,
        )
        if tab_filename:
            filename = tab_filename
            came_from_tab = True

    # 4. 语言 hint
    language = _filename_to_language(filename) if filename else None

    # 5. quality flags
    flags: list[str] = []
    if not filename:
        flags.append("code.tab_unreadable")
    if not breadcrumb_lines:
        flags.append("code.breadcrumb_missing")
    elif filename and not path and not came_from_tab:
        # breadcrumb 解出了 filename 但没解出 path（多见于 OCR 把分隔符
        # 读丢只剩单段）。标 flag 让上层 _reconcile_within_image 借同图
        # 其他栏的 dir 补全。
        flags.append("code.breadcrumb_path_missing")
    if came_from_tab:
        # 审计信号：标识本栏 filename 来自 tab 兜底而非 breadcrumb，
        # 准确度低于 breadcrumb-truth 路径。下游归类可据此降低权重，
        # 或运维通过 quality_report 统计 tab 兜底比例评估 OCR 质量。
        flags.append("code.tab_only_fallback")

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


def _detect_breadcrumb_band(
    lines: list[TextLine],
) -> tuple[int, int] | None:
    """找到 breadcrumb 行所在的 y 区间。

    任一含 ``>`` 的行就是 breadcrumb 锚点，取所有锚点 y 范围的并集。
    返回 ``(y_top, y_bottom)`` 或 ``None``（无 breadcrumb）。
    """
    bc_anchors = [ln for ln in lines if ">" in ln.text]
    if not bc_anchors:
        return None
    y_top = min(ln.bbox[1] for ln in bc_anchors)
    y_bot = max(ln.bbox[3] for ln in bc_anchors)
    return y_top, y_bot


def _line_in_band(line: TextLine, band: tuple[int, int]) -> bool:
    """文本行是否落在 y band 内（重叠占行高 ≥ 50%）。"""
    line_top, line_bot = line.bbox[1], line.bbox[3]
    band_top, band_bot = band
    overlap = max(0, min(line_bot, band_bot) - max(line_top, band_top))
    height = max(1, line_bot - line_top)
    return overlap / height >= 0.5


def _split_lines_by_kind(
    lines: list[TextLine],
    *,
    band: tuple[int, int] | None = None,
) -> tuple[list[TextLine], list[TextLine]]:
    """把 lines 拆成 (breadcrumb, tab) 两组。

    给定 ``band`` 时优先按 y 带划分：落在 band 上的视为 breadcrumb 片段
    （包括没有 ``>`` 的截断片段，如 DSC06953 ``_video_decode_accelerator
    .cc``）。否则退回旧规则（含 ``>`` 视为 breadcrumb）。
    """
    breadcrumb: list[TextLine] = []
    tab: list[TextLine] = []
    for ln in lines:
        text = ln.text.strip()
        if not text:
            continue
        in_band = band is not None and _line_in_band(ln, band)
        if in_band:
            breadcrumb.append(ln)
            continue
        if text.count(">") >= 2:
            breadcrumb.append(ln)
        elif FILENAME_RE.search(text):
            tab.append(ln)
    return breadcrumb, tab


def _stitch_breadcrumb_fragments(lines: list[TextLine]) -> str:
    """按 x 顺序拼接 breadcrumb 片段，对 bbox 重叠的相邻片段去重首尾共享字符。

    OCR 把一行连续文字拆成多个 bbox 时，常在分割点处复制字符（``openmax_``
    + ``_video_decode_accelerator.cc`` → 共享 ``_``）。重叠区域的首尾共享
    字符 → 去重一份。

    非重叠片段之间用空格分隔；重叠片段无空格直接拼。
    """
    if not lines:
        return ""
    sorted_lines = sorted(lines, key=lambda ln: ln.bbox[0])
    out_parts: list[str] = []
    out = ""
    prev_x_max = -1
    for ln in sorted_lines:
        text = ln.text.strip()
        if not text:
            continue
        x_min, x_max = ln.bbox[0], ln.bbox[2]
        if not out:
            out = text
        elif x_min < prev_x_max + 5:
            # 相邻或重叠 → 尝试边界字符去重，无空格拼接
            text = _dedup_overlap_boundary(out, text)
            out += text
        else:
            out += " " + text
        prev_x_max = max(prev_x_max, x_max)
        out_parts.append(text)
    return out


def _dedup_overlap_boundary(prev: str, curr: str) -> str:
    """两个相邻片段共享首尾字符时去重一份。

    取最长共同 ``prev[-n:] == curr[:n]`` 的 n（≤ 8 防止误伤），从 curr
    去掉前 n 个字符。无共享时返回原 curr。
    """
    max_n = min(8, len(prev), len(curr))
    for n in range(max_n, 0, -1):
        if prev[-n:] == curr[:n]:
            return curr[n:]
    return curr


def _looks_truncated_filename(filename: str) -> bool:
    """启发式：filename 看起来被前缀截断（OCR 在 ``_`` 处一刀切的产物）。

    典型：以 ``_`` 开头（``_video_decode_accelerator.cc``）。
    """
    return filename.startswith("_")


def _complete_via_tab_suffix(
    partial: str, tab_lines: list[TextLine],
) -> str | None:
    """用同栏 tab 候选的完整名 suffix-match 补全 partial filename。

    例：partial=``_decode_accelerator.cc``，tab 候选含
    ``openmax_video_decode_accelerator.cc`` → 后者以前者结尾 → 用后者。
    多个 tab 候选 endswith partial 时取最长（更具体）。
    """
    candidates: list[str] = []
    for ln in tab_lines:
        for m in FILENAME_RE.finditer(ln.text):
            cand = _strip_attached_icon(m.group(1))
            if cand != partial and cand.endswith(partial):
                candidates.append(cand)
    if not candidates:
        return None
    return max(candidates, key=len)


def _filename_to_language(filename: str) -> str | None:
    """文件后缀 → 语言 hint"""
    ext = filename.rsplit(".", 1)[-1].lower()
    return EXT_TO_LANG.get(ext)


def _parse_breadcrumb(text: str) -> tuple[str | None, str | None]:  # noqa: C901 — 多分隔符 + path 兜底分支
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
    file_match: re.Match[str] | None = None
    for i in range(len(parts) - 1, -1, -1):
        m = FILENAME_RE.search(parts[i])
        if m:
            file_idx = i
            filename = _strip_attached_icon(m.group(1))
            file_match = m
            break
    if file_idx < 0 or filename is None or file_match is None:
        return None, None

    # 反向收集路径段，遇到 symbol/另一个 file/非法字符就停（spike DSC06837
    # 等场景：OCR 把两条 breadcrumb 合一行，含 ``{}media > AllocateOmxC``
    # symbol path，必须截断不让它污染 path）
    path_segments: list[str] = []
    for j in range(file_idx - 1, -1, -1):
        cleaned = _clean_segment(parts[j])
        if not cleaned:
            break
        # 遇到第二个含扩展名段 = 这是上一个 file 的路径终点，停止
        if FILENAME_RE.search(cleaned):
            break
        if not _is_valid_path_segment(cleaned):
            break
        path_segments.insert(0, cleaned)

    # 文件段同时含路径前缀（DSC07050：``openmax C+openmax_video_decode_acc
    # elerator.cc`` —— OCR 把分隔符 `>` 漏识，dir ``openmax`` 与 filename
    # 同段）。把文件名前的空白分词逐个验证为路径段，追加到 path_segments
    # 末尾。注意要剔除 VSCode tab 图标 OCR 噪声（``C+`` / ``C`` 等单字符
    # icon），否则 ``C openmax.h`` 会误产生 ``C/openmax.h``。
    prefix_text = parts[file_idx][: file_match.start(1)]
    for word in prefix_text.split():
        cleaned = _clean_segment(word)
        if (
            cleaned
            and not FILENAME_RE.search(cleaned)
            and _is_valid_path_segment(cleaned)
            and not _looks_like_icon_word(cleaned)
        ):
            path_segments.append(cleaned)

    path_segments.append(filename)
    return "/".join(path_segments), filename


def _clean_segment(seg: str) -> str:
    """去掉 VSCode 文件类型图标 OCR 误识的前缀（如 ``C+ ``、``Cgl ``）"""
    return _ICON_PREFIX_WITH_SPACE_RE.sub("", seg).strip()


_VALID_PATH_SEGMENT_RE = re.compile(r"^[\w\-.]+$")
_CAMELCASE_SYMBOL_RE = re.compile(r"^[A-Z][a-z]+[A-Z]")

#: VSCode tab 图标 OCR 残留，长度短且大写为主，不是合法 dir 名。
#: 单字符 ``C`` / ``H`` / ``J`` / ``S`` / ``T`` 也算（icon 残留）。
_ICON_WORDS: frozenset[str] = frozenset({
    "C", "C+", "G", "G+", "H", "J", "S", "T", "TS", "JS", "JSX", "TSX",
})


def _looks_like_icon_word(seg: str) -> bool:
    """判断段是 VSCode tab 图标 OCR 残留（不是合法路径段）。"""
    return seg in _ICON_WORDS


def _is_valid_path_segment(seg: str) -> bool:
    """段看起来像合法路径段（vs symbol path 或 OCR 噪声）

    路径段：纯字母数字 + ``_-.``，长度 ≤ 50。
    排除：CamelCase 符号（``AllocateOmxC``）、过长段、含 ``{}()`` 等
    """
    if len(seg) > 50 or not _VALID_PATH_SEGMENT_RE.match(seg):
        return False
    return not _CAMELCASE_SYMBOL_RE.match(seg)


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


#: VSCode 窗口标题/SSH 标识的噪声特征（不是文件 tab）
_WINDOW_TITLE_RE = re.compile(r"\[SSH:|-src\[|\(.*@.*?\)|@\d+\.\d+\.\d+\.\d+")

#: VSCode active tab 关闭按钮（紧邻文件名后；允许中间数字如 ``.cc 4 ×``
#: 表示该文件有 4 处未保存修改，OCR 常把空格吞掉成 ``.cc4×``）
_ACTIVE_TAB_RE = re.compile(r"\.[a-zA-Z]+\d*\s*[×x]")


def _pick_best_tab_filename(  # noqa: C901 — tab 候选筛选 + hint 增强多分支
    tab_lines: list[TextLine],
    *,
    hint_lines: list[TextLine] | None = None,
) -> str | None:
    """从多个 tab line 中挑出最可信的 filename。

    优先级：
      1. 排除 window title（含 SSH / -src[/IP 地址等噪声特征）
      2. 优先 active tab（VSCode 当前激活 tab 紧跟 ``×`` 关闭按钮）
      3. 用 breadcrumb-row hint suffix-match 消歧（DSC06953 等场景：
         tab bar 多 tab 都无 ``×``，但 breadcrumb 有截断片段透露 active）
      4. 否则取 y 最小的（最顶 tab bar 行）+ 第一个匹配扩展名的
    """
    if not tab_lines:
        return None

    # 第一阶段：过滤 window title 噪声
    filtered = [
        ln for ln in tab_lines
        if not _WINDOW_TITLE_RE.search(ln.text)
    ]
    if not filtered:
        filtered = tab_lines  # 全是噪声 → 退化用原列表

    # 第二阶段：优先选含 `×`（active 标记）的 tab
    active = [ln for ln in filtered if _ACTIVE_TAB_RE.search(ln.text)]
    if active:
        active.sort(key=lambda ln: ln.bbox[1])
        for ln in active:
            m = FILENAME_RE.search(ln.text)
            if m:
                return _strip_attached_icon(m.group(1))

    # 第三阶段：breadcrumb-row hint 消歧（无 active 标记时）
    if hint_lines:
        hint_filenames = _collect_filename_hints(hint_lines)
        for hint in hint_filenames:
            for ln in filtered:
                m = FILENAME_RE.search(ln.text)
                if m:
                    cand = _strip_attached_icon(m.group(1))
                    if cand == hint or cand.endswith(hint):
                        return cand

    # 第四阶段：所有过滤后 tab 按 y 排序
    filtered.sort(key=lambda ln: ln.bbox[1])
    for ln in filtered:
        m = FILENAME_RE.search(ln.text)
        if m:
            return _strip_attached_icon(m.group(1))
    return None


def _collect_filename_hints(lines: list[TextLine]) -> list[str]:
    """从 breadcrumb-row 片段中收集所有 FILENAME_RE 命中的候选名。"""
    hints: list[str] = []
    for ln in lines:
        for m in FILENAME_RE.finditer(ln.text):
            cand = _strip_attached_icon(m.group(1))
            if cand and cand not in hints:
                hints.append(cand)
    return hints
