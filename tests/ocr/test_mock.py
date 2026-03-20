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

"""FixtureOCREngine 单元测试"""

from __future__ import annotations

import pytest

from docrestore.ocr.mock import FixtureOCREngine

from ..conftest import TEST_IMAGE_DIR, TEST_STEMS


@pytest.mark.usefixtures("require_ocr_data")
class TestFixtureOCREngine:
    """FixtureOCREngine 测试"""

    @pytest.mark.asyncio
    async def test_initialize_and_ready(self) -> None:
        """初始化后 is_ready 为 True"""
        engine = FixtureOCREngine()
        assert not engine.is_ready
        await engine.initialize()
        assert engine.is_ready
        await engine.shutdown()
        assert not engine.is_ready

    @pytest.mark.asyncio
    async def test_ocr_single(self) -> None:
        """单张 OCR 返回正确的 PageOCR"""
        assert TEST_IMAGE_DIR is not None
        stem = TEST_STEMS[0]
        engine = FixtureOCREngine()
        await engine.initialize()

        page = await engine.ocr(
            TEST_IMAGE_DIR / f"{stem}.JPG", TEST_IMAGE_DIR
        )
        assert page.raw_text != ""
        assert page.output_dir == TEST_IMAGE_DIR / f"{stem}_OCR"
        assert page.image_path == TEST_IMAGE_DIR / f"{stem}.JPG"
        assert page.has_eos is True

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_ocr_batch(self) -> None:
        """批量 OCR 返回正确数量的结果"""
        assert TEST_IMAGE_DIR is not None
        stems = TEST_STEMS[:4]
        engine = FixtureOCREngine()
        await engine.initialize()

        paths = [
            TEST_IMAGE_DIR / f"{s}.JPG" for s in stems
        ]
        progress_calls: list[tuple[int, int]] = []
        results = await engine.ocr_batch(
            paths,
            TEST_IMAGE_DIR,
            on_progress=lambda c, t: progress_calls.append(
                (c, t)
            ),
        )

        assert len(results) == len(stems)
        assert len(progress_calls) == len(stems)
        assert progress_calls[-1] == (
            len(stems),
            len(stems),
        )

        for page in results:
            assert page.raw_text != ""

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_missing_ocr_dir(self) -> None:
        """OCR 目录不存在时抛出 FileNotFoundError"""
        assert TEST_IMAGE_DIR is not None
        engine = FixtureOCREngine()
        await engine.initialize()

        with pytest.raises(FileNotFoundError):
            await engine.ocr(
                TEST_IMAGE_DIR / "NONEXISTENT.JPG",
                TEST_IMAGE_DIR,
            )

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_regions_have_labels(self) -> None:
        """regions 从 grounding 文本中提取标签"""
        assert TEST_IMAGE_DIR is not None
        stem = TEST_STEMS[0]
        engine = FixtureOCREngine()
        await engine.initialize()

        page = await engine.ocr(
            TEST_IMAGE_DIR / f"{stem}.JPG", TEST_IMAGE_DIR
        )
        for region in page.regions:
            assert region.label != ""
            assert region.cropped_path is not None

        await engine.shutdown()
