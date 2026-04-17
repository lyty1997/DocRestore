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

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from importlib.metadata import version as pkg_version

from fastapi import Depends, FastAPI

from docrestore.api.auth import configure_auth, require_auth
from docrestore.api.routes import router, set_task_manager, ws_router
from docrestore.api.upload import (
    cleanup_all_sessions,
    start_cleanup_task,
    upload_router,
)
from docrestore.ocr.engine_manager import EngineManager
from docrestore.persistence.database import TaskDatabase
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.scheduler import PipelineScheduler
from docrestore.pipeline.task_manager import TaskManager

logger = logging.getLogger(__name__)

_CONDA_DETECT_TIMEOUT_SECONDS = 10


def _detect_conda_python(env_name: str) -> str:
    """自动检测 conda 环境的 python 路径。"""
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 — conda_bin 来自 shutil.which，可信
            [conda_bin, "run", "-n", env_name, "which", "python"],
            capture_output=True, text=True,
            timeout=_CONDA_DETECT_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _auto_configure_paddle(config: PipelineConfig) -> None:
    """PaddleOCR 相关配置自动填充。

    - paddle_python：从 ppocr_client conda 环境自动检测
    - paddle_server_python：从 ppocr_vlm conda 环境自动检测
    - gpu_id / paddle_server_port：从环境变量读取（与 start.sh 一致）
    - paddle_server_url：根据 paddle_server_port 自动构造
    """
    if not config.ocr.paddle_python:
        detected = _detect_conda_python("ppocr_client")
        if detected:
            config.ocr.paddle_python = detected
            logger.info("自动检测 PaddleOCR python: %s", detected)

    if not config.ocr.paddle_server_python:
        detected = _detect_conda_python("ppocr_vlm")
        if detected:
            config.ocr.paddle_server_python = detected
            logger.info("自动检测 ppocr_vlm python: %s", detected)

    # 从环境变量读取 GPU / 端口（与 start.sh 默认值一致）
    env_gpu = os.environ.get("PPOCR_GPU_ID", "")
    if env_gpu:
        config.ocr.gpu_id = env_gpu
        logger.info("从环境变量 PPOCR_GPU_ID 配置 GPU: %s", env_gpu)

    env_port = os.environ.get("PPOCR_PORT", "")
    if env_port:
        config.ocr.paddle_server_port = int(env_port)
        logger.info("从环境变量 PPOCR_PORT 配置端口: %s", env_port)

    if not config.ocr.paddle_server_url:
        config.ocr.paddle_server_url = (
            config.ocr.build_default_paddle_server_url()
        )
        logger.info(
            "自动配置 PaddleOCR server URL: %s",
            config.ocr.paddle_server_url,
        )


def _auto_configure_deepseek(config: PipelineConfig) -> None:
    """DeepSeek-OCR-2 相关配置自动填充。

    - deepseek_python：从 deepseek_ocr conda 环境自动检测
    """
    if not config.ocr.deepseek_python:
        detected = _detect_conda_python("deepseek_ocr")
        if detected:
            config.ocr.deepseek_python = detected
            logger.info("自动检测 DeepSeek python: %s", detected)


def _auto_configure_llm(config: PipelineConfig) -> None:
    """从环境变量自动填充 LLM 配置。

    支持的环境变量：
    - DOCRESTORE_LLM_MODEL：litellm 模型名（如 openai/gpt-4o）
    - DOCRESTORE_LLM_API_BASE：自定义 API 地址
    - DOCRESTORE_LLM_API_KEY：API 密钥
    """
    if config.llm.model:
        return  # 已有配置，不覆盖

    model = os.environ.get("DOCRESTORE_LLM_MODEL", "")
    if model:
        config.llm.model = model
        logger.info("从环境变量配置 LLM model: %s", model)

    api_base = os.environ.get("DOCRESTORE_LLM_API_BASE", "")
    if api_base and not config.llm.api_base:
        config.llm.api_base = api_base
        logger.info("从环境变量配置 LLM api_base: %s", api_base)

    api_key = os.environ.get("DOCRESTORE_LLM_API_KEY", "")
    if api_key and not config.llm.api_key:
        config.llm.api_key = api_key
        logger.info("已从环境变量读取 LLM api_key")

    if not config.llm.model:
        logger.warning(
            "未配置 LLM model（设置环境变量 DOCRESTORE_LLM_MODEL "
            "或在请求中传入 llm.model），将跳过 LLM 精修"
        )



def create_app(
    config: PipelineConfig | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    config 为 None 时使用默认配置。
    OCR 引擎优先通过 app.state.ocr_engine 注入，
    否则根据 config.ocr.model 自动创建。
    """
    if config is None:
        config = PipelineConfig()

    _auto_configure_paddle(config)
    _auto_configure_deepseek(config)
    _auto_configure_llm(config)
    configure_auth(os.environ.get("DOCRESTORE_API_TOKEN", ""))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """启动时初始化 Pipeline + Scheduler，关闭时释放资源"""
        pipeline = Pipeline(config)

        # 创建全局调度器
        scheduler = PipelineScheduler(
            max_concurrent_llm_requests=config.llm.max_concurrent_requests,
        )

        # 全局 LLM 限流 semaphore 必须在 initialize() 之前注入，
        # 否则默认 refiner 不会带上 semaphore，跨任务并发不受控。
        pipeline.set_llm_semaphore(scheduler.llm_semaphore)

        # 优先使用外部注入的引擎（测试用），否则使用 EngineManager
        engine = getattr(app.state, "ocr_engine", None)
        engine_manager: EngineManager | None = None
        if engine is not None:
            pipeline.set_ocr_engine(engine)
        else:
            engine_manager = EngineManager(config.ocr, scheduler.gpu_lock)
            pipeline.set_engine_manager(engine_manager)

        try:
            await pipeline.initialize()
        except Exception:
            logger.exception("Pipeline 初始化失败")
            await pipeline.shutdown()
            raise

        # 后台预热默认 OCR 引擎（尽力而为，不阻塞服务启动）
        warmup_task: asyncio.Task[None] | None = None
        if engine_manager is not None:

            async def _warmup_default_engine() -> None:
                try:
                    logger.info(
                        "后台预热默认 OCR 引擎: %s (GPU %s)",
                        config.ocr.model, config.ocr.gpu_id,
                    )
                    await engine_manager.ensure()
                    logger.info("默认 OCR 引擎预热完成")
                except Exception:
                    logger.warning(
                        "默认 OCR 引擎预热失败（不影响服务）",
                        exc_info=True,
                    )

            warmup_task = asyncio.create_task(_warmup_default_engine())

        # 初始化持久化层
        db = TaskDatabase(config.db_path)
        await db.initialize()

        manager = TaskManager(pipeline, scheduler=scheduler, db=db)
        set_task_manager(manager)
        app.state.task_manager = manager
        app.state.engine_manager = engine_manager
        app.state.scheduler = scheduler
        app.state.db = db

        # 启动上传会话清理后台任务
        cleanup_task = await start_cleanup_task()

        yield

        # 取消未完成的预热任务
        if warmup_task is not None and not warmup_task.done():
            warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warmup_task

        # 先 cancel 运行中的 OCR 任务，避免它们与 pipeline.shutdown 并发
        # 抢占 worker stdin/stdout（会导致 StreamReader 冲突或长时间阻塞）
        await manager.shutdown()

        cleanup_task.cancel()
        cleanup_all_sessions()
        await pipeline.shutdown()
        await db.close()

    app = FastAPI(
        title="DocRestore",
        version=pkg_version("docrestore"),
        lifespan=lifespan,
        redirect_slashes=False,
    )
    _auth_deps = [Depends(require_auth)]
    app.include_router(router, prefix="/api/v1", dependencies=_auth_deps)
    app.include_router(
        upload_router, prefix="/api/v1", dependencies=_auth_deps,
    )
    # WebSocket 路由单独注册，不挂 HTTPBearer 认证（WS 用 require_auth_ws）
    app.include_router(ws_router, prefix="/api/v1")
    return app
