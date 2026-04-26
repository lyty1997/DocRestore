# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""AGE-49 compile_check 错误分类测试。

g++ stderr 文本 → 区分 syntax (真 OCR 噪声) vs semantic (缺 chromium sysroot)
vs cascade (前面错误的连锁反应，归 semantic)。

样本来自 spike e2e-refined 的真实 g++ 报错。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from age8_compile_check import _classify_errors  # type: ignore[import-not-found]


class TestSyntaxErrors:
    """OCR 噪声专属信号：粘连 / 中文标点 / 引号未闭等"""

    def test_invalid_preprocessing_directive(self) -> None:
        """spike 高频：#ifndEfOMX 粘连"""
        err = (
            "foo.h:5:2: error: invalid preprocessing directive "
            "#ifndEfOMX_GPU_MEDIA_GLES2_DMABUF_TO_EGL_IMAGE_TRANSLATORH\n"
        )
        syntax, semantic, lines = _classify_errors(err)
        assert syntax == 1
        assert semantic == 0
        assert lines == [5]

    def test_stray_token(self) -> None:
        """OCR 把中文标点带进代码 → stray '\\343'"""
        err = "foo.cc:10:5: error: stray '\\343' in program\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 1
        assert semantic == 0

    def test_unterminated_string(self) -> None:
        err = 'foo.cc:15:1: error: unterminated string literal\n'
        syntax, _, _ = _classify_errors(err)
        assert syntax == 1


class TestSemanticErrors:
    """缺 chromium sysroot 的语义错（不是 OCR 责任）"""

    def test_undeclared_identifier(self) -> None:
        err = "foo.h:12:12: error: 'BitstreamBuffer' has not been declared\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 0
        assert semantic == 1

    def test_does_not_name_a_type(self) -> None:
        err = "foo.h:30:5: error: 'StatusCodeType' does not name a type\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 0
        assert semantic == 1

    def test_in_nested_name_specifier(self) -> None:
        """枚举继承时 ': StatusCodeType' 缺类型 → g++ syntax 风格表述但根因是 sysroot"""
        err = "foo.h:12:27: error: found ':' in nested-name-specifier, expected '::'\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 0
        assert semantic == 1


class TestCascadeErrors:
    """前面错误的连锁反应 → 归 semantic（避免污染 OCR 噪声指标）"""

    def test_expected_unqualified_id_cascade(self) -> None:
        err = "foo.h:18:5: error: expected unqualified-id before numeric constant\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 0
        assert semantic == 1

    def test_expected_brace(self) -> None:
        err = "foo.h:37:1: error: expected '}' at end of input\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 0
        assert semantic == 1


class TestMixedErrors:
    """混合场景：真 OCR 噪声 + sysroot cascade"""

    def test_mixed_real_spike_sample(self) -> None:
        """spike 实际：line 5/6 粘连(syntax) + line 9 'Loading' cascade(sem)"""
        err = (
            "foo.cc:5:2: error: invalid preprocessing directive "
            "#ifndEfOMX_GPU\n"
            "foo.cc:6:2: error: invalid preprocessing directive "
            "#dEfineOMX_GPU\n"
            "foo.cc:9:1: error: 'Loading' does not name a type\n"
        )
        syntax, semantic, lines = _classify_errors(err)
        assert syntax == 2
        assert semantic == 1
        assert lines == [5, 6]


class TestEdgeCases:
    def test_empty_input(self) -> None:
        assert _classify_errors("") == (0, 0, [])

    def test_no_errors_in_text(self) -> None:
        """warning 不算错误（无 'error:' 标记）"""
        err = "foo.cc:5:2: warning: unused variable 'x'\n"
        syntax, semantic, _ = _classify_errors(err)
        assert syntax == 0
        assert semantic == 0

    def test_unmatched_garbage_falls_through_to_semantic(self) -> None:
        """未匹配 syntax/semantic 关键词的错误 → fallback 到 semantic（保守）"""
        err = "foo.cc:1:1: error: some never-seen weird error message\n"
        syntax, semantic, _ = _classify_errors(err)
        # fallback 到 semantic（语义错），不污染 OCR 噪声指标
        assert syntax == 0
        assert semantic == 1
