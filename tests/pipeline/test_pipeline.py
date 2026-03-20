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
import shutil
from pathlib import Path

import pytest

from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.task_manager import (
    TaskManager,
    TaskStatus,
)

from ..support.ocr_engine import FixtureOCREngine

from ..conftest import TEST_STEMS

_GLM_API_KEY = os.environ.get("GLM_API_KEY", "")


@pytest.fixture
def work_dir(
    tmp_path: Path, require_ocr_data: Path
) -> Path:
    """将样例图片和 OCR 数据拷贝到 tmp_path，模拟真实工作目录。

    目录结构：
      tmp_path/input/  — 图片（.JPG 空文件，仅用于文件名匹配）
      tmp_path/output/ — OCR 输出（从 TEST_IMAGE_DIR 拷贝 *_OCR/ 目录）
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    stems = TEST_STEMS[:4]
    for stem in stems:
        (input_dir / f"{stem}.JPG").write_bytes(b"fake")
        src = require_ocr_data / f"{stem}_OCR"
        if src.exists():
            shutil.copytree(src, output_dir / f"{stem}_OCR")

    return tmp_path


@pytest.mark.usefixtures("require_ocr_data")
class TestPipelineE2E:
    """Pipeline 端到端测试（无 LLM）"""

    @pytest.mark.asyncio
    async def test_full_pipeline(
        self, work_dir: Path
    ) -> None:
        """完整流程：OCR → 清洗 → 合并 → 输出"""
        input_dir = work_dir / "input"
        output_dir = work_dir / "output"

        config = PipelineConfig()
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        progress_stages: list[str] = []
        result = await pipeline.process(
            image_dir=input_dir,
            output_dir=output_dir,
            on_progress=lambda p: progress_stages.append(
                p.stage
            ),
        )

        await pipeline.shutdown()

        assert result.output_path.exists()
        assert result.markdown != ""
        assert "TH1520" in result.markdown

        assert "ocr" in progress_stages
        assert "clean" in progress_stages
        assert "refine" in progress_stages
        assert "render" in progress_stages

        doc_path = output_dir / "document.md"
        assert doc_path.exists()
        content = doc_path.read_text(encoding="utf-8")
        assert "<!-- page:" not in content

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
            await pipeline.process(
                image_dir=empty_dir, output_dir=tmp_path
            )

        await pipeline.shutdown()


@pytest.mark.usefixtures("require_ocr_data")
class TestTaskManager:
    """TaskManager 测试"""

    @pytest.mark.asyncio
    async def test_task_lifecycle(
        self, work_dir: Path
    ) -> None:
        """任务生命周期：创建 → 运行 → 完成"""
        input_dir = work_dir / "input"
        output_dir = work_dir / "output"

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
    not _GLM_API_KEY,
    reason="GLM_API_KEY 未设置",
)
class TestPipelineWithLLM:
    """带真实 LLM 精修的 Pipeline 端到端测试"""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_llm(
        self, work_dir: Path
    ) -> None:
        """完整流程：OCR → 清洗 → 合并 → LLM 精修 → 输出"""
        input_dir = work_dir / "input"
        output_dir = work_dir / "output"

        llm_config = LLMConfig(
            model="openai/glm-5",
            api_key=_GLM_API_KEY,
        )
        config = PipelineConfig(llm=llm_config)
        pipeline = Pipeline(config)
        engine = FixtureOCREngine()
        pipeline.set_ocr_engine(engine)
        await pipeline.initialize()

        progress_stages: list[str] = []
        result = await pipeline.process(
            image_dir=input_dir,
            output_dir=output_dir,
            on_progress=lambda p: progress_stages.append(
                p.stage
            ),
        )

        await pipeline.shutdown()

        assert result.output_path.exists()
        assert result.markdown != ""

        assert "ocr" in progress_stages
        assert "clean" in progress_stages
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
