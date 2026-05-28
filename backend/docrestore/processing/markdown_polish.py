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

"""Final-refine 后的轻量 markdown 美化（程序化兜底）。

两个独立操作：
1. `strip_code_block_line_numbers` — 剥代码块内"视觉行号"前缀
   场景：原图代码框左侧的行号被 OCR 识别成 `1 ` `2 ` ... 前缀塞到代码里
   实测残留：U-Boot 51 行 / EMMC 27 行
2. `strip_residual_ui_noise` — 兜底删 LLM 漏过的代码框 UI 噪音行
   场景：cleaner 已剥过一次，但 final_refine 偶尔把"复制代码"塞回输出
   实测残留：EMMC 段 4 retry 后还有 1 处 `Makefile 复制代码`

两个操作都设计成幂等 + 安全：
- 行号剥离：要求代码块内 ≥ 3 行带数字前缀且**单调递增**才剥（排除真实
  代码恰好以数字开头的小概率场景）
- UI 噪音：复用 cleaner 的 `UI_NOISE_LINE_RE`（极特定字符串模式，几乎
  不可能在真实代码内出现）
"""

from __future__ import annotations

import logging
import re

from docrestore.processing.cleaner import UI_NOISE_LINE_RE

logger = logging.getLogger(__name__)


_FENCE_RE = re.compile(
    r"^(\s*)(```+|~~~+)([^\n]*)\n(.*?)^\1\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_LINE_NUM_PREFIX_RE = re.compile(
    r"^([ \t]*)(\d{1,4})[ \t]+(\S.*)$",
)


def _strip_line_numbers_in_block(block_body: str) -> tuple[str, int]:
    """对单个代码块内部应用行号剥离。返回 (new_body, stripped_count)。

    安全规则：
    - 至少 3 行命中数字前缀模式
    - 这些数字单调递增（允许重复 / 跳号，但严格非递减）
    - 数字 ≥ 1（行号不会是 0；规避代码注释 `0 reserved` 这种）
    """
    lines = block_body.splitlines()
    if len(lines) < 3:
        return block_body, 0

    candidates: list[tuple[int, int]] = []  # (line_idx, parsed number)
    for i, line in enumerate(lines):
        m = _LINE_NUM_PREFIX_RE.match(line)
        if m and int(m.group(2)) >= 1:
            candidates.append((i, int(m.group(2))))

    if len(candidates) < 3:
        return block_body, 0

    # 单调非递减检查
    for k in range(1, len(candidates)):
        if candidates[k][1] < candidates[k - 1][1]:
            return block_body, 0

    # 通过校验，剥前缀
    candidate_idxs = {idx for idx, _ in candidates}
    new_lines: list[str] = []
    stripped = 0
    for i, line in enumerate(lines):
        if i in candidate_idxs:
            m = _LINE_NUM_PREFIX_RE.match(line)
            if m is not None:
                new_lines.append(m.group(1) + m.group(3))
                stripped += 1
                continue
        new_lines.append(line)

    # 保留末尾空行（splitlines 不带末尾换行，需要看原 body 是否以换行结束）
    suffix = "\n" if block_body.endswith("\n") else ""
    return "\n".join(new_lines) + suffix, stripped


def strip_code_block_line_numbers(markdown: str) -> tuple[str, int]:
    """扫所有 ``` ... ``` / ~~~ ... ~~~ 代码块，剥视觉行号前缀。

    返回 `(new_markdown, total_stripped_lines)`。
    """
    total_stripped = 0
    parts: list[str] = []
    last_end = 0
    for m in _FENCE_RE.finditer(markdown):
        # 把匹配前的非代码段直接拷过去
        parts.append(markdown[last_end:m.start()])
        indent, fence, info, body = m.group(1), m.group(2), m.group(3), m.group(4)
        new_body, stripped = _strip_line_numbers_in_block(body)
        total_stripped += stripped
        # 重组完整 fence + new_body + 闭合 fence
        parts.append(f"{indent}{fence}{info}\n{new_body}{indent}{fence}")
        last_end = m.end()
    parts.append(markdown[last_end:])

    if total_stripped:
        logger.info(
            "代码块行号剥离：共剥 %d 行前缀", total_stripped,
        )
    return "".join(parts), total_stripped


def strip_residual_ui_noise(markdown: str) -> tuple[str, int]:
    """扫整篇 markdown 删除整行匹配 `UI_NOISE_LINE_RE` 的残留 UI 噪音行。

    与 `OCRCleaner.remove_ui_noise` 用同一正则（cleaner 已先做过；这里
    兜底 LLM 偶尔把这种行带回输出的场景）。

    返回 `(new_markdown, removed_lines)`。
    """
    lines = markdown.splitlines(keepends=True)
    kept: list[str] = []
    removed = 0
    for line in lines:
        # 匹配时去掉行尾 \n
        bare = line.rstrip("\n").rstrip("\r")
        if UI_NOISE_LINE_RE.match(bare):
            removed += 1
            continue
        kept.append(line)
    if removed:
        logger.info(
            "UI 噪音残留剥离：共删 %d 行", removed,
        )
    return "".join(kept), removed
