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

import pytest

from docrestore.pipeline.config import (
    CustomWord,
    DedupConfig,
    LLMConfig,
    OCRConfig,
    OutputConfig,
    PIIConfig,
    PipelineConfig,
)


class TestOCRConfig:
    """OCRConfig 默认值和覆盖测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        cfg = OCRConfig()
        assert cfg.model == "paddle-ocr/ppocr-v4"
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
        assert cfg.max_chars_per_segment == 8000
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
        assert cfg.ocr.model == "paddle-ocr/ppocr-v4"
        assert cfg.output.image_format == "jpg"

    def test_llm_max_concurrent_requests_default(self) -> None:
        """LLMConfig.max_concurrent_requests 默认 3。

        取代旧 QueueConfig.max_concurrent_pipelines。
        """
        cfg = PipelineConfig()
        assert cfg.llm.max_concurrent_requests == 3

    def test_override_llm_max_concurrent_requests(self) -> None:
        """覆盖 LLMConfig.max_concurrent_requests"""
        cfg = PipelineConfig(
            llm=LLMConfig(max_concurrent_requests=1),
        )
        assert cfg.llm.max_concurrent_requests == 1

    def test_independent_instances(self) -> None:
        """不同实例的子配置互不影响"""
        cfg1 = PipelineConfig()
        cfg2 = PipelineConfig()
        cfg1.llm.model = "model-a"
        assert cfg2.llm.model == ""


class TestLLMConfigExtra:
    """LLMConfig 补充字段覆盖"""

    def test_provider_default_cloud(self) -> None:
        cfg = LLMConfig()
        assert cfg.provider == "cloud"

    def test_provider_local(self) -> None:
        cfg = LLMConfig(provider="local")
        assert cfg.provider == "local"

    def test_enable_final_refine_default_true(self) -> None:
        cfg = LLMConfig()
        assert cfg.enable_final_refine is True

    def test_enable_gap_fill_default_true(self) -> None:
        cfg = LLMConfig()
        assert cfg.enable_gap_fill is True

    def test_disable_final_refine_and_gap_fill(self) -> None:
        cfg = LLMConfig(
            enable_final_refine=False, enable_gap_fill=False,
        )
        assert cfg.enable_final_refine is False
        assert cfg.enable_gap_fill is False

    def test_timeout_default(self) -> None:
        assert LLMConfig().timeout == 600

    def test_segment_overlap_lines_override(self) -> None:
        cfg = LLMConfig(segment_overlap_lines=10)
        assert cfg.segment_overlap_lines == 10


class TestDedupConfigExtra:
    """DedupConfig 跨页频率过滤字段"""

    def test_repeated_line_defaults(self) -> None:
        cfg = DedupConfig()
        assert cfg.repeated_line_threshold == 0.5
        assert cfg.repeated_line_min_pages == 4
        assert cfg.repeated_line_min_block == 3

    def test_override_repeated_line_fields(self) -> None:
        cfg = DedupConfig(
            repeated_line_threshold=0.8,
            repeated_line_min_pages=10,
            repeated_line_min_block=5,
        )
        assert cfg.repeated_line_threshold == 0.8
        assert cfg.repeated_line_min_pages == 10
        assert cfg.repeated_line_min_block == 5


class TestOCRConfigExtra:
    """OCRConfig 侧栏过滤与 GPU 字段"""

    def test_column_filter_defaults_off(self) -> None:
        cfg = OCRConfig()
        assert cfg.enable_column_filter is False
        assert cfg.column_filter_min_sidebar == 5

    def test_enable_column_filter(self) -> None:
        cfg = OCRConfig(
            enable_column_filter=True, column_filter_min_sidebar=10,
        )
        assert cfg.enable_column_filter is True
        assert cfg.column_filter_min_sidebar == 10

    def test_gpu_id_default(self) -> None:
        # 默认 None → engine_manager 启动时调 gpu_detect.pick_best_gpu 选显存最大的
        assert OCRConfig().gpu_id is None

    def test_gpu_id_explicit(self) -> None:
        assert OCRConfig(gpu_id="0").gpu_id == "0"

    def test_paddle_server_url_default_empty(self) -> None:
        assert OCRConfig().paddle_server_url == ""


class TestPIIConfig:
    """PIIConfig 完整覆盖（全新）"""

    def test_defaults_disabled(self) -> None:
        """默认 enable=False，默认所有 redact 开关为 True。"""
        cfg = PIIConfig()
        assert cfg.enable is False
        assert cfg.redact_phone is True
        assert cfg.redact_email is True
        assert cfg.redact_id_card is True
        assert cfg.redact_bank_card is True
        assert cfg.redact_person_name is True
        assert cfg.redact_org_name is True

    def test_default_placeholders(self) -> None:
        cfg = PIIConfig()
        assert cfg.phone_placeholder == "[手机号]"
        assert cfg.email_placeholder == "[邮箱]"
        assert cfg.id_card_placeholder == "[身份证号]"
        assert cfg.bank_card_placeholder == "[银行卡号]"
        assert cfg.person_name_placeholder == "[人名]"
        assert cfg.org_name_placeholder == "[机构名]"

    def test_custom_sensitive_words_empty_by_default(self) -> None:
        cfg = PIIConfig()
        assert cfg.custom_sensitive_words == []
        assert cfg.custom_words_placeholder == "[敏感词]"

    def test_custom_sensitive_words_accepts_list(self) -> None:
        cfg = PIIConfig(
            custom_sensitive_words=[
                CustomWord(word="项目A", code="[P-A]"),
                CustomWord(word="客户B"),
            ],
        )
        assert len(cfg.custom_sensitive_words) == 2
        assert cfg.custom_sensitive_words[0].word == "项目A"
        assert cfg.custom_sensitive_words[0].code == "[P-A]"
        assert cfg.custom_sensitive_words[1].code == ""

    def test_block_cloud_on_detect_failure_default_true(self) -> None:
        assert PIIConfig().block_cloud_on_detect_failure is True

    def test_selective_disable(self) -> None:
        """可以只关闭其中部分脱敏类别。"""
        cfg = PIIConfig(
            enable=True,
            redact_phone=True,
            redact_email=False,
            redact_id_card=True,
            redact_person_name=False,
        )
        assert cfg.enable is True
        assert cfg.redact_email is False
        assert cfg.redact_person_name is False
        # 未显式改的保持默认
        assert cfg.redact_bank_card is True


class TestCustomWord:
    """CustomWord 行为（frozen + 可哈希）"""

    def test_defaults_empty_code(self) -> None:
        w = CustomWord(word="机密A")
        assert w.word == "机密A"
        assert w.code == ""

    def test_is_frozen(self) -> None:
        """frozen=True：不能修改字段。"""
        w = CustomWord(word="x")
        with pytest.raises((TypeError, ValueError)):
            w.word = "y"

    def test_is_hashable(self) -> None:
        """frozen 模型可放入 set / dict key。"""
        w1 = CustomWord(word="A", code="[A]")
        w2 = CustomWord(word="A", code="[A]")
        # 相同字段应视为等值
        assert w1 == w2
        # 可放入集合
        assert len({w1, w2}) == 1


class TestPipelineConfigPII:
    """PipelineConfig.pii 嵌套 + JSON 往返"""

    def test_pii_nested_default(self) -> None:
        cfg = PipelineConfig()
        assert isinstance(cfg.pii, PIIConfig)
        assert cfg.pii.enable is False

    def test_override_pii(self) -> None:
        cfg = PipelineConfig(
            pii=PIIConfig(enable=True, redact_email=False),
        )
        assert cfg.pii.enable is True
        assert cfg.pii.redact_email is False

    def test_json_round_trip(self) -> None:
        """model_dump_json → model_validate_json 往返不丢字段。"""
        original = PipelineConfig(
            llm=LLMConfig(
                model="openai/glm-5",
                enable_gap_fill=False,
            ),
            pii=PIIConfig(
                enable=True,
                custom_sensitive_words=[
                    CustomWord(word="项目X", code="[X]"),
                ],
            ),
            db_path="/tmp/x.db",  # noqa: S108
            debug=False,
        )
        js = original.model_dump_json()
        loaded = PipelineConfig.model_validate_json(js)

        assert loaded.llm.model == "openai/glm-5"
        assert loaded.llm.enable_gap_fill is False
        assert loaded.pii.enable is True
        assert loaded.pii.custom_sensitive_words[0].word == "项目X"
        assert loaded.pii.custom_sensitive_words[0].code == "[X]"
        assert loaded.db_path == "/tmp/x.db"  # noqa: S108
        assert loaded.debug is False

    def test_db_path_default(self) -> None:
        assert PipelineConfig().db_path == "data/docrestore.db"

    def test_debug_default(self) -> None:
        assert PipelineConfig().debug is True
