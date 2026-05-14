# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""OCR 后处理纠错（标点统一、标识符纠错、IDE UI 噪声过滤）。

适用场景：代码模式 (`code.enable=True`) 在 ide_layout / code_assembly /
group_into_files 完成后、render_code_files 之前对每个 SourceFile.merged_text
跑一遍。规则保守：默认行数严格保持、字符串字面量保护、hex / 十进制字面量不动。

D（粘连/丢空格）、E（整段错识）类错误超出规则能力，留给 CodeLLMRefiner
兜底（AGE-?? prompt 加强）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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

# C 类：行首注释符 OCR 错识。只在有 // 注释的语言启用，避免伤到 Python。
_SLASH_COMMENT_LANGUAGES = {
    "c", "cpp", "c++", "cc", "cxx", "h", "hpp", "hxx",
    "javascript", "typescript", "java", "go", "rust", "swift",
    "kotlin", "dart", "scala", "php", "css", "scss", "less",
}
_SLASH_COMMENT_PREFIX_RE = re.compile(r"^(\s*)[1lI]/(?=\s|\S)")


# D 类：C/C++ 预处理指令中常见 OCR 大小写错误。
_C_LIKE_LANGUAGES = {
    "c", "cpp", "c++", "cc", "cxx", "h", "hpp", "hxx", "objc",
    "objective-c",
}
_PREPROCESSOR_DIRECTIVES = {
    "include", "define", "undef", "if", "ifdef", "ifndef", "else",
    "elif", "endif", "pragma", "error", "warning", "line",
}
_PREPROCESSOR_RE = re.compile(r"^(\s*#\s*)([A-Za-z][A-Za-z0-9_]*)\b")


# E 类：IDE/编辑器 UI 噪声。只匹配整行强信号，命中后置空保留行数。
_UI_NOISE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"Loading\.{0,3}|"
    r"EXPLORER|OUTLINE|TIMELINE|"
    r"PROBLEMS(?:\s+\d+)?(?:\s+OUTPUT)?(?:\s+DEBUG\s+CONSOLE)?"
    r"(?:\s+TERMINAL)?(?:\s+PORTS)?|"
    r"OUTPUT(?:\s+DEBUG\s+CONSOLE)?(?:\s+TERMINAL)?(?:\s+PORTS)?|"
    r"DEBUG\s+CONSOLE(?:\s+TERMINAL)?(?:\s+PORTS)?|"
    r"TERMINAL(?:\s+PORTS)?|"
    r"Search\s+Marketplace|"
    r"The\s+Marketplace\s+has\s+extensions.*|"
    r"Aa\s+ab\s+\*?\s*\d+\s+of\s+\d+.*|"
    r"\d+\s+of\s+\d+\s+\S*|"
    r"src\[SSH:[^\]]+\].*|"
    r".*\s+-\s+Visual\s+Studio\s+Code"
    r")\s*$",
    re.IGNORECASE,
)

_OCR_GLYPH_NOISE = {"工", "王", "丫", "十", "丁"}


_COMMENT_PREFIX_BY_LANGUAGE: dict[str, str] = {
    "python": "#",
    "py": "#",
    "ruby": "#",
    "rb": "#",
    "shell": "#",
    "bash": "#",
    "sh": "#",
    "zsh": "#",
    "yaml": "#",
    "yml": "#",
    "toml": "#",
    "gn": "#",
    "gni": "#",
    "makefile": "#",
    "dockerfile": "#",
    "ini": "#",
    "c": "//",
    "cpp": "//",
    "c++": "//",
    "cc": "//",
    "cxx": "//",
    "h": "//",
    "hpp": "//",
    "javascript": "//",
    "typescript": "//",
    "java": "//",
    "go": "//",
    "rust": "//",
    "swift": "//",
    "kotlin": "//",
    "dart": "//",
}


@dataclass(frozen=True)
class CodePostfixResult:
    """代码 OCR 后处理结果。"""

    text: str
    """清理后的代码文本。"""

    flags: list[str]
    """可追踪处理标记，写入 files-index / quality report。"""


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
    if not text:
        return text

    # 行级处理：每行独立扫描字面量、应用规则。
    # 跨行字面量（C++ raw string、Python triple-quoted）spike 极少出现，
    # 简化为不识别 → 跨行字面量内的 0/O 仍可能被改，但概率极低。
    out_lines: list[str] = []
    for line in text.split("\n"):
        out_lines.append(_correct_line(line, language))
    return "\n".join(out_lines)


def clean_code_ocr_text(
    text: str,
    language: str | None,
) -> CodePostfixResult:
    """过滤 IDE UI 噪声并应用保守 OCR 纠错。

    UI 噪声按整行强规则匹配，命中后替换为空行而不是删除行，从而保持
    `splitlines` 的行号语义，便于后续全文修复和人工回看来源页。
    """
    if not text:
        return CodePostfixResult(text=text, flags=[])

    flags: list[str] = []
    filtered_ui = 0
    filtered_glyphs = 0
    out_lines: list[str] = []
    for line in text.split("\n"):
        if _is_ui_noise_line(line):
            out_lines.append("")
            filtered_ui += 1
            continue
        if _is_ocr_glyph_noise_line(line):
            out_lines.append("")
            filtered_glyphs += 1
            continue
        out_lines.append(_correct_line(line, language))

    if filtered_ui:
        flags.append(f"code.noise.filtered_ui_lines={filtered_ui}")
    if filtered_glyphs:
        flags.append(f"code.noise.filtered_ocr_glyphs={filtered_glyphs}")
    if flags:
        flags.append("code.ocr_postfix.line_count_preserved")

    return CodePostfixResult(text="\n".join(out_lines), flags=flags)


def comment_prefix_for_language(language: str | None) -> str | None:
    """返回语言对应的单行注释前缀；未知或无注释语言返回 None。"""
    if language is None:
        return None
    return _COMMENT_PREFIX_BY_LANGUAGE.get(language.strip().lower())


def _correct_line(line: str, language: str | None) -> str:
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
    return _apply_language_rules("".join(pieces), language)


def _apply_rules(segment: str) -> str:
    """对非字面量代码段应用 A+B 类规则。"""
    # A：中英文标点统一；B：标识符里 0→O
    return _IDENT_ZERO_O_RE.sub("O", segment.translate(_PUNCT_TRANS))


def _apply_language_rules(line: str, language: str | None) -> str:
    """应用需要语言上下文的保守规则。"""
    lang = _norm_language(language)
    if lang in _SLASH_COMMENT_LANGUAGES:
        line = _SLASH_COMMENT_PREFIX_RE.sub(r"\1//", line, count=1)
    if lang in _C_LIKE_LANGUAGES:
        line = _normalize_preprocessor_directive(line)
    return line


def _normalize_preprocessor_directive(line: str) -> str:
    """规范 C/C++ 预处理指令的大小写。"""
    match = _PREPROCESSOR_RE.match(line)
    if match is None:
        return line
    directive = match.group(2).lower()
    if directive not in _PREPROCESSOR_DIRECTIVES:
        return line
    return f"{match.group(1)}{directive}{line[match.end(2):]}"


def _is_ui_noise_line(line: str) -> bool:
    """判断整行是否为 IDE UI 噪声。"""
    return _UI_NOISE_LINE_RE.match(line) is not None


def _is_ocr_glyph_noise_line(line: str) -> bool:
    """判断是否为孤立 OCR 伪字形噪声行。"""
    stripped = line.strip()
    return len(stripped) == 1 and stripped in _OCR_GLYPH_NOISE


def _norm_language(language: str | None) -> str:
    """标准化语言 hint。"""
    if language is None:
        return ""
    return language.strip().lower()
