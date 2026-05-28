# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""markdown_polish 测试。"""

from __future__ import annotations

from docrestore.processing.markdown_polish import (
    strip_code_block_line_numbers,
    strip_residual_ui_noise,
)


class TestStripCodeBlockLineNumbers:
    def test_typical_visual_line_numbers_stripped(self) -> None:
        md = (
            "## SPL log\n"
            "```text\n"
            "1 U-Boot SPL 2020.01\n"
            "2 FM[1] lpddr4x dualrank freq=3733\n"
            "3 ddr initialized, jump to uboot\n"
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        assert n == 3
        assert "U-Boot SPL 2020.01" in out
        # 前缀行号被剥
        assert "1 U-Boot" not in out
        assert "2 FM[1]" not in out
        # 围栏 + 标题保留
        assert "```text" in out
        assert "## SPL log" in out

    def test_too_few_lines_skipped(self) -> None:
        """代码块只有 2 行带数字前缀 → 不动（样本不足，可能是误判）。"""
        md = "```\n1 abc\n2 def\n```\n"
        out, n = strip_code_block_line_numbers(md)
        assert n == 0
        assert out == md

    def test_non_monotonic_skipped(self) -> None:
        """数字非单调递增 → 不动（可能是真实代码恰好以数字开头）。"""
        md = (
            "```c\n"
            "1 first\n"
            "5 jumped ahead\n"
            "3 went back\n"  # 非递增
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        assert n == 0
        assert out == md

    def test_zero_excluded(self) -> None:
        """以 `0 ` 开头视为非行号（行号从 1 起）。"""
        md = (
            "```c\n"
            "0 reserved\n"
            "1 first\n"
            "2 second\n"
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        # 只有 2 个候选 (1, 2) → < 3 跳过
        assert n == 0
        assert out == md

    def test_with_repeated_numbers_allowed(self) -> None:
        """相同数字（重复 / 跳号）也接受（典型 OCR 错识）。"""
        md = (
            "```text\n"
            "1 line a\n"
            "2 line b\n"
            "2 line c\n"  # OCR 重复识别
            "3 line d\n"
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        assert n == 4
        assert "1 line a" not in out
        assert "line a" in out
        assert "line c" in out

    def test_no_code_block_unchanged(self) -> None:
        md = "## A\n1 这看起来像行号但在正文里\n2 不该剥\n## B\n"
        out, n = strip_code_block_line_numbers(md)
        assert n == 0
        assert out == md

    def test_multiple_blocks_independent(self) -> None:
        md = (
            "```text\n"
            "1 a\n2 b\n3 c\n"
            "```\n"
            "正文\n"
            "```text\n"
            "10 d\n11 e\n12 f\n"
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        assert n == 6

    def test_block_with_mixed_lines_only_strips_numbered(self) -> None:
        """代码块内既有数字行也有不带数字的注释行 → 只剥数字行。"""
        md = (
            "```c\n"
            "1 #include <stdio.h>\n"
            "2 \n"  # 空 / 没有 \S 内容 — 不匹配模式
            "3 int main() {\n"
            "4   return 0;\n"
            "5 }\n"
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        # 行 2 不命中 _LINE_NUM_PREFIX_RE（要求 \S 跟随）
        # 行 1, 3, 4, 5 命中 (4 行 ≥ 3，单调) → 剥
        assert n == 4
        assert "#include <stdio.h>" in out
        assert "int main() {" in out

    def test_real_uboot_log_pattern(self) -> None:
        """复现实测：U-Boot SPL 启动 log 多次出现。"""
        md = (
            "SPL DDR 初始化失败 log:\n"
            "```text\n"
            "1 U-Boot SPL 2020.01 (Mar 19 2023 - 05:14:32 +0000)\n"
            "2 FM[1] lpddr4x dualrank freq=3733 64bit dbi_off=n sdram init\n"
            "3 PHY0 DDR_INIT_ERR\n"
            "```\n"
        )
        out, n = strip_code_block_line_numbers(md)
        assert n == 3
        assert "U-Boot SPL 2020.01" in out
        assert "PHY0 DDR_INIT_ERR" in out
        assert "1 U-Boot SPL" not in out


class TestStripResidualUINoise:
    def test_plain_text_copy_in_body_removed(self) -> None:
        md = (
            "## SPL log\n"
            "Plain Text 复制代码\n"
            "正文行\n"
        )
        out, n = strip_residual_ui_noise(md)
        assert n == 1
        assert "Plain Text 复制代码" not in out
        assert "正文行" in out
        assert "## SPL log" in out

    def test_bullet_prefixed_removed(self) -> None:
        md = "正文\n▶ Bash 复制代码\n更多正文\n"
        out, n = strip_residual_ui_noise(md)
        assert n == 1
        assert "复制代码" not in out

    def test_inline_copy_text_kept(self) -> None:
        """正文中含"复制代码"的句子不动。"""
        md = "使用 Ctrl+C 复制代码到剪贴板\n"
        out, n = strip_residual_ui_noise(md)
        assert n == 0
        assert out == md

    def test_no_match_unchanged(self) -> None:
        md = "## A\n正文\n## B\n"
        out, n = strip_residual_ui_noise(md)
        assert n == 0
        assert out == md

    def test_emmc_makefile_residual(self) -> None:
        """复现实测：EMMC final 输出残留 'Makefile 复制代码'。"""
        md = (
            "## emmc 配置\n"
            "Makefile 复制代码\n"
            "make uboot\n"
        )
        out, n = strip_residual_ui_noise(md)
        assert n == 1
        assert "Makefile 复制代码" not in out
        assert "make uboot" in out
