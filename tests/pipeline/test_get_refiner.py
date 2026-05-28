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

"""Pipeline._get_refiner 空 model 跳过测试（2026-04-24 回归）。

背景：请求级 llm 覆盖传入 `LLMConfig(model="", ...)` 时，历史逻辑会无条件
`_create_refiner(llm)` 并把空 model 塞给 litellm，导致 15 次 refine/gap_fill
调用全部抛 BadRequestError，stderr 刷屏 "Provider List: https://docs.litellm.ai/
docs/providers"。下游调用点都已做 `if refiner is None: 跳过`，这里只需在
_get_refiner 把 model 空的请求同样当作"禁用 LLM"返回 None。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline


def _make_pipeline(default_model: str = "") -> tuple[Pipeline, MagicMock]:
    """构造 Pipeline 实例但跳过 initialize/引擎注入（单元测试不需要 IO）。

    返回 (pipeline, _create_refiner mock) —— mock 单独返回方便断言调用次数
    且不触发 mypy 对实例方法动态赋值的类型投诉。
    """
    pipe: Pipeline = Pipeline.__new__(Pipeline)
    pipe._config = PipelineConfig(llm=LLMConfig(model=default_model))
    pipe._refiner = None
    create_mock = MagicMock(return_value="REFINER_INSTANCE")
    # _create_refiner 的真实调用会构造 CloudLLMRefiner，测试用 stub 替换
    cast(Any, pipe)._create_refiner = create_mock
    return pipe, create_mock


class TestGetRefiner:
    """_get_refiner 的四条路径。"""

    def test_llm_none_returns_default_refiner(self) -> None:
        """llm=None：复用默认 refiner（此处 None 因 default_model=""）。"""
        pipe, create_mock = _make_pipeline(default_model="")
        assert pipe._get_refiner(None) is None
        create_mock.assert_not_called()

    def test_llm_none_with_configured_default(self) -> None:
        """默认 refiner 已存在时 llm=None 返回它，不重建。"""
        pipe, create_mock = _make_pipeline(default_model="openai/glm-5")
        cast(Any, pipe)._refiner = "DEFAULT"
        assert cast(Any, pipe._get_refiner(None)) == "DEFAULT"
        create_mock.assert_not_called()

    def test_llm_empty_model_returns_none(self) -> None:
        """llm 非空但 model="" → 返回 None，不走 _create_refiner。

        这是防 15 次 Provider List 刷屏 bug 的核心断言。
        """
        pipe, create_mock = _make_pipeline()
        result = pipe._get_refiner(
            LLMConfig(model="", api_base="https://example.com/v1"),
        )
        assert result is None
        create_mock.assert_not_called()

    def test_llm_real_model_builds_refiner(self) -> None:
        """llm 有效 model → 调 _create_refiner 构造请求级快照。"""
        pipe, create_mock = _make_pipeline()
        result = pipe._get_refiner(
            LLMConfig(model="openai/glm-5", api_key="k"),
        )
        assert cast(Any, result) == "REFINER_INSTANCE"
        create_mock.assert_called_once()
