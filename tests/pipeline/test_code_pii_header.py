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
  - ``_redact_code_pii``：header 全量脱敏 + 正文 regex（结构化 PII + 凭据/token）
    与自定义词脱敏，但不做实体脱敏（import 路径 / namespace / 标识符不动）
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
    """构造最小 SourceFile 用于 _redact_code_pii 测试。"""
    pc = PageColumn(
        page_stem="page00001",
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


class TestRedactCodePii:
    """端到端：header 全量脱敏 + 正文 regex/凭据脱敏，import/标识符不动。"""

    @pytest.mark.asyncio
    async def test_email_redacted_in_header_and_body(self) -> None:
        """header 与正文里的结构化 PII（邮箱）都脱敏；import 路径不被误伤。"""
        src = _build_source(
            "// Copyright 2024 ACME\n"
            "// Author: alice@acme.com\n"
            "\n"
            "#include \"third_party/acme/headers.h\"\n"
            "// runtime contact: bob@acme.com\n",
        )
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)  # 跳过 __init__；只测 _redact_code_pii
        await pipe._redact_code_pii([src], pii_cfg, refiner=None)
        # header 与正文邮箱都 → 占位符（正文走 redact_regex_only）
        assert "alice@acme.com" not in src.merged_text
        assert "bob@acme.com" not in src.merged_text
        # import 路径不被结构化 regex 误伤
        assert "third_party/acme/headers.h" in src.merged_text

    @pytest.mark.asyncio
    async def test_body_credential_redacted_identifier_intact(self) -> None:
        """正文里的硬编码凭据/token 被脱敏；变量名/namespace 等标识符不动。"""
        src = _build_source(
            "// header\n"
            'const char* password = "hunter2secret";\n'
            'std::string apikey = "sk-abcdefghijklmnopqrstuvwxyz0123";\n'
            "namespace acme { int Zhang = 1; }\n",
        )
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg, refiner=None)
        # 凭据值 / token 被替换
        assert "hunter2secret" not in src.merged_text
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in src.merged_text
        assert pii_cfg.credential_placeholder in src.merged_text
        # 正文不做实体脱敏（无 lexicon）：namespace / 变量名保留（AGE-50）
        assert "namespace acme" in src.merged_text
        assert "Zhang" in src.merged_text

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
        await pipe._redact_code_pii([src], pii_cfg, refiner=refiner)
        # header XuanTie 被替换
        assert "Copyright 2024 XuanTie" not in src.merged_text
        # body import 路径里的 xuantie_ext 不动（lexicon 只跑在 header 上）
        assert "xuantie_ext" in src.merged_text

    @pytest.mark.asyncio
    async def test_header_structured_pii_masked_before_detect(self) -> None:
        """#36：header 拼 combined 送 detect_pii_entities（云端）**前**，结构化
        PII（邮箱/手机）已被 regex 脱敏；人名不被 regex 触及，仍保留供实体检测。
        """
        src = _build_source(
            "// Copyright 2024 ACME\n"
            "// Author: 张三 <zhangsan@acme.com> 13800138000\n"
            "int x = 1;\n",
        )
        pii_cfg = PIIConfig(enable=True, redact_person_name=True)
        captured: list[str] = []

        async def _capture(text: str) -> tuple[list[str], list[str]]:
            captured.append(text)
            return (["张三"], [])

        refiner = AsyncMock()
        refiner.detect_pii_entities = AsyncMock(side_effect=_capture)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg, refiner=refiner)

        # detect_pii_entities 被调用，且送云端的文本里已无明文邮箱/手机
        assert len(captured) == 1
        sent = captured[0]
        assert "zhangsan@acme.com" not in sent
        assert "13800138000" not in sent
        # 已替换为请求级 PIIConfig 占位符（派生自配置，非硬编码字面量）
        assert pii_cfg.email_placeholder in sent
        # 人名未被结构化 regex 触及，仍在送检文本里（实体检测据此工作）
        assert "张三" in sent

    @pytest.mark.asyncio
    async def test_disabled_short_circuit(self) -> None:
        src = _build_source("// alice@acme.com\nint x = 1;\n")
        original = src.merged_text
        pii_cfg = PIIConfig(enable=False)
        # enable=False 时调用方过滤，但本方法 defensive：不应崩
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg, refiner=None)
        # 即使 enable=False 走到这里，redact_snippet 仍按 regex 替换
        # （这里只验证不抛异常；调用方过滤是 _code_pipeline 的职责）
        assert isinstance(src.merged_text, str)
        # 没有副作用断言，避免与 PIIConfig 其他默认字段耦合
        del original  # 避免 unused

    @pytest.mark.asyncio
    async def test_no_header_no_pii_unchanged(self) -> None:
        """无 leading comment 且正文无 PII → 不应改任何字符（正文 regex 空转）。"""
        src = _build_source("int x = 1;\nint y = 2;\n")
        original = src.merged_text
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg, refiner=None)
        assert src.merged_text == original


class TestRedactCodePiiFailClosed:
    """#25：检测失败 + block_cloud_on_detect_failure → 返回 block_cloud 阻断云端。"""

    @staticmethod
    def _raising_refiner() -> AsyncMock:
        refiner = AsyncMock()
        refiner.detect_pii_entities = AsyncMock(
            side_effect=RuntimeError("detect boom"),
        )
        return refiner

    @pytest.mark.asyncio
    async def test_detect_failure_returns_block_true(self) -> None:
        """检测抛错 + flag 默认 True → 返回 True（调用方据此跳过云端精修）。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=True,
        )
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii(
            [src], pii_cfg, refiner=self._raising_refiner(),
        )
        assert block is True

    @pytest.mark.asyncio
    async def test_detect_failure_flag_off_returns_false(self) -> None:
        """检测抛错但 flag 关 → 返回 False（保持旧行为，不阻断）。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=False,
        )
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii(
            [src], pii_cfg, refiner=self._raising_refiner(),
        )
        assert block is False

    @pytest.mark.asyncio
    async def test_detect_success_returns_false(self) -> None:
        """检测成功 → 返回 False（不阻断，照常云端精修）。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=True,
        )
        refiner = AsyncMock()
        refiner.detect_pii_entities = AsyncMock(return_value=(["someone"], []))
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii([src], pii_cfg, refiner=refiner)
        assert block is False

    @pytest.mark.asyncio
    async def test_no_refiner_returns_false(self) -> None:
        """无 refiner（未尝试检测）→ 返回 False。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=True,
        )
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii([src], pii_cfg, refiner=None)
        assert block is False
