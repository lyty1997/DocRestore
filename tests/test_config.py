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

"""pipeline/config.py 配置 dataclass 单元测试"""

from docrestore.pipeline.config import (
    DedupConfig,
    LLMConfig,
    OCRConfig,
    OutputConfig,
    PipelineConfig,
)


class TestOCRConfig:
    """OCRConfig 默认值和覆盖测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        cfg = OCRConfig()
        assert cfg.engine == "deepseek-ocr-2"
        assert cfg.model_path == "models/DeepSeek-OCR-2"
        assert cfg.gpu_memory_utilization == 0.75
        assert cfg.max_model_len == 8192
        assert cfg.max_tokens == 8192
        assert cfg.base_size == 1024
        assert cfg.crop_size == 768
        assert cfg.max_crops == 6
        assert cfg.min_crops == 2
        assert cfg.ngram_size == 20
        assert cfg.ngram_window_size == 90
        assert cfg.ngram_whitelist_token_ids == {128821, 128822}
        assert "<image>" in cfg.prompt

    def test_override(self) -> None:
        """覆盖默认值"""
        cfg = OCRConfig(max_model_len=4096, max_crops=4)
        assert cfg.max_model_len == 4096
        assert cfg.max_crops == 4


class TestDedupConfig:
    """DedupConfig 默认值和覆盖测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        cfg = DedupConfig()
        assert cfg.similarity_threshold == 0.8
        assert cfg.overlap_context_lines == 3
        assert cfg.search_ratio == 0.7

    def test_override(self) -> None:
        """覆盖默认值"""
        cfg = DedupConfig(similarity_threshold=0.9)
        assert cfg.similarity_threshold == 0.9


class TestLLMConfig:
    """LLMConfig 默认值和覆盖测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        cfg = LLMConfig()
        assert cfg.model == ""
        assert cfg.api_base == ""
        assert cfg.api_key == ""
        assert cfg.max_chars_per_segment == 18000
        assert cfg.segment_overlap_lines == 5
        assert cfg.max_retries == 2

    def test_override(self) -> None:
        """覆盖默认值"""
        cfg = LLMConfig(
            model="openai/glm-5",
            api_base="https://poloai.top/v1",
            max_retries=3,
        )
        assert cfg.model == "openai/glm-5"
        assert cfg.max_retries == 3


class TestOutputConfig:
    """OutputConfig 默认值和覆盖测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        cfg = OutputConfig()
        assert cfg.image_format == "jpg"
        assert cfg.image_quality == 95

    def test_override(self) -> None:
        """覆盖默认值"""
        cfg = OutputConfig(image_format="png", image_quality=80)
        assert cfg.image_format == "png"
        assert cfg.image_quality == 80


class TestPipelineConfig:
    """PipelineConfig 嵌套结构测试"""

    def test_defaults(self) -> None:
        """默认嵌套子配置"""
        cfg = PipelineConfig()
        assert isinstance(cfg.ocr, OCRConfig)
        assert isinstance(cfg.dedup, DedupConfig)
        assert isinstance(cfg.llm, LLMConfig)
        assert isinstance(cfg.output, OutputConfig)

    def test_override_nested(self) -> None:
        """覆盖嵌套子配置"""
        cfg = PipelineConfig(
            llm=LLMConfig(model="openai/glm-5"),
            dedup=DedupConfig(similarity_threshold=0.7),
        )
        assert cfg.llm.model == "openai/glm-5"
        assert cfg.dedup.similarity_threshold == 0.7
        # 未覆盖的保持默认
        assert cfg.ocr.engine == "deepseek-ocr-2"
        assert cfg.output.image_format == "jpg"

    def test_independent_instances(self) -> None:
        """不同实例的子配置互不影响"""
        cfg1 = PipelineConfig()
        cfg2 = PipelineConfig()
        cfg1.llm.model = "model-a"
        assert cfg2.llm.model == ""
