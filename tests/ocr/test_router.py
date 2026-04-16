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

"""OCR Router 单元测试"""

import pytest

from docrestore.ocr.router import _parse_model, create_engine
from docrestore.pipeline.config import OCRConfig


class TestParseModel:
    """测试模型标识符解析"""

    def test_parse_with_provider_and_model(self) -> None:
        """解析完整格式"""
        provider, model = _parse_model("paddle-ocr/ppocr-v4")
        assert provider == "paddle-ocr"
        assert model == "ppocr-v4"

    def test_parse_provider_only(self) -> None:
        """只有 provider，使用默认模型"""
        provider, model = _parse_model("paddle-ocr")
        assert provider == "paddle-ocr"
        assert model == "ppocr-v4"

    def test_parse_deepseek_with_model(self) -> None:
        """解析 DeepSeek 完整格式"""
        provider, model = _parse_model("deepseek/ocr-2")
        assert provider == "deepseek"
        assert model == "ocr-2"

    def test_parse_deepseek_provider_only(self) -> None:
        """只有 deepseek provider，使用默认模型"""
        provider, model = _parse_model("deepseek")
        assert provider == "deepseek"
        assert model == "ocr-2"


class TestCreateEngine:
    """测试引擎创建"""

    def test_create_paddle_ocr_engine(self) -> None:
        """创建 PaddleOCR 引擎"""
        config = OCRConfig(model="paddle-ocr/ppocr-v4")
        engine = create_engine("paddle-ocr/ppocr-v4", config)
        assert engine is not None

    def test_create_deepseek_engine(self) -> None:
        """创建 DeepSeek-OCR-2 引擎"""
        config = OCRConfig(model="deepseek/ocr-2")
        engine = create_engine("deepseek/ocr-2", config)
        assert engine is not None

    def test_create_deepseek_with_provider_only(self) -> None:
        """只指定 deepseek provider，使用默认模型"""
        config = OCRConfig(model="deepseek")
        engine = create_engine("deepseek", config)
        assert engine is not None

    def test_unsupported_provider(self) -> None:
        """不支持的 provider 抛出异常"""
        config = OCRConfig(model="unknown/model")
        with pytest.raises(ValueError, match="不支持的 OCR provider"):
            create_engine("unknown/model", config)
