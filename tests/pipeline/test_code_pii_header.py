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
  - ``_redact_code_pii``：header 全量脱敏（full）+ 正文 tokens_only（仅高置信密钥
    token + 自定义词，不跑 KV/手机/邮箱全量正则、不做实体脱敏），import/标识符不动；
    实体检测改本地 NER（S3，注入 fake detector 拦截，不依赖 spaCy 是否安装）
"""

from __future__ import annotations

import pytest

from docrestore.pipeline.config import PIIConfig
from docrestore.pipeline.pipeline import (
    Pipeline,
    _split_leading_comment,
)
from docrestore.privacy import guard as guard_mod
from docrestore.privacy.ner import NERUnavailableError
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


class _FakeDetector:
    """假本地 NER detector：返回预置实体 / 抛异常 / 捕获送检文本。"""

    def __init__(
        self,
        persons: tuple[str, ...] = (),
        orgs: tuple[str, ...] = (),
        *,
        raises: Exception | None = None,
        captured: list[str] | None = None,
    ) -> None:
        """记录预置实体、待抛异常、送检文本捕获列表。"""
        self._persons = persons
        self._orgs = orgs
        self._raises = raises
        self._captured = captured

    def detect(self, text: str) -> tuple[list[str], list[str]]:
        """捕获送检文本，返回预置实体；配置了 raises 则抛出。"""
        if self._captured is not None:
            self._captured.append(text)
        if self._raises is not None:
            raise self._raises
        return list(self._persons), list(self._orgs)


def _patch_detector(
    monkeypatch: pytest.MonkeyPatch, detector: _FakeDetector,
) -> None:
    """把 guard.get_detector 替换为返回指定 fake detector（拦截本地 NER 检测）。"""
    monkeypatch.setattr(guard_mod, "get_detector", lambda models: detector)


class TestRedactCodePii:
    """端到端：header 全量脱敏 + 正文 regex/凭据脱敏，import/标识符不动。"""

    @pytest.mark.asyncio
    async def test_header_full_body_tokens_only(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """S2：header 全量（邮箱掉），正文 tokens_only（邮箱留、高置信 token 掉）。

        正文不跑全量正则以防改坏代码（§4.2）；import 路径不被误伤。
        """
        _patch_detector(monkeypatch, _FakeDetector())
        src = _build_source(
            "// Copyright 2024 ACME\n"
            "// Author: alice@acme.com\n"
            "\n"
            "#include \"third_party/acme/headers.h\"\n"
            'const char* k = "sk-abcdefghijklmnopqrstuvwxyz0123";\n'
            "// runtime contact: bob@acme.com\n",
        )
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)  # 跳过 __init__；只测 _redact_code_pii
        await pipe._redact_code_pii([src], pii_cfg)
        # header 邮箱：full → 脱
        assert "alice@acme.com" not in src.merged_text
        # 正文高置信 token：tokens_only → 脱
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in src.merged_text
        assert pii_cfg.credential_placeholder in src.merged_text
        # 正文邮箱：tokens_only 不脱（§4.2 取舍，保正文不被改坏）
        assert "bob@acme.com" in src.merged_text
        # import 路径不被结构化 regex 误伤
        assert "third_party/acme/headers.h" in src.merged_text

    @pytest.mark.asyncio
    async def test_body_token_redacted_code_expr_intact(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """S2 正文 tokens_only：高置信 token 脱；KV 代码表达式/标识符不被改坏。

        正文不跑 KV 正则（否则 ``password = get_secret()`` 右侧被吞坏代码，§4.2）——
        代价是字符串字面量密码不脱，属「稳一点」取舍。
        """
        _patch_detector(monkeypatch, _FakeDetector())
        src = _build_source(
            "// header\n"
            "const char* password = get_secret();\n"
            'std::string apikey = "sk-abcdefghijklmnopqrstuvwxyz0123";\n'
            "namespace acme { int Zhang = 1; }\n",
        )
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg)
        # 高置信 token 被替换
        assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in src.merged_text
        assert pii_cfg.credential_placeholder in src.merged_text
        # 核心 S2 收益：KV 代码表达式不被改坏，get_secret() 原样保留
        assert "password = get_secret();" in src.merged_text
        # 正文不做实体脱敏（无 lexicon）：namespace / 变量名保留（AGE-50）
        assert "namespace acme" in src.merged_text
        assert "Zhang" in src.merged_text

    @pytest.mark.asyncio
    async def test_lexicon_only_from_headers(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本地 NER 检测到的公司名只对 header 替换，不污染正文 import 路径。"""
        _patch_detector(monkeypatch, _FakeDetector(orgs=("XuanTie",)))
        src = _build_source(
            "// Copyright 2024 XuanTie\n"
            "import(\"//third_party/xuantie_ext/options.gni\")\n",
        )
        pii_cfg = PIIConfig(enable=True, redact_org_name=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg)
        # header XuanTie 被替换
        assert "Copyright 2024 XuanTie" not in src.merged_text
        # body import 路径里的 xuantie_ext 不动（lexicon 只跑在 header 上）
        assert "xuantie_ext" in src.merged_text

    @pytest.mark.asyncio
    async def test_header_structured_pii_masked_before_detect(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """#36：header 拼 combined 送本地 NER 检测**前**，结构化 PII（邮箱/手机）
        已被 regex 脱敏；人名不被 regex 触及，仍保留供实体检测。
        """
        captured: list[str] = []
        _patch_detector(
            monkeypatch, _FakeDetector(persons=("张三",), captured=captured),
        )
        src = _build_source(
            "// Copyright 2024 ACME\n"
            "// Author: 张三 <zhangsan@acme.com> 13800138000\n"
            "int x = 1;\n",
        )
        pii_cfg = PIIConfig(enable=True, redact_person_name=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg)

        # 送本地 NER 的文本里已无明文邮箱/手机（检测前已结构化脱敏）
        assert len(captured) == 1
        sent = captured[0]
        assert "zhangsan@acme.com" not in sent
        assert "13800138000" not in sent
        # 已替换为请求级 PIIConfig 占位符（派生自配置，非硬编码字面量）
        assert pii_cfg.email_placeholder in sent
        # 人名未被结构化 regex 触及，仍在送检文本里（实体检测据此工作）
        assert "张三" in sent

    @pytest.mark.asyncio
    async def test_disabled_short_circuit(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_detector(monkeypatch, _FakeDetector())
        src = _build_source("// alice@acme.com\nint x = 1;\n")
        original = src.merged_text
        pii_cfg = PIIConfig(enable=False)
        # enable=False 时调用方过滤，但本方法 defensive：不应崩
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg)
        # 只验证不抛异常；调用方过滤是 _code_pipeline 的职责
        assert isinstance(src.merged_text, str)
        del original  # 避免 unused

    @pytest.mark.asyncio
    async def test_no_header_no_pii_unchanged(self) -> None:
        """无 leading comment 且正文无 PII → 不应改任何字符（正文 regex 空转）。

        无 header → combined 为空 → 不触发实体检测，故无需注入 detector。
        """
        src = _build_source("int x = 1;\nint y = 2;\n")
        original = src.merged_text
        pii_cfg = PIIConfig(enable=True)
        pipe = Pipeline.__new__(Pipeline)
        await pipe._redact_code_pii([src], pii_cfg)
        assert src.merged_text == original


class TestRedactCodePiiFailClosed:
    """#25：检测失败 + block_cloud_on_detect_failure → 返回 block_cloud 阻断云端。"""

    @pytest.mark.asyncio
    async def test_detect_failure_returns_block_true(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本地 NER 检测抛错 + flag 默认 True → 返回 True（调用方跳过云端精修）。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=True,
        )
        _patch_detector(
            monkeypatch,
            _FakeDetector(raises=NERUnavailableError("spacy unavailable")),
        )
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii([src], pii_cfg)
        assert block is True

    @pytest.mark.asyncio
    async def test_detect_failure_flag_off_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """检测抛错但 flag 关 → 返回 False（保持旧行为，不阻断）。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=False,
        )
        _patch_detector(
            monkeypatch,
            _FakeDetector(raises=NERUnavailableError("spacy unavailable")),
        )
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii([src], pii_cfg)
        assert block is False

    @pytest.mark.asyncio
    async def test_detect_success_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """检测成功 → 返回 False（不阻断，照常云端精修）。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True,
            block_cloud_on_detect_failure=True,
        )
        _patch_detector(monkeypatch, _FakeDetector(orgs=("someone",)))
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii([src], pii_cfg)
        assert block is False

    @pytest.mark.asyncio
    async def test_backend_none_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ner_backend="none"（知情放弃）→ 返回 False，且检测器不被调用。"""
        src = _build_source("// Copyright 2024 ACME\nint x = 1;\n")
        pii_cfg = PIIConfig(
            enable=True, redact_org_name=True, ner_backend="none",
            block_cloud_on_detect_failure=True,
        )
        # detector 抛错也无所谓：backend=none 应在调用 detector 前就返回 None
        _patch_detector(
            monkeypatch,
            _FakeDetector(raises=NERUnavailableError("不应被调用")),
        )
        pipe = Pipeline.__new__(Pipeline)
        block = await pipe._redact_code_pii([src], pii_cfg)
        assert block is False
