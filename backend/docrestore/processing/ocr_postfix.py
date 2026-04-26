# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""OCR 后处理纠错（A 标点统一 + B 标识符 0→O）。

适用场景：代码模式 (`code.enable=True`) 在 ide_layout / code_assembly /
group_into_files 完成后、render_code_files 之前对每个 SourceFile.merged_text
跑一遍。规则保守：行数严格保持、字符串字面量保护、hex / 十进制字面量不动。

D（粘连/丢空格）、E（整段错识）类错误超出规则能力，留给 CodeLLMRefiner
兜底（AGE-?? prompt 加强）。
"""

from __future__ import annotations

import re

# A 类：中英文标点统一映射。代码里出现这些字符 100% 是 OCR 错认，
# 字符串字面量内可能是真用户输入 → 调用方扫描字面量边界后再 translate。
_PUNCT_MAP: dict[str, str] = {
    "，": ",", "。": ".", "；": ";", "：": ":",
    "（": "(", "）": ")", "【": "[", "】": "]",
    "「": '"', "」": '"', "『": '"', "』": '"',
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "！": "!", "？": "?",
}
_PUNCT_TRANS = str.maketrans(_PUNCT_MAP)


# B 类：标识符里 0→O。模式：前为字母/下划线，后为字母。
# 排除 hex 字面量（0xDEAD 前为非字母）、十进制（100 前为非字母）、
# var0_name（后为非字母）、独立 = 0;（前为空格/=，后为标点）。
_IDENT_ZERO_O_RE = re.compile(r"(?<=[A-Za-z_])0(?=[A-Za-z])")


# 字符串字面量识别：单/双引号配对，允许反斜杠转义。
# 简化：行级匹配，不处理跨行 raw string（spike 极少出现）。
_STRING_LITERAL_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'
    r"|'(?:[^'\\]|\\.)*'",
)


def correct_ocr_artifacts(
    text: str, language: str | None,
) -> str:
    """应用 A+B 类规则纠错。

    Parameters
    ----------
    text : str
        待纠错文本（一个 SourceFile 的合并代码）。
    language : str | None
        语言 hint，影响 B 类规则启用范围；当前所有支持语言行为一致，
        预留未来按语言定制。

    Returns
    -------
    str
        纠错后文本，行数与输入严格相等。
    """
    del language  # 当前规则跨语言一致；保留参数便于未来按语言扩展
    if not text:
        return text

    # 行级处理：每行独立扫描字面量、应用规则。
    # 跨行字面量（C++ raw string、Python triple-quoted）spike 极少出现，
    # 简化为不识别 → 跨行字面量内的 0/O 仍可能被改，但概率极低。
    out_lines: list[str] = []
    for line in text.split("\n"):
        out_lines.append(_correct_line(line))
    return "\n".join(out_lines)


def _correct_line(line: str) -> str:
    """单行纠错：先按字符串字面量切片，外部应用 A+B，内部原样保留。"""
    if not line:
        return line

    pieces: list[str] = []
    cursor = 0
    for m in _STRING_LITERAL_RE.finditer(line):
        # 字面量之前的代码段：应用 A+B
        if m.start() > cursor:
            pieces.append(_apply_rules(line[cursor:m.start()]))
        # 字面量本身：原样保留
        pieces.append(m.group(0))
        cursor = m.end()
    # 最后一段
    if cursor < len(line):
        pieces.append(_apply_rules(line[cursor:]))
    return "".join(pieces)


def _apply_rules(segment: str) -> str:
    """对非字面量代码段应用 A+B 类规则。"""
    # A：中英文标点统一；B：标识符里 0→O
    return _IDENT_ZERO_O_RE.sub("O", segment.translate(_PUNCT_TRANS))
