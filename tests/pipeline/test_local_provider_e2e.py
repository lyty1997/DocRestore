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

"""LocalLLM provider 全链路测试

验证 `LLMConfig.provider="local"` 经 Pipeline → _create_refiner
→ LocalLLMRefiner → litellm.acompletion 的完整链路：

- _create_refiner 在 provider="local" 时返回 LocalLLMRefiner 实例
- Pipeline.process_many 完整跑通（refine/final_refine 调用 litellm）
- PII 启用时 Local 不做实体检测（LocalLLMRefiner.detect_pii_entities 空实现），
  regex 脱敏仍生效，不会阻断流水线
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.local import LocalLLMRefiner
from docrestore.models import PageOCR
from docrestore.pipeline.config import (
    LLMConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.pipeline import Pipeline


def _mock_response(content: str, finish: str = "stop") -> object:
    """litellm 风格的响应对象。"""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish,
            ),
        ],
    )


def _engine(texts: dict[str, str]) -> MagicMock:
    engine = MagicMock()

    async def _ocr(image_path: Path, _out: Path) -> PageOCR:
        text = texts.get(image_path.name, image_path.name)
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=text,
            cleaned_text=text,
        )

    engine.ocr = AsyncMock(side_effect=_ocr)
    engine.reocr_page = AsyncMock(return_value="")
    engine.shutdown = AsyncMock(return_value=None)
    return engine


def _build_image_dir(root: Path, names: list[str]) -> Path:
    img_dir = root / "imgs"
    img_dir.mkdir()
    for n in names:
        (img_dir / n).write_bytes(b"fake")
    return img_dir


class TestCreateRefinerDispatches:
    """_create_refiner 根据 provider 选择对应实现"""

    def _make_pipeline(self) -> Pipeline:
        from docrestore.pipeline.config import PipelineConfig

        return Pipeline(PipelineConfig())

    def test_local_provider_returns_local_refiner(self) -> None:
        cfg = LLMConfig(provider="local", model="ollama/x")
        refiner = self._make_pipeline()._create_refiner(cfg)
        assert isinstance(refiner, LocalLLMRefiner)
        assert not isinstance(refiner, CloudLLMRefiner)

    def test_cloud_provider_returns_cloud_refiner(self) -> None:
        cfg = LLMConfig(provider="cloud", model="openai/x")
        refiner = self._make_pipeline()._create_refiner(cfg)
        assert isinstance(refiner, CloudLLMRefiner)
        assert not isinstance(refiner, LocalLLMRefiner)

    def test_default_provider_is_cloud(self) -> None:
        cfg = LLMConfig(model="openai/x")  # provider 默认 "cloud"
        assert self._make_pipeline()._create_refiner(cfg).__class__.__name__ \
            == "CloudLLMRefiner"


class TestLocalProviderFullChain:
    """provider=local 时 Pipeline.process_many 全链路（litellm 被 mock）"""

    @pytest.mark.asyncio
    async def test_refine_goes_through_local_refiner(
        self, tmp_path: Path,
    ) -> None:
        """refine 阶段调用 litellm.acompletion → local refiner 产出 markdown。"""
        llm_cfg = LLMConfig(
            provider="local",
            model="ollama/qwen",
            api_base="http://localhost:11434/v1",
            enable_gap_fill=False,
            enable_final_refine=False,
        )
        cfg = PipelineConfig(llm=llm_cfg, pii=PIIConfig(enable=False))
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_engine({"a.jpg": "原始内容"}))
        # 注入由 Pipeline._create_refiner 产生的真 LocalLLMRefiner
        pipeline.set_refiner(pipeline._create_refiner(llm_cfg))

        img_dir = _build_image_dir(tmp_path, ["a.jpg"])

        # 打 patch 让 litellm 返回确定内容
        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_response("# 精修后\n内容OK"),
        ) as acompletion:
            results = await pipeline.process_many(
                img_dir, tmp_path / "out",
            )

        assert len(results) == 1
        # refine 产出进入最终 markdown
        assert "精修后" in results[0].markdown
        # 至少调用过一次 litellm（refine 段）
        assert acompletion.await_count >= 1
        # refiner 是 LocalLLMRefiner（而非 Cloud）
        assert isinstance(pipeline._refiner, LocalLLMRefiner)

    @pytest.mark.asyncio
    async def test_final_refine_uses_local_refiner(
        self, tmp_path: Path,
    ) -> None:
        """enable_final_refine=True 下 final_refine 也走 local refiner。"""
        llm_cfg = LLMConfig(
            provider="local",
            model="ollama/qwen",
            api_base="http://localhost:11434/v1",
            enable_gap_fill=False,
            enable_final_refine=True,
        )
        cfg = PipelineConfig(llm=llm_cfg, pii=PIIConfig(enable=False))
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_engine({"p.jpg": "段落1"}))
        pipeline.set_refiner(pipeline._create_refiner(llm_cfg))

        img_dir = _build_image_dir(tmp_path, ["p.jpg"])

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_response("final refined"),
        ) as acompletion:
            results = await pipeline.process_many(
                img_dir, tmp_path / "out",
            )

        assert "final refined" in results[0].markdown
        # refine + final_refine 两次
        assert acompletion.await_count >= 2


class TestLocalProviderWithPII:
    """Local provider 下 PII 行为：只做 regex，不阻断云端（云端本来就没）"""

    @pytest.mark.asyncio
    async def test_regex_pii_redacted_under_local(
        self, tmp_path: Path,
    ) -> None:
        """Local + PII enable=True → 手机号被替换，流水线不阻断。"""
        llm_cfg = LLMConfig(
            provider="local",
            model="ollama/qwen",
            api_base="http://localhost:11434/v1",
            enable_gap_fill=False,
            enable_final_refine=False,
        )
        cfg = PipelineConfig(
            llm=llm_cfg,
            pii=PIIConfig(
                enable=True,
                # Local 不做实体检测，避免误报阻断
                block_cloud_on_detect_failure=False,
            ),
        )
        pipeline = Pipeline(cfg)
        pipeline.set_ocr_engine(_engine({
            "p.jpg": "联系 13812345678 或 abc@test.com",
        }))
        pipeline.set_refiner(pipeline._create_refiner(llm_cfg))

        img_dir = _build_image_dir(tmp_path, ["p.jpg"])

        with patch(
            "docrestore.llm.base.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=lambda **kw: _mock_response(
                kw["messages"][-1]["content"]
                if kw.get("messages") else "OK",
            ),
        ):
            results = await pipeline.process_many(
                img_dir, tmp_path / "out",
            )

        md = results[0].markdown
        # 原始 PII 不应出现在 markdown 中
        assert "13812345678" not in md
        assert "abc@test.com" not in md
        # 脱敏记录落账
        assert len(results[0].redaction_records) > 0

    @pytest.mark.asyncio
    async def test_local_detect_pii_entities_returns_empty(
        self,
    ) -> None:
        """LocalLLMRefiner.detect_pii_entities 不产生实体 lexicon。"""
        refiner = LocalLLMRefiner(
            LLMConfig(provider="local", model="ollama/q"),
        )
        persons, orgs = await refiner.detect_pii_entities(
            "张三来自 ACME 公司",
        )
        assert persons == []
        assert orgs == []
