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

"""代码版面端点测试（#93 · B3）。

覆盖 ``GET /tasks/{id}/code-layout``：任务不存在 404 / 有 sidecar 返回
CodeLayoutPayload（字段与类型符合契约）/ 无 sidecar 404 优雅 / 多文档按 doc_dir
取对子目录 / doc_dir 边界守卫（``..`` 越界 → 404）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.output.code_layout_sidecar import (
    CodeFileLayout,
    CodeLayout,
    CodeLineBox,
    write_code_layout,
)
from docrestore.pipeline.task_manager import Task, TaskStatus


def _sample_layout() -> CodeLayout:
    """一文件、两行 bbox 的样例版面。"""
    return CodeLayout(files=[
        CodeFileLayout(path="app/foo.py", lines=[
            CodeLineBox(1, "page0001.col0", (10, 20, 200, 40)),
            CodeLineBox(2, "page0001.col0", (10, 40, 200, 60)),
        ]),
    ])


def _inject_task(task_id: str, output_dir: Path) -> None:
    """注入一个已完成 Task，output_dir 指向给定目录。"""
    assert routes._task_manager is not None
    task = Task(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        image_dir=str(output_dir / "imgs"),
        output_dir=str(output_dir),
    )
    routes._task_manager._tasks[task_id] = task


@pytest.mark.asyncio
async def test_returns_404_when_task_missing(api_client: AsyncClient) -> None:
    """任务不存在 → 404。"""
    resp = await api_client.get("/api/v1/tasks/ghost/code-layout")
    assert resp.status_code == 404
    assert "任务不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_returns_payload_when_sidecar_present(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """有 .code_layout.json → 200，字段/类型符合契约（line_no/page/bbox 四元）。"""
    out = tmp_path / "out_ok"
    out.mkdir()
    write_code_layout(out, _sample_layout())
    _inject_task("c-ok", out)

    resp = await api_client.get("/api/v1/tasks/c-ok/code-layout")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    file_layout = data["files"][0]
    assert file_layout["path"] == "app/foo.py"
    assert len(file_layout["lines"]) == 2
    first = file_layout["lines"][0]
    assert first["line_no"] == 1
    assert first["page"] == "page0001.col0"
    assert first["bbox"] == [10, 20, 200, 40]


@pytest.mark.asyncio
async def test_returns_404_when_no_sidecar(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """无 sidecar（非 VL / 文档或 PPT 模式 / 老任务）→ 404 优雅。"""
    out = tmp_path / "out_empty"
    out.mkdir()
    _inject_task("c-none", out)

    resp = await api_client.get("/api/v1/tasks/c-none/code-layout")
    assert resp.status_code == 404
    assert "代码版面数据不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_multi_doc_selects_by_doc_dir(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """多文档：doc_dir 指向子目录，取该子目录的 sidecar；根目录无则 404。"""
    out = tmp_path / "out_multi"
    sub = out / "projA"
    sub.mkdir(parents=True)
    write_code_layout(sub, CodeLayout(files=[CodeFileLayout(
        path="a.py", lines=[CodeLineBox(7, "p.col0", (0, 0, 5, 5))],
    )]))
    _inject_task("c-multi", out)

    resp_root = await api_client.get("/api/v1/tasks/c-multi/code-layout")
    assert resp_root.status_code == 404

    resp_sub = await api_client.get(
        "/api/v1/tasks/c-multi/code-layout", params={"doc_dir": "projA"},
    )
    assert resp_sub.status_code == 200
    data = resp_sub.json()
    assert data["files"][0]["lines"][0]["line_no"] == 7


@pytest.mark.asyncio
async def test_doc_dir_traversal_rejected(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """doc_dir 含 .. 越界 → 守卫拒绝 → 404（不读到 output_dir 之外）。"""
    out = tmp_path / "out_guard"
    out.mkdir()
    write_code_layout(out, _sample_layout())  # 根有 sidecar
    _inject_task("c-guard", out)

    resp = await api_client.get(
        "/api/v1/tasks/c-guard/code-layout",
        params={"doc_dir": "../out_guard"},
    )
    assert resp.status_code == 404
