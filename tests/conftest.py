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

"""测试配置和公共 fixture"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from docrestore.api.routes import router, set_task_manager, ws_router
from docrestore.api.upload import upload_router
from docrestore.ocr.base import OCR_RESULT_FILENAME
from docrestore.pipeline.config import PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import TaskManager

from .support.ocr_engine import FixtureOCREngine

# 测试数据根目录
TEST_IMAGES_ROOT = Path(__file__).parent.parent / "test_images"

# 支持的图片后缀（大小写不敏感）
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class WsTestEnv:
    """WebSocket 测试环境。"""

    client: TestClient
    manager: TaskManager


def get_test_image_path(directory: Path, stem: str) -> Path:
    """根据 stem 找到实际存在的图片路径。

    说明：Linux/macOS（默认）文件系统大小写敏感，`1.jpg` 与 `1.JPG`
    是两个不同的文件名。历史测试用例里大量使用 `.JPG`，因此这里做
    一层兼容：按常见后缀顺序尝试，返回第一个存在的文件。
    """
    candidates = [
        directory / f"{stem}.JPG",
        directory / f"{stem}.jpg",
        directory / f"{stem}.jpeg",
        directory / f"{stem}.png",
    ]
    for p in candidates:
        if p.exists():
            return p

    raise FileNotFoundError(
        f"测试图片不存在：stem={stem!r}, directory={directory}"
    )


def _find_test_image_dir() -> Path | None:
    """找到 test_images/ 下第一个含图片的目录。

    兼容两种布局：
    1) test_images/<子目录>/*.jpg
    2) test_images/*.jpg（根目录直接放图片）

    说明：为保持历史行为，优先选择“子目录”中的第一组图片；
    若子目录均无图片，再回退到根目录。
    """
    if not TEST_IMAGES_ROOT.exists():
        return None

    # 优先：test_images/ 下的子目录
    for sub in sorted(TEST_IMAGES_ROOT.iterdir()):
        if sub.is_dir():
            images = [
                p
                for p in sub.iterdir()
                if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
            ]
            if images:
                return sub

    # 回退：test_images/ 根目录直接放图片
    root_images = [
        p
        for p in TEST_IMAGES_ROOT.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    ]
    if root_images:
        return TEST_IMAGES_ROOT

    return None


def _find_test_images(directory: Path) -> list[Path]:
    """扫描目录下所有图片，排序返回"""
    return sorted(
        p
        for p in directory.iterdir()
        if p.suffix.lower() in _IMAGE_SUFFIXES
    )


def _get_test_stems(directory: Path | None) -> list[str]:
    """从图片文件名提取 stem 列表"""
    if directory is None:
        return []
    return [p.stem for p in _find_test_images(directory)]


def _has_gpu() -> bool:
    """检查是否有可用 GPU"""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _has_model() -> bool:
    """检查模型是否可加载（vllm 可 import 且 vendor 路径存在）"""
    try:
        import vllm  # noqa: F401
    except ImportError:
        return False
    try:
        root = Path(__file__).resolve().parent.parent
        vendor_code_dir = (
            root
            / "vendor"
            / "DeepSeek-OCR-2"
            / "DeepSeek-OCR2-master"
            / "DeepSeek-OCR2-vllm"
        )
        return vendor_code_dir.exists()
    except Exception:
        return False


# 模块级常量，供各测试文件 import
TEST_IMAGE_DIR = _find_test_image_dir()
TEST_STEMS = _get_test_stems(TEST_IMAGE_DIR)


def _has_ocr_data(
    directory: Path | None, stems: list[str], count: int = 4
) -> bool:
    """检查前 count 个 stem 的 OCR 目录是否都存在"""
    if not directory or not stems:
        return False
    return all(
        (directory / f"{s}_OCR" / OCR_RESULT_FILENAME).exists()
        for s in stems[:count]
    )


def prepare_work_dir(
    tmp_path: Path,
    require_ocr_data: Path,
    *,
    count: int = 4,
    copy_real_images: bool = False,
) -> Path:
    """准备测试工作目录。"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    stems = TEST_STEMS[:count]
    for stem in stems:
        src = require_ocr_data / f"{stem}_OCR"
        if src.exists():
            shutil.copytree(src, output_dir / f"{stem}_OCR")

        if copy_real_images:
            real_img = get_test_image_path(require_ocr_data, stem)
            shutil.copy2(real_img, input_dir / real_img.name)
        else:
            (input_dir / f"{stem}.JPG").write_bytes(b"fake")

    return tmp_path


async def wait_task_done(
    client: AsyncClient,
    task_id: str,
    *,
    max_attempts: int = 200,
    interval_seconds: float = 0.05,
) -> None:
    """轮询等待任务进入终态。"""
    for _ in range(max_attempts):
        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            return
        await asyncio.sleep(interval_seconds)

    raise AssertionError("任务未在预期时间内结束")


@pytest.fixture
def test_images_dir() -> Path | None:
    """测试图片目录"""
    return TEST_IMAGE_DIR


@pytest.fixture
def sample_ocr_dir() -> Path | None:
    """样例 OCR 输出目录（第一个 stem 的 _OCR 目录）"""
    if TEST_IMAGE_DIR and TEST_STEMS:
        d = TEST_IMAGE_DIR / f"{TEST_STEMS[0]}_OCR"
        if d.exists():
            return d
    return None


@pytest.fixture(scope="session")
async def ocr_data_dir() -> Path | None:
    """session 级 fixture：有 GPU 时自动跑 OCR 生成测试数据"""
    img_dir = _find_test_image_dir()
    if img_dir is None:
        return None

    images = _find_test_images(img_dir)[:4]  # 只取前 4 张
    stems = [p.stem for p in images]

    # 幂等：已有 OCR 数据则跳过
    all_exist = all(
        (img_dir / f"{s}_OCR" / OCR_RESULT_FILENAME).exists()
        for s in stems
    )
    if all_exist:
        return img_dir

    # 无 GPU 或无模型则放弃
    if not _has_gpu() or not _has_model():
        return None

    # 跑 OCR
    from docrestore.ocr.deepseek_ocr2 import DeepSeekOCR2Engine
    from docrestore.pipeline.config import OCRConfig

    engine = DeepSeekOCR2Engine(OCRConfig())
    await engine.initialize()
    try:
        await engine.ocr_batch(images, img_dir)
    finally:
        await engine.shutdown()

    return img_dir


@pytest.fixture
def require_ocr_data(
    ocr_data_dir: Path | None,
) -> Path:
    """依赖 ocr_data_dir，无数据时 skip"""
    if ocr_data_dir is None:
        pytest.skip("无 OCR 数据（无 GPU 或无图片）")
    return ocr_data_dir


@pytest.fixture
def work_dir(tmp_path: Path, require_ocr_data: Path) -> Path:
    """准备 API/WS 测试使用的工作目录。"""
    return prepare_work_dir(tmp_path, require_ocr_data, count=4)


@pytest.fixture
def pipeline_work_dir(tmp_path: Path, require_ocr_data: Path) -> Path:
    """准备 Pipeline 测试使用的工作目录（复制真实图片）。"""
    return prepare_work_dir(
        tmp_path,
        require_ocr_data,
        count=4,
        copy_real_images=True,
    )


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    """创建 API 测试客户端，手动初始化 Pipeline + TaskManager。"""
    config = PipelineConfig()
    pipeline = Pipeline(config)
    engine = FixtureOCREngine()
    pipeline.set_ocr_engine(engine)
    await pipeline.initialize()

    manager = TaskManager(pipeline)
    set_task_manager(manager)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(upload_router, prefix="/api/v1")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac

    await pipeline.shutdown()
    set_task_manager(None)


@pytest.fixture
def ws_env() -> Iterator[WsTestEnv]:
    """创建 WebSocket 测试环境：手动初始化 Pipeline + TaskManager。"""
    config = PipelineConfig()
    pipeline = Pipeline(config)
    engine = FixtureOCREngine()
    pipeline.set_ocr_engine(engine)
    asyncio.run(pipeline.initialize())

    manager = TaskManager(pipeline)
    set_task_manager(manager)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(upload_router, prefix="/api/v1")
    app.include_router(ws_router, prefix="/api/v1")

    with TestClient(app) as tc:
        yield WsTestEnv(client=tc, manager=manager)

    asyncio.run(pipeline.shutdown())
    set_task_manager(None)
