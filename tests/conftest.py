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

from pathlib import Path

import pytest

# 测试数据根目录
TEST_IMAGES_ROOT = Path(__file__).parent.parent / "test_images"

# 支持的图片后缀
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _find_test_image_dir() -> Path | None:
    """找到 test_images/ 下第一个含图片的子目录"""
    if not TEST_IMAGES_ROOT.exists():
        return None
    for sub in sorted(TEST_IMAGES_ROOT.iterdir()):
        if sub.is_dir():
            images = [
                p
                for p in sub.iterdir()
                if p.suffix.lower() in _IMAGE_SUFFIXES
            ]
            if images:
                return sub
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

        return torch.cuda.is_available()
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
        (directory / f"{s}_OCR" / "result.mmd").exists()
        for s in stems[:count]
    )


# --- fixtures ---


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
        (img_dir / f"{s}_OCR" / "result.mmd").exists()
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
