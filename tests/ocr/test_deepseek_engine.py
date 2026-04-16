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

"""DeepSeek-OCR-2 真实引擎测试（需要 GPU + 模型）

无 GPU 或模型时自动跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None

from docrestore.ocr.base import OCR_RAW_RESULT_FILENAME, OCR_RESULT_FILENAME

from ..conftest import (
    TEST_IMAGE_DIR,
    TEST_STEMS,
    _has_gpu,
    get_test_image_path,
)


@pytest.mark.skipif(
    not _has_gpu(), reason="无可用 GPU"
)
@pytest.mark.skipif(
    torch is None,
    reason="torch 未安装",
)
@pytest.mark.skipif(
    TEST_IMAGE_DIR is None or not TEST_STEMS,
    reason="测试图片不存在",
)
class TestDeepSeekOCR2Engine:
    """DeepSeek-OCR-2 引擎测试"""

    @pytest.mark.asyncio
    async def test_single_ocr(
        self, tmp_path: Path
    ) -> None:
        """单张图片 OCR"""
        from docrestore.ocr.deepseek_ocr2 import (
            DeepSeekOCR2Engine,
        )
        from docrestore.pipeline.config import OCRConfig

        assert TEST_IMAGE_DIR is not None
        test_image = get_test_image_path(
            TEST_IMAGE_DIR, TEST_STEMS[0]
        )
        stem = TEST_STEMS[0]

        config = OCRConfig()
        engine = DeepSeekOCR2Engine(config)

        try:
            await engine.initialize()
        except ImportError:
            pytest.skip("torch 未安装")
        except Exception as exc:
            # 说明：在显存不足时，模型初始化可能直接 OOM；
            # 该场景属于测试环境资源限制，跳过以避免整套测试失败。
            oom_err = (
                getattr(torch, "OutOfMemoryError", None)
                if torch is not None
                else None
            )
            if oom_err is not None and isinstance(exc, oom_err):
                pytest.skip(
                    "GPU 显存不足，跳过 DeepSeek-OCR-2 真机测试"
                )
            raise

        try:
            result = await engine.ocr(
                test_image, tmp_path
            )

            assert result.raw_text != ""
            assert result.output_dir is not None
            assert result.output_dir.exists()
            assert isinstance(result.has_eos, bool)
            assert result.image_size[0] > 0
            assert result.image_size[1] > 0

            ocr_dir = tmp_path / f"{stem}_OCR"
            assert ocr_dir.exists()
            assert (ocr_dir / OCR_RESULT_FILENAME).exists()
            assert (
                ocr_dir / OCR_RAW_RESULT_FILENAME
            ).exists()
            assert (ocr_dir / "images").exists()
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_ocr_batch(
        self, tmp_path: Path
    ) -> None:
        """批量 OCR + 进度回调"""
        from docrestore.ocr.deepseek_ocr2 import (
            DeepSeekOCR2Engine,
        )
        from docrestore.pipeline.config import OCRConfig

        assert TEST_IMAGE_DIR is not None
        test_image = get_test_image_path(
            TEST_IMAGE_DIR, TEST_STEMS[0]
        )

        config = OCRConfig()
        engine = DeepSeekOCR2Engine(config)

        try:
            await engine.initialize()
        except ImportError:
            pytest.skip("torch 未安装")
        except Exception as exc:
            # 说明：在显存不足时，模型初始化可能直接 OOM；
            # 该场景属于测试环境资源限制，跳过以避免整套测试失败。
            oom_err = (
                getattr(torch, "OutOfMemoryError", None)
                if torch is not None
                else None
            )
            if oom_err is not None and isinstance(exc, oom_err):
                pytest.skip(
                    "GPU 显存不足，跳过 DeepSeek-OCR-2 真机测试"
                )
            raise

        try:
            progress_calls: list[tuple[int, int]] = []
            results = await engine.ocr_batch(
                [test_image],
                tmp_path,
                on_progress=lambda c, t: progress_calls.append(
                    (c, t)
                ),
            )

            assert len(results) == 1
            assert results[0].raw_text != ""
            assert len(progress_calls) == 1
            assert progress_calls[0] == (1, 1)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_engine_lifecycle(self) -> None:
        """引擎生命周期：初始化 → 就绪 → 关闭

        说明：真实引擎在部分 GPU 环境可能因显存不足而无法初始化；
        该场景属于测试环境资源限制，跳过以避免整套测试失败。
        """
        from docrestore.ocr.deepseek_ocr2 import (
            DeepSeekOCR2Engine,
        )
        from docrestore.pipeline.config import OCRConfig

        config = OCRConfig()
        engine = DeepSeekOCR2Engine(config)

        assert not engine.is_ready

        try:
            await engine.initialize()
        except ImportError:
            pytest.skip("torch 未安装")
        except Exception as exc:
            oom_err = (
                getattr(torch, "OutOfMemoryError", None)
                if torch is not None
                else None
            )
            if oom_err is not None and isinstance(exc, oom_err):
                pytest.skip("GPU 显存不足，跳过 DeepSeek-OCR-2 真机测试")
            raise

        assert engine.is_ready
        await engine.shutdown()
        assert not engine.is_ready
