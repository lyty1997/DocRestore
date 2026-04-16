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

"""OCR 路由器：统一多引擎调用接口（类似 litellm）"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docrestore.ocr.base import OCREngine
    from docrestore.pipeline.config import OCRConfig


def _parse_model(model: str) -> tuple[str, str]:
    """解析模型标识符

    Args:
        model: 格式 "provider/model-name" 或 "provider"

    Returns:
        (provider, model_name)

    Examples:
        "paddle-ocr/ppocr-v4" -> ("paddle-ocr", "ppocr-v4")
        "deepseek/ocr-2" -> ("deepseek", "ocr-2")
        "paddle-ocr" -> ("paddle-ocr", "ppocr-v4")
        "deepseek" -> ("deepseek", "ocr-2")
    """
    if "/" in model:
        provider, model_name = model.split("/", 1)
    else:
        provider = model
        # 根据 provider 设置默认模型
        if provider == "deepseek":
            model_name = "ocr-2"
        else:
            model_name = "ppocr-v4"
    return provider, model_name


def create_engine(model: str, config: OCRConfig) -> OCREngine:
    """创建 OCR 引擎实例

    Args:
        model: 模型标识符，格式 "provider/model-name"
        config: OCR 配置对象

    Returns:
        OCR 引擎实例

    Raises:
        ValueError: 不支持的 provider
    """
    provider, _model_name = _parse_model(model)

    if provider == "paddle-ocr":
        from docrestore.ocr.paddle_ocr import PaddleOCREngine
        return PaddleOCREngine(config)

    if provider in ("deepseek", "deepseek-ocr-2"):
        from docrestore.ocr.deepseek_ocr2 import DeepSeekOCR2Engine
        return DeepSeekOCR2Engine(config)

    msg = f"不支持的 OCR provider: {provider}"
    raise ValueError(msg)
