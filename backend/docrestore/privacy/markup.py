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

"""markdown/HTML/LaTeX「结构跨度」识别——实体脱敏保护区的单一真相源。

实体（人名/机构名）替换只应作用于正文，不得改动结构：图片 ``src`` / 链接目标、
HTML 标签属性、行内/围栏代码、LaTeX 数学、URL。本模块把这些跨度集中定义一处，
供两端复用，避免两处正则漂移：

- ``redactor`` 用 :func:`split_protected` 把替换限制在自由文本段（保护段原样保留）；
- ``ner`` 用 :func:`mask_structure` 在检测前抹掉结构内容，避免把 ``x.jpg`` / ``;'>`` /
  ``\\mu`` 之类碎片误检为人名/机构名。

设计见 ``docs/zh/backend/pii-entity-overredaction-fix.md`` §3。
"""

from __future__ import annotations

import re

#: 结构跨度模式，顺序即匹配优先级（代码段最先——其内可能含 ``< $`` 等结构字符，
#: 须整体保护；再标签 / 图片链接 / 数学 / URL）。各分支均不含捕获组，
#: 故 ``re.split`` 只产出唯一外层捕获组（奇数段=保护段）。
_STRUCTURE_PATTERNS: tuple[str, ...] = (
    r"```.*?```",                 # 围栏代码块（DOTALL 跨行）
    r"`[^`]*`",                   # 行内代码
    r"<[^>]+>",                   # HTML 标签（含 <img src=...> / <td ...> / </td>）
    r"!?\[[^\]]*\]\([^)]*\)",     # markdown 图片 / 链接
    r"\$[^$]*\$",                 # LaTeX 行内数学（含 OCR 产的 `$ ... $`）
    r"https?://\S+",              # 裸 URL
)

#: 外层单捕获组：``re.split`` 后偶数下标=自由文本，奇数下标=保护段。
STRUCTURE_SPAN_RE: re.Pattern[str] = re.compile(
    "(" + "|".join(_STRUCTURE_PATTERNS) + ")",
    re.DOTALL,
)


def split_protected(text: str) -> list[str]:
    """按结构跨度切分：返回交替的 [自由, 保护, 自由, ...]（偶数下标=自由文本）。

    无任何结构时返回 ``[text]``（单个自由段）。供实体替换只改自由段、保护段原样拼回。
    """
    return STRUCTURE_SPAN_RE.split(text)


def mask_structure(text: str, fill: str = " ") -> str:
    """把结构跨度替换为等长填充字符（默认空格），保持其余偏移不变。

    供 NER 检测前清洗：结构内容被抹成空白后，spaCy 不会把图片名 / 标签碎片 /
    LaTeX 当成实体；正文（含表格单元格文本）原样保留以维持人名/机构名召回。
    """
    return STRUCTURE_SPAN_RE.sub(lambda m: fill * len(m.group(0)), text)
