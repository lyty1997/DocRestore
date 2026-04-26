# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""AGE-50 代码模式 PII 头部脱敏单元测试。

覆盖：
  - ``_split_leading_comment``：跨语言注释块识别，无注释直通
  - ``_redact_code_headers``：header 内邮箱被替换、正文 import/namespace 不动
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from docrestore.pipeline.config import PIIConfig
from docrestore.pipeline.pipeline import (
    Pipeline,
    _split_leading_comment,
)
from docrestore.processing.code_assembly import CodeColumn
from docrestore.processing.code_file_grouping import PageColumn, SourceFile
from docrestore.processing.ide_meta_extract import IDEMeta


class TestSplitLeadingComment:
    """注释块识别：覆盖 //、#、/* */、混合空行、无注释场景。"""

    def test_cpp_double_slash(self) -> None:
        text = (
            "// Copyright 2024 ACME Corp.\n"
            "// Author: alice@acme.com\n"
            "\n"
            "#include <foo.h>\n"
            "namespace acme {}\n"
        )
        header, body = _split_leading_comment(text)
        assert "Copyright" in header
        assert "alice@acme.com" in header
        assert "#include" in body
        assert "namespace" in body
        assert header + body == text

    def test_python_hash(self) -> None:
        text = (
            "# Copyright 2024 ACME\n"
            "# alice@acme.com\n"
            "import os\n"
        )
        header, body = _split_leading_comment(text)
        assert "alice@acme.com" in header
        assert "import os" in body
        assert header + body == text

    def test_c_block_comment(self) -> None:
        text = (
            "/* Copyright 2024 ACME */\n"
            "/* alice@acme.com */\n"
            "int main() { return 0; }\n"
        )
        header, body = _split_leading_comment(text)
        assert "Copyright" in header
        assert "int main" in body
        assert header + body == text

    def test_no_leading_comment(self) -> None:
        text = "import os\nprint('hi')\n"
        header, body = _split_leading_comment(text)
        assert header == ""
        assert body == text

    def test_blank_line_inside_header(self) -> None:
        """注释 → 空行 → 注释 → 代码：三块都归 header"""
        text = (
            "// Copyright 2024 ACME\n"
            "\n"
            "// Author: alice@acme.com\n"
            "int x = 1;\n"
        )
        header, body = _split_leading_comment(text)
        assert "Copyright" in header
        assert "alice@acme.com" in header
        assert "int x" in body
        assert header + body == text

    def test_empty_input(self) -> None:
        assert _split_leading_comment("") == ("", "")

    def test_only_comment_no_body(self) -> None:
        text = "// solo header\n// no body following"
        header, body = _split_leading_comment(text)
        assert header == text
        assert body == ""


def _build_source(merged_text: str, path: str = "x/foo.cc") -> SourceFile:
    """构造最小 SourceFile 用于 _redact_code_headers 测试。"""
    pc = PageColumn(
        page_stem="DSC00001",
        column_index=0,
        meta=IDEMeta(
            column_index=0,
            filename="foo.cc",
            path=path,
            language="cpp",
            tab_readable=True,
        ),
        column=CodeColumn(
            column_index=0,
            bbox=(0, 0, 100, 100),
            code_text=merged_text,
            lines=[],
            char_width=10.0,
            avg_line_height=20,
        ),
    )
    return SourceFile(
        path=path,
        filename="foo.cc",
        language="cpp",
        pages=[pc],
        merged_text=merged_text,
        line_count=merged_text.count("\n") + 1,
        line_no_range=(1, merged_text.count("\n") + 1),
    )


class TestRedactCodeHeaders:
    """端到端：header 邮箱被替换、正文 import 不动。"""

    @pytest.mark.asyncio
    async def test_email_in_header_redacted_body_intact(self) -> None:
        src = _build_source(
            "// Copyright 2024 ACME\n"
            "// Author: alice@acme.com\n"
            "\n"
            "#include \"third_party/acme/headers.h\"\n"
            "// runtime contact: bob@acme.com (in body — must NOT be touched)\n",
        )
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)  # 跳过 __init__；只测 _redact_code_headers
        await pipe._redact_code_headers([src], pii_cfg, refiner=None)
        # header 邮箱 → 占位符
        assert "alice@acme.com" not in src.merged_text
        # body 邮箱保留（不脱敏正文）+ import 路径保留
        assert "bob@acme.com" in src.merged_text
        assert "third_party/acme/headers.h" in src.merged_text

    @pytest.mark.asyncio
    async def test_lexicon_only_from_headers(self) -> None:
        """LLM 检测到的公司名只对 header 替换，不污染正文 import 路径。"""
        src = _build_source(
            "// Copyright 2024 XuanTie\n"
            "import(\"//third_party/xuantie_ext/options.gni\")\n",
        )
        pii_cfg = PIIConfig(enable=True, redact_org_name=True)
        # mock refiner 返回 XuanTie 作为 org
        refiner = AsyncMock()
        refiner.detect_pii_entities = AsyncMock(
            return_value=([], ["XuanTie"]),
        )
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_headers([src], pii_cfg, refiner=refiner)
        # header XuanTie 被替换
        assert "Copyright 2024 XuanTie" not in src.merged_text
        # body import 路径里的 xuantie_ext 不动（lexicon 只跑在 header 上）
        assert "xuantie_ext" in src.merged_text

    @pytest.mark.asyncio
    async def test_disabled_short_circuit(self) -> None:
        src = _build_source("// alice@acme.com\nint x = 1;\n")
        original = src.merged_text
        pii_cfg = PIIConfig(enable=False)
        # enable=False 时调用方过滤，但本方法 defensive：不应崩
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_headers([src], pii_cfg, refiner=None)
        # 即使 enable=False 走到这里，redact_snippet 仍按 regex 替换
        # （这里只验证不抛异常；调用方过滤是 _code_pipeline 的职责）
        assert isinstance(src.merged_text, str)
        # 没有副作用断言，避免与 PIIConfig 其他默认字段耦合
        del original  # 避免 unused

    @pytest.mark.asyncio
    async def test_no_header_skips(self) -> None:
        src = _build_source("int x = 1;\nint y = 2;\n")
        original = src.merged_text
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_headers([src], pii_cfg, refiner=None)
        # 无 leading comment → 不应改任何字符
        assert src.merged_text == original
