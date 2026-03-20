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

"""FastAPI 应用创建 + 生命周期管理"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from docrestore.api.routes import router, set_task_manager
from docrestore.ocr.base import OCREngine
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

logger = logging.getLogger(__name__)


def _create_ocr_engine(config: PipelineConfig) -> OCREngine:
    """根据配置创建 OCR 引擎。

    当前仅支持 engine == "deepseek-ocr-2"。

    注意：不再提供测试用 OCR 引擎回退。若 DeepSeek-OCR-2 依赖缺失，
    会直接抛出 ImportError，以便在启动时尽早失败。
    （测试请在 tests/ 侧通过 pipeline.set_ocr_engine() 注入测试引擎。）
    """
    if config.ocr.engine != "deepseek-ocr-2":
        msg = f"不支持的 OCR 引擎: {config.ocr.engine}"
        raise ValueError(msg)

    from docrestore.ocr.deepseek_ocr2 import DeepSeekOCR2Engine

    logger.info("使用 DeepSeek-OCR-2 引擎")
    return DeepSeekOCR2Engine(config.ocr)


def create_app(
    config: PipelineConfig | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    config 为 None 时使用默认配置。
    OCR 引擎优先通过 app.state.ocr_engine 注入，
    否则根据 config.ocr.engine 自动创建。
    """
    if config is None:
        config = PipelineConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """启动时初始化 Pipeline，关闭时释放资源"""
        pipeline = Pipeline(config)

        # 优先使用外部注入的引擎，否则根据配置自动创建
        engine = getattr(app.state, "ocr_engine", None)
        if engine is None:
            engine = _create_ocr_engine(config)
        pipeline.set_ocr_engine(engine)

        try:
            await pipeline.initialize()
        except Exception:
            logger.exception("Pipeline 初始化失败")
            await pipeline.shutdown()
            raise

        manager = TaskManager(pipeline)
        set_task_manager(manager)
        app.state.task_manager = manager

        yield

        await pipeline.shutdown()

    app = FastAPI(
        title="DocRestore",
        version="0.1.0",
        lifespan=lifespan,
        redirect_slashes=False,
    )
    app.include_router(router, prefix="/api/v1")
    return app
