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

"""PUT /tasks/{task_id}/results/{result_index} 端到端测试

覆盖：
- 404：任务不存在（实际返回 400，因 manager 返回字符串错误）
- 未完成任务拒绝
- 索引越界拒绝
- 成功：HTTP 200 + 磁盘文件被改写 + GET /result 返回新内容
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.models import PipelineResult
from docrestore.pipeline.task_manager import Task, TaskStatus


def _inject_completed_task(
    task_id: str,
    tmp_path: Path,
    *,
    num_results: int = 1,
    initial_markdown: str = "原始内容",
) -> list[Path]:
    """向 routes._task_manager 注入一个 COMPLETED Task，并落盘 markdown。"""
    assert routes._task_manager is not None, "api_client fixture 未初始化"

    out_dir = tmp_path / f"out_{task_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[PipelineResult] = []
    paths: list[Path] = []
    for i in range(num_results):
        sub = out_dir if num_results == 1 else out_dir / f"doc{i}"
        sub.mkdir(parents=True, exist_ok=True)
        md_path = sub / "document.md"
        md_path.write_text(initial_markdown, encoding="utf-8")
        results.append(
            PipelineResult(
                output_path=md_path,
                markdown=initial_markdown,
                doc_dir=sub.name if num_results > 1 else "",
            ),
        )
        paths.append(md_path)

    task = Task(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        image_dir=str(tmp_path / "imgs"),
        output_dir=str(out_dir),
        results=results,
    )
    routes._task_manager._tasks[task_id] = task
    return paths


class TestPutResultMarkdown:
    """PUT /tasks/{task_id}/results/{result_index}"""

    @pytest.mark.asyncio
    async def test_update_missing_task_returns_400(
        self, api_client: AsyncClient,
    ) -> None:
        """任务不存在：manager 返回字符串 → HTTP 400。"""
        resp = await api_client.put(
            "/api/v1/tasks/ghost/results/0",
            json={"markdown": "x"},
        )
        assert resp.status_code == 400
        assert "任务不存在" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_incomplete_task_rejected(
        self, api_client: AsyncClient,
    ) -> None:
        """任务未完成：返回 400。"""
        # 先创建一个真实任务（FixtureOCREngine 无数据会 failed，但先立即 PUT）
        # 更可靠：直接注入 PENDING 状态 task
        assert routes._task_manager is not None
        task = Task(
            task_id="pending-xx",
            status=TaskStatus.PENDING,
            image_dir="/x",
            output_dir="/y",
        )
        routes._task_manager._tasks[task.task_id] = task

        resp = await api_client.put(
            f"/api/v1/tasks/{task.task_id}/results/0",
            json={"markdown": "x"},
        )
        assert resp.status_code == 400
        assert "任务未完成" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_out_of_range_rejected(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        _inject_completed_task("oob-1", tmp_path)
        resp = await api_client.put(
            "/api/v1/tasks/oob-1/results/99",
            json={"markdown": "x"},
        )
        assert resp.status_code == 400
        assert "索引越界" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_success_writes_disk_and_memory(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """成功路径：HTTP 200 + 磁盘更新 + GET result 返回新 markdown。"""
        paths = _inject_completed_task(
            "happy-1", tmp_path, initial_markdown="旧内容",
        )
        md_path = paths[0]

        new_md = "# 新标题\n修改后的正文"
        resp = await api_client.put(
            "/api/v1/tasks/happy-1/results/0",
            json={"markdown": new_md},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "保存成功"

        # 磁盘写入生效
        assert md_path.read_text(encoding="utf-8") == new_md

        # GET result 读到新内容
        get = await api_client.get("/api/v1/tasks/happy-1/result")
        assert get.status_code == 200
        assert get.json()["markdown"] == new_md

    @pytest.mark.asyncio
    async def test_update_multi_doc_targets_correct_index(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """多文档任务：只修改指定 index，其它文档不动。"""
        paths = _inject_completed_task(
            "multi-1", tmp_path, num_results=3,
            initial_markdown="共享原文",
        )

        resp = await api_client.put(
            "/api/v1/tasks/multi-1/results/1",
            json={"markdown": "只改中间那份"},
        )
        assert resp.status_code == 200

        assert paths[0].read_text(encoding="utf-8") == "共享原文"
        assert paths[1].read_text(encoding="utf-8") == "只改中间那份"
        assert paths[2].read_text(encoding="utf-8") == "共享原文"
