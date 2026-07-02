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

"""代码模式版面 sidecar：悬停行 ↔ 原图局部放大的位置真相源 ``.code_layout.json``。

代码模式收尾时落盘、前端放大镜端读取后按行级 bbox 在原图上裁出局部放大（#93）。
本模块纯函数（dataclass + 序列化），不依赖 OCR / PII / 导出工具，便于单测。

相对文档模式 ``.layout.json`` 的差异：
- 粒度是**行级**（``line_no -> bbox``）而非块级——代码逐行对应原图一行；
- **不含正文文字**，只有 ``line_no + page + bbox`` → PII 面零暴露，无需脱敏；
- 同一 ``line_no`` 多页观测（拍照重叠区）时，按 ``SourceFile.line_provenance``
  指定的胜出页取 bbox，避免重复行。

反序列化采用「坏行 / 坏文件跳过、不整份失败」的宽松策略（向后兼容、容损），
与 ``layout_sidecar`` 同口径。设计真相源 ``docs/zh/code-source-magnifier.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

from docrestore.output.sidecar_common import (
    as_int_quad,
    load_json_sidecar,
    write_json_sidecar,
)

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import PageColumn, SourceFile

#: sidecar 文件名：dot 前缀=内部文件，不进下载 zip / asset 白名单。
CODE_LAYOUT_FILENAME = ".code_layout.json"
#: schema 版本：读到不匹配版本即视为无数据（fail-safe，前端不显示放大镜）。
_CODE_LAYOUT_VERSION = 1


@dataclass(frozen=True)
class CodeLineBox:
    """单行代码在原图的像素框：行号 + 来源页标识 + bbox。"""

    line_no: int  # OCR 行号（与前端编辑器 data-line 同值）
    page: str  # 来源页标识 ``{page_stem}.col{column_index}``（对齐 source_page_ranges）
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) 原图像素


@dataclass(frozen=True)
class CodeFileLayout:
    """单个源文件的行级版面：path 对齐 files-index entry.path。"""

    path: str
    lines: list[CodeLineBox] = field(default_factory=list)
    #: #5 行映射：按**精修后**正文行序（0-based）索引、值为该行对应的「原 OCR line_no」
    #: （= ``lines`` 的 CodeLineBox.line_no 键），None = rewrite/repair 新增行→不放大。
    #: 空 = 精修守恒（identity，前端按 displayLineNumber 直接查表）。与 ``lines``（按
    #: line_no 排序）是两套坐标：``line_map`` 按显示行序，``lines`` 按 line_no。
    line_map: list[int | None] = field(default_factory=list)


@dataclass(frozen=True)
class CodeLayout:
    """整个代码任务的行级版面：各源文件行 bbox。"""

    files: list[CodeFileLayout]
    version: int = _CODE_LAYOUT_VERSION


# ── 构造：SourceFile → sidecar ────────────────────────────────


def _page_id(page: PageColumn) -> str:
    """来源页标识 ``{page_stem}.col{column_index}``（与 renderer 同口径）。"""
    return f"{page.page_stem}.col{page.column_index}"


def _file_layout_from_source(src: SourceFile) -> CodeFileLayout:
    """单个 ``SourceFile`` → ``CodeFileLayout``：逐行取胜出页的 bbox。

    同一 ``line_no`` 多页观测时优先用 ``line_provenance`` 指定的胜出页 stem；
    胜出页该行无 bbox（推断行）时回退首个有 bbox 的页（按 pages 顺序，稳定）。
    仅收 ``bbox`` 非空的行；推断行 / gap 填充空行无 bbox → 跳过。
    """
    # line_no -> (CodeLineBox, 是否来自胜出页)
    best: dict[int, tuple[CodeLineBox, bool]] = {}
    for page in src.pages:
        page_id = _page_id(page)
        for line in page.column.lines:
            if line.bbox is None:
                continue
            is_winner = src.line_provenance.get(line.line_no) == page.page_stem
            existing = best.get(line.line_no)
            if existing is None:
                best[line.line_no] = (
                    CodeLineBox(line_no=line.line_no, page=page_id, bbox=line.bbox),
                    is_winner,
                )
            elif is_winner and not existing[1]:
                # 已有非胜出页占位 → 升级为胜出页 bbox。
                best[line.line_no] = (
                    CodeLineBox(line_no=line.line_no, page=page_id, bbox=line.bbox),
                    True,
                )
    lines = [box for box, _ in best.values()]
    lines.sort(key=lambda b: b.line_no)
    # line_map 在 LLM 精修回写处算好挂到 src（空=守恒/未精修，identity）。
    return CodeFileLayout(
        path=src.path, lines=lines, line_map=list(src.refined_line_map),
    )


def build_code_layout(sources: list[SourceFile]) -> CodeLayout | None:
    """从 ``list[SourceFile]`` 构造 ``CodeLayout``。

    所有文件均无任何行 bbox（非 VL 引擎 / 捕获失败）→ None，调用方不落盘、
    前端无数据不显示放大镜。
    """
    files = [_file_layout_from_source(src) for src in sources]
    if not any(file_layout.lines for file_layout in files):
        return None
    return CodeLayout(files=files)


def build_refined_line_map(
    original: str, refined: str, base_line_no: int,
) -> list[int | None]:
    """求「精修后行序 → 原 OCR line_no」映射（#5，供 pipeline 在精修回写处调用）。

    用 ``difflib`` 对 ``original`` / ``refined`` 逐行对齐：仅 ``equal`` 段（未改动行，
    与原文逐字相同）精确映回其原 OCR line_no（``base_line_no + 原行下标``）；
    ``replace`` / ``insert``（被 LLM 改写 / 新增的行）一律置 None——按本放大镜既定的
    「不放大优于错放邻行」原则，改过的行宁可不放大也不指向可能错位的源图行。

    切行用 ``str.split("\\n")`` 与前端 ``splitEditorLines`` 同口径（含末尾空行），
    使返回数组下标与前端 ``displayLineNumber - line_no_range[0]`` 严格对齐。
    ``base_line_no`` = ``SourceFile.line_no_range[0]``（原文首行 OCR line_no）。

    精修守恒（``original`` 与 ``refined`` 逐行相同）→ 返回 ``[]``（identity 信号，
    sidecar 不落该字段、前端按 displayLineNumber 直接查表，零回归）。
    """
    old_lines = original.split("\n")
    new_lines = refined.split("\n")
    if old_lines == new_lines:
        return []
    line_map: list[int | None] = [None] * len(new_lines)
    matcher = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue  # replace / insert / delete：改写或新增行 → 不放大（None）
        for offset in range(j2 - j1):
            line_map[j1 + offset] = base_line_no + i1 + offset
    return line_map


# ── 序列化 ────────────────────────────────────────────────────


def _file_to_dict(file_layout: CodeFileLayout) -> dict[str, object]:
    """单文件 → dict；``line_map`` 仅非空时输出（守恒/旧任务体积不变、纯可选增量）。"""
    out: dict[str, object] = {
        "path": file_layout.path,
        "lines": [
            {
                "line_no": line.line_no,
                "page": line.page,
                "bbox": list(line.bbox),
            }
            for line in file_layout.lines
        ],
    }
    if file_layout.line_map:
        out["line_map"] = list(file_layout.line_map)
    return out


def to_dict(layout: CodeLayout) -> dict[str, object]:
    """``CodeLayout`` → JSON 可序列化 dict。"""
    return {
        "version": layout.version,
        "files": [_file_to_dict(file_layout) for file_layout in layout.files],
    }


# ── 反序列化（宽松：坏行 / 坏文件跳过、不整份失败）────────────


def _line_from_dict(raw: object) -> CodeLineBox | None:
    """单行 dict → ``CodeLineBox``；字段非法返回 None（调用方跳过该行）。"""
    if not isinstance(raw, dict):
        return None
    bbox = as_int_quad(raw.get("bbox"))
    page = raw.get("page")
    raw_line_no = raw.get("line_no")
    if bbox is None or not isinstance(page, str) or not isinstance(
        raw_line_no, int,
    ) or isinstance(raw_line_no, bool):
        return None
    return CodeLineBox(line_no=raw_line_no, page=page, bbox=bbox)


def _as_line_map(raw: object) -> list[int | None]:
    """宽松解析 ``line_map``：非 list → []；逐元素 int（非 bool）或 None，其余置 None。

    缺键 / 非法 → []（identity 回退）；坏元素就地置 None（不整文件失败、容损）。
    """
    if not isinstance(raw, list):
        return []
    out: list[int | None] = []
    for v in raw:
        out.append(v if isinstance(v, int) and not isinstance(v, bool) else None)
    return out


def _file_from_dict(raw: object) -> CodeFileLayout | None:
    """单文件 dict → ``CodeFileLayout``；path 非法返回 None（调用方跳过该文件）。

    坏行逐个跳过（不致整文件失败）；path / lines 容器非法才整文件弃。
    ``line_map`` 缺键 / 非法 → []（向后兼容旧 sidecar、退化为 identity）。
    """
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    raw_lines = raw.get("lines")
    if not isinstance(path, str) or not isinstance(raw_lines, list):
        return None
    lines: list[CodeLineBox] = []
    for raw_line in raw_lines:
        line = _line_from_dict(raw_line)
        if line is not None:
            lines.append(line)
    return CodeFileLayout(
        path=path, lines=lines, line_map=_as_line_map(raw.get("line_map")),
    )


def from_dict(data: object) -> CodeLayout | None:
    """JSON dict → ``CodeLayout``；版本不符 / 顶层非法 → None。

    坏文件逐个跳过（不致整份失败）；无任何合法文件 → None（前端无数据不放大）。
    """
    if not isinstance(data, dict) or data.get("version") != _CODE_LAYOUT_VERSION:
        return None
    raw_files = data.get("files")
    if not isinstance(raw_files, list):
        return None
    files: list[CodeFileLayout] = []
    for raw_file in raw_files:
        file_layout = _file_from_dict(raw_file)
        if file_layout is not None:
            files.append(file_layout)
    if not files:
        return None
    return CodeLayout(files=files)


# ── 磁盘 I/O（同步；async 调用方用 asyncio.to_thread 包裹）─────


def write_code_layout(output_dir: Path, layout: CodeLayout) -> Path:
    """把 ``CodeLayout`` 写到 ``output_dir/.code_layout.json``，返回路径。"""
    return write_json_sidecar(output_dir / CODE_LAYOUT_FILENAME, to_dict(layout))


def load_code_layout(output_dir: Path) -> CodeLayout | None:
    """读 ``output_dir/.code_layout.json`` → ``CodeLayout``；缺失 / 损坏 → None。"""
    data = load_json_sidecar(output_dir / CODE_LAYOUT_FILENAME)
    return None if data is None else from_dict(data)
