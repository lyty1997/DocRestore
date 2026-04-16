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

"""Pipeline + TaskManager 端到端测试

使用测试用 OCR 引擎（无 GPU），不启用 LLM 精修。
测试前将样例 OCR 数据拷贝到 tmp_path 模拟真实引擎写入。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docrestore.ocr.base import OCR_RESULT_FILENAME
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import (
    TaskManager,
    TaskStatus,
)

from ..support.ocr_engine import FixtureOCREngine

from ..conftest import TEST_STEMS

_GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
_LLM_API_KEY = _GEMINI_API_KEY or _GLM_API_KEY
_LLM_MODEL = "gemini/gemini-2.0-flash-exp" if _GEMINI_API_KEY else "openai/glm-5"


@pytest.mark.usefixtures("require_ocr_data")
class TestPipelineE2E:
    """Pipeline 端到端测试（无 LLM）"""

    @pytest.mark.asyncio
    async def test_full_pipeline(
        self, pipeline_work_dir: Path
    ) -> None:
        """完整流程：OCR → 清洗 → 合并 → 输出"""
        input_dir = pipeline_work_dir / "input"
        output_dir = pipeline_work_dir / "output"

        config = PipelineConfig()
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        progress_stages: list[str] = []
        results = await pipeline.process_many(
            image_dir=input_dir,
            output_dir=output_dir,
            on_progress=lambda p: progress_stages.append(
                p.stage
            ),
        )
        result = results[0]

        await pipeline.shutdown()

        assert result.output_path.exists()
        assert result.markdown != ""

        # 断言输出包含 OCR 原始文本中的关键内容
        first_stem = TEST_STEMS[0]
        raw_text = (
            output_dir / f"{first_stem}_OCR" / OCR_RESULT_FILENAME
        ).read_text(encoding="utf-8")
        keyword = next(
            (
                line.strip()
                for line in raw_text.splitlines()
                if line.strip()
            ),
            "",
        )
        assert keyword, "OCR 原始文本为空，无法提取关键字"
        assert keyword in result.markdown

        assert "ocr" in progress_stages
        # 无 LLM 配置时不会有 refine 阶段
        assert "render" in progress_stages

        content = result.output_path.read_text(encoding="utf-8")
        assert "<!-- page:" not in content

    @pytest.mark.asyncio
    async def test_pipeline_returns_result(
        self, pipeline_work_dir: Path
    ) -> None:
        """process_many() 返回 PipelineResult 列表"""
        input_dir = pipeline_work_dir / "input"
        output_dir = pipeline_work_dir / "output"

        config = PipelineConfig()
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        results = await pipeline.process_many(
            image_dir=input_dir,
            output_dir=output_dir,
        )

        await pipeline.shutdown()

        assert len(results) >= 1
        assert results[0].output_path.exists()

    @pytest.mark.asyncio
    async def test_pipeline_no_images(
        self, tmp_path: Path
    ) -> None:
        """空目录报错"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        config = PipelineConfig()
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        with pytest.raises(FileNotFoundError):
            await pipeline.process_many(
                image_dir=empty_dir, output_dir=tmp_path
            )

        await pipeline.shutdown()


@pytest.mark.usefixtures("require_ocr_data")
class TestTaskManager:
    """TaskManager 测试"""

    @pytest.mark.asyncio
    async def test_task_lifecycle(
        self, pipeline_work_dir: Path
    ) -> None:
        """任务生命周期：创建 → 运行 → 完成"""
        input_dir = pipeline_work_dir / "input"
        output_dir = pipeline_work_dir / "output"

        config = PipelineConfig()
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        manager = TaskManager(pipeline)
        task = manager.create_task(
            image_dir=str(input_dir),
            output_dir=str(output_dir),
        )

        assert task.status == TaskStatus.PENDING
        assert manager.get_task(task.task_id) is task

        await manager.run_task(task.task_id)

        updated = manager.get_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        # 单组退化：result 直接赋值
        assert updated.result is not None
        assert updated.result.markdown != ""
        assert updated.error is None

        await pipeline.shutdown()

    @pytest.mark.asyncio
    async def test_task_failure(
        self, tmp_path: Path
    ) -> None:
        """任务失败时状态为 FAILED"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        manager = TaskManager(pipeline)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        task = manager.create_task(
            image_dir=str(empty_dir),
            output_dir=str(tmp_path / "out"),
        )

        await manager.run_task(task.task_id)

        assert task.status == TaskStatus.FAILED
        assert task.error is not None
        assert "FileNotFoundError" in task.error

        await pipeline.shutdown()

    def test_create_task_auto_output_dir(self) -> None:
        """output_dir 为空时自动生成"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        manager = TaskManager(pipeline)
        task = manager.create_task(
            image_dir="/some/path"
        )
        assert task.output_dir != ""
        assert task.task_id in task.output_dir

    def test_get_nonexistent_task(self) -> None:
        """查询不存在的任务返回 None"""
        config = PipelineConfig()
        pipeline = Pipeline(config)
        manager = TaskManager(pipeline)
        assert manager.get_task("nonexistent") is None


@pytest.mark.usefixtures("require_ocr_data")
@pytest.mark.skipif(
    not _LLM_API_KEY,
    reason="GLM_API_KEY 或 GEMINI_API_KEY 未设置",
)
class TestPipelineWithLLM:
    """带真实 LLM 精修的 Pipeline 端到端测试"""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_llm(
        self, pipeline_work_dir: Path
    ) -> None:
        """完整流程：OCR → 清洗 → 合并 → LLM 精修 → 输出"""
        input_dir = pipeline_work_dir / "input"
        output_dir = pipeline_work_dir / "output"

        from docrestore.pipeline.config import PIIConfig

        llm_config = LLMConfig(
            model=_LLM_MODEL,
            api_key=_LLM_API_KEY,
        )
        # 禁用 PII 避免实体检测 API 失败干扰 LLM 精修测试
        pii_config = PIIConfig(enable=False)
        config = PipelineConfig(llm=llm_config, pii=pii_config)
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        progress_stages: list[str] = []

        try:
            results = await pipeline.process_many(
                image_dir=input_dir,
                output_dir=output_dir,
                on_progress=lambda p: progress_stages.append(
                    p.stage
                ),
            )
            result = results[0]
        except Exception as e:
            # API key 无效时跳过测试
            if "AuthenticationError" in str(type(e).__name__):
                pytest.skip(f"LLM API 认证失败: {e}")
            raise

        await pipeline.shutdown()

        assert result.output_path.exists()
        assert result.markdown != ""

        assert "ocr" in progress_stages
        assert "refine" in progress_stages
        assert "render" in progress_stages

        content = result.markdown
        assert "<!-- page:" not in content

        print(
            f"\n=== LLM 精修后输出 ({len(content)} 字符) ==="
        )
        print(content[:500])
        if len(content) > 500:
            print("...")
        print(f"\n=== GAP 数量: {len(result.gaps)} ===")
