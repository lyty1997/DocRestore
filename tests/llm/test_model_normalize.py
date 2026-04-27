# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""litellm provider 兜底归一测试。

回归用户实测 bug：UI 填 ``model=deepseek-v4-flash`` +
``base_url=https://api.deepseek.com``，litellm 抛 BadRequestError 因为
没有 provider 前缀。归一函数要在 ``api_base`` 非空时自动加 ``openai/``，
让所有 OpenAI 兼容协议（DeepSeek、GLM、中转站、vLLM）开箱即用。
"""

from __future__ import annotations

from docrestore.llm.base import _normalize_model_id


class TestNormalizeModelId:
    """_normalize_model_id 行为约束。"""

    def test_litellm_known_provider_prefix_passthrough(self) -> None:
        """已显式带 litellm 内置 provider 前缀的，原样不动。"""
        assert _normalize_model_id(
            "openai/gpt-4o", "https://proxy.example.com",
        ) == "openai/gpt-4o"
        assert _normalize_model_id(
            "deepseek/deepseek-chat", "",
        ) == "deepseek/deepseek-chat"
        assert _normalize_model_id(
            "anthropic/claude-sonnet-4", "",
        ) == "anthropic/claude-sonnet-4"

    def test_custom_model_with_api_base_prepends_openai(self) -> None:
        """用户填厂商自有模型名 + 自定义 api_base → 加 openai/ 前缀。

        覆盖实测 bug：DeepSeek 中转的 ``deepseek-v4-flash``、
        中转站 GLM ``glm-5-air`` 等 litellm 无内置识别的 model id。
        """
        assert _normalize_model_id(
            "deepseek-v4-flash", "https://api.deepseek.com",
        ) == "openai/deepseek-v4-flash"
        assert _normalize_model_id(
            "glm-5-air", "https://open.bigmodel.cn/api/paas/v4",
        ) == "openai/glm-5-air"

    def test_no_api_base_no_prefix(self) -> None:
        """api_base 为空 → 不前缀，让 litellm 按 model 名 fallback。

        env vars 路径（OPENAI_API_KEY 等）不应被本归一干扰。
        """
        assert _normalize_model_id(
            "gpt-4o-mini", "",
        ) == "gpt-4o-mini"

    def test_empty_model_passthrough(self) -> None:
        """空字符串原样返回，留给上层 LLMConfig 校验。"""
        assert _normalize_model_id("", "https://x") == ""
        assert _normalize_model_id("", "") == ""
