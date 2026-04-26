# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""OCR 后处理纠错测试（A 标点统一 + B 标识符 0→O）。

样本来自 chromium spike 实际 OCR 错误：
  - ``k0mxStateInvalid`` → ``kOmxStateInvalid``
  - ``kOmxStateExecuting g=4，`` → ``kOmxStateExecuting g=4,``
  - ``（）`` → ``()``

设计原则：
  - 字符串字面量内不动（保证 URL / 路径里的 0/O 不被破坏）
  - hex 字面量不动（``0xDEAD`` 不能误改成 ``OxDEAD``）
  - 行数严格保持（让 LLM refine 的截断检测继续工作）
"""

from __future__ import annotations

import pytest

from docrestore.processing.ocr_postfix import correct_ocr_artifacts


class TestAPunctuation:
    """A 类：中英文标点统一。"""

    def test_chinese_comma_to_ascii(self) -> None:
        result = correct_ocr_artifacts("kOmxStateExecuting g=4，", "cpp")
        assert result == "kOmxStateExecuting g=4,"

    def test_chinese_parens_to_ascii(self) -> None:
        result = correct_ocr_artifacts("static int Group（）{return 1；}", "cpp")
        assert result == "static int Group(){return 1;}"

    def test_chinese_quotes_in_comment(self) -> None:
        """注释里的中文引号也统一（OCR 错认成中文引号几乎是 100% 错的）"""
        result = correct_ocr_artifacts(
            '// see "foo" link', "cpp",
        )
        assert result == '// see "foo" link'

    def test_punctuation_in_string_literal_preserved(self) -> None:
        """字符串字面量内的中文标点保留（用户真的写了中文）"""
        result = correct_ocr_artifacts(
            'logger.info("处理完成，共 5 项");', "cpp",
        )
        # 字符串内的「，」保留，分号结尾的「；」不出现（这里是 ASCII ;）
        assert '"处理完成，共 5 项"' in result

    def test_multiline_preserves_line_count(self) -> None:
        text = "a，b\nc（d）e\n"
        result = correct_ocr_artifacts(text, "cpp")
        assert result.count("\n") == text.count("\n")
        assert result == "a,b\nc(d)e\n"


class TestBIdentifierZeroToO:
    """B 类：标识符里 0→O（保守：前后都是字母时改）。"""

    def test_camelcase_zero_to_o(self) -> None:
        """k0mx → kOmx（小写+0+小写，spike 真实错误）"""
        result = correct_ocr_artifacts("k0mxStateInvalid=1", "cpp")
        assert result == "kOmxStateInvalid=1"

    def test_camelcase_zero_before_uppercase(self) -> None:
        """k0Mx → kOMx（小写+0+大写）"""
        result = correct_ocr_artifacts("k0Mx=1", "cpp")
        assert result == "kOMx=1"

    def test_underscore_zero_letter(self) -> None:
        """_0mx → _Omx（下划线+0+字母）"""
        result = correct_ocr_artifacts("var _0mxFlag = 1;", "cpp")
        assert result == "var _OmxFlag = 1;"

    def test_hex_literal_not_changed(self) -> None:
        """0xDEAD 不能误改（hex 字面量保护）"""
        result = correct_ocr_artifacts("uint32_t x = 0xDEAD;", "cpp")
        assert result == "uint32_t x = 0xDEAD;"

    def test_decimal_literal_not_changed(self) -> None:
        """100 这种十进制数字里的 0 不动"""
        result = correct_ocr_artifacts("int n = 100;", "cpp")
        assert result == "int n = 100;"

    def test_decimal_in_identifier_not_touched(self) -> None:
        """var0_name 形态：0 紧跟 _ 而不是字母 → 风险高，不动"""
        result = correct_ocr_artifacts("int var0_name = 1;", "cpp")
        # var0 后接 _，按保守规则不动
        assert result == "int var0_name = 1;"

    def test_zero_at_word_boundary_not_changed(self) -> None:
        """=0; 这种独立 0 数字，不动"""
        result = correct_ocr_artifacts("int x = 0;", "cpp")
        assert result == "int x = 0;"

    def test_string_literal_internal_zero_preserved(self) -> None:
        """字符串内的 k0mx 保留（用户也许真的命名了）"""
        result = correct_ocr_artifacts(
            'const char* s = "k0mxLabel";', "cpp",
        )
        # 字符串内不动；外部 = 0 也不该变
        assert 'k0mxLabel' in result

    def test_multiple_replacements_one_line(self) -> None:
        """同一行多个 0→O 都被替换"""
        result = correct_ocr_artifacts(
            "k0mx, k0k, k0Pause", "cpp",
        )
        assert result == "kOmx, kOk, kOPause"

    def test_chromium_enum_block(self) -> None:
        """spike 实际样本：枚举块（混合 k0mx 和真 0 数字字面量）"""
        text = (
            "enum class OmxStatusCodes : StatusCodeType {\n"
            "  k0k=0,\n"
            "  k0mxStateInvalid=1,\n"
            "  k0mxStateLoaded=2,\n"
            "};"
        )
        expected = (
            "enum class OmxStatusCodes : StatusCodeType {\n"
            "  kOk=0,\n"
            "  kOmxStateInvalid=1,\n"
            "  kOmxStateLoaded=2,\n"
            "};"
        )
        assert correct_ocr_artifacts(text, "cpp") == expected


class TestSafetyAndRobustness:
    """边界与安全：避免错伤、跨语言行为一致。"""

    def test_empty_input(self) -> None:
        assert correct_ocr_artifacts("", "cpp") == ""
        assert correct_ocr_artifacts("", None) == ""

    def test_no_change_returns_same_text(self) -> None:
        text = "int main() { return 0; }\n"
        assert correct_ocr_artifacts(text, "cpp") == text

    def test_python_language(self) -> None:
        """Python 也适用相同规则（标识符约定一致）"""
        result = correct_ocr_artifacts("x = k0mx + 1", "python")
        assert result == "x = kOmx + 1"

    def test_gn_language(self) -> None:
        """GN 配置文件：路径里的 0/O 不动（路径本身可能含 0）"""
        # gn 文件里几乎不会有标识符模式 0→O 的场景；这里验证不崩
        text = '  defines = ["FOO_BAR"]\n'
        assert correct_ocr_artifacts(text, "gn") == text

    def test_unknown_language_falls_back(self) -> None:
        """language=None 时应用通用规则（A 总是开，B 也开）"""
        result = correct_ocr_artifacts("k0mxFlag，1", None)
        assert result == "kOmxFlag,1"

    def test_line_count_strictly_preserved(self) -> None:
        """规则不能引入或删除换行（refine 行数检查依赖）"""
        text = "a\n\nb\n"
        result = correct_ocr_artifacts(text, "cpp")
        assert result.count("\n") == text.count("\n")

    def test_no_trailing_whitespace_added(self) -> None:
        text = "x = 1;"
        assert correct_ocr_artifacts(text, "cpp") == text

    @pytest.mark.parametrize("ch", ["，", "。", "；", "：", "（", "）", "！", "？"])
    def test_each_chinese_punct_mapped(self, ch: str) -> None:
        result = correct_ocr_artifacts(f"x{ch}y", "cpp")
        # 不应残留中文标点（除非在字符串字面量内）
        assert ch not in result
