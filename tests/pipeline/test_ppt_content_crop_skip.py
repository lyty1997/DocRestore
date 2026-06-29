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

"""PPT 模式跳过自动 content_crop 回归测试（2026-06-29 回退 §14.2）。

屏摄幻灯无固定正文列、矫正后自动裁剪易误伤版式 → PPT 重新纳入
``skip_content_crop``，只做矫正、不自动裁剪（仍可手动框）。经 ``process_tree`` 走门禁：

- PPT 模式（矫正关）+ 含侧栏三栏图（文档模式必裁）→ **不**产 ``.content_crop``。
- 文档模式同图作对照 → 产 ``.content_crop``（证明该图确会触发裁剪，PPT 分支非空转）。

OCR mock、关精修 / PII，断言从行为派生，不写死数据集标识符。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("cv2")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from docrestore.models import PageOCR  # noqa: E402
from docrestore.pipeline.config import (  # noqa: E402
    LLMConfig,
    PIIConfig,
    PipelineConfig,
    PowerPointRestoreConfig,
)
from docrestore.pipeline.pipeline import Pipeline  # noqa: E402
from docrestore.pipeline.rate_controller import RateController  # noqa: E402


@pytest.fixture(autouse=True)
def _fast_cold_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """冷启动超时缩到 0.5s，避免 mock 路径样本不足时白等。"""
    monkeypatch.setattr(RateController, "COLD_START_TIMEOUT_S", 0.5)


def _build_pipeline() -> Pipeline:
    """带 mock OCR 的 Pipeline：OCR 目录落 output_dir（同生产），关精修/PII。"""
    cfg = PipelineConfig(
        llm=LLMConfig(model=""),  # 空 model → 无 refiner → 跳过精修
        pii=PIIConfig(enable=False),
    )
    pipeline = Pipeline(cfg)

    mock_engine = MagicMock()

    async def _ocr(image_path: Path, out_dir: Path) -> PageOCR:
        # 同真实引擎：OCR 产物落 ``{out_dir}/{stem}_OCR``（PPT 按页 sidecar 需要）。
        ocr_dir = out_dir / f"{image_path.stem}_OCR"
        (ocr_dir / "images").mkdir(parents=True, exist_ok=True)
        return PageOCR(
            image_path=image_path,
            image_size=(1280, 900),
            raw_text=f"正文 {image_path.name}",
            cleaned_text=f"正文 {image_path.name}",
            output_dir=ocr_dir,
        )

    mock_engine.ocr = AsyncMock(side_effect=_ocr)
    mock_engine.shutdown = AsyncMock(return_value=None)
    pipeline.set_ocr_engine(mock_engine)
    return pipeline


def _write_three_column(path: Path) -> None:
    """写含左导航+中正文+右大纲的三栏图（文档模式 content_crop 必裁两侧栏）。"""
    w, h = 1280, 900
    img = np.full((h, w, 3), 255, np.uint8)

    def _lines(
        x0: int, x1: int, y0: int, y1: int, gap: int, thick: int,
    ) -> None:
        y = y0
        while y < y1:
            cv2.line(img, (x0, y), (x1, y), (40, 40, 40), thick)
            y += gap

    _lines(40, 200, 80, 820, 30, 4)     # 左导航：窄列密集短行
    _lines(460, 840, 100, 400, 36, 6)   # 中正文上半（宽行）
    _lines(460, 780, 460, 800, 36, 6)   # 中正文下半（含段间空白）
    _lines(1080, 1240, 90, 700, 32, 4)  # 右大纲：窄列短行
    assert cv2.imwrite(str(path), img)


@pytest.mark.asyncio
async def test_ppt_mode_skips_auto_content_crop(tmp_path: Path) -> None:
    """PPT 模式（矫正关）不自动裁剪 → 不产 ``.content_crop``（回退 §14.2）。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    _write_three_column(image_dir / "slide.jpg")

    results = await _build_pipeline().process_tree(
        image_dir,
        output_dir,
        ppt=PowerPointRestoreConfig(enable=True, rectify=False),
    )

    assert len(results) == 1
    assert results[0].error == ""
    # PPT 重新纳入 skip_content_crop → 无自动裁剪 debug 目录、OCR 跑在原图上。
    assert not (output_dir / ".content_crop").exists()
    assert (output_dir / "slide_OCR").exists()


@pytest.mark.asyncio
async def test_doc_mode_crops_same_image(tmp_path: Path) -> None:
    """对照：同图文档模式触发自动裁剪 → 产 ``.content_crop``（证明 PPT 分支非空转）。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    _write_three_column(image_dir / "slide.jpg")

    results = await _build_pipeline().process_tree(image_dir, output_dir)

    assert len(results) == 1
    assert results[0].error == ""
    # 文档模式自动裁剪生效 → 落 .content_crop，且 OCR 跑在裁剪图上（_crop_OCR）。
    assert (output_dir / ".content_crop").exists()
    assert (output_dir / "slide_crop_OCR").exists()
