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

"""版面高亮端点测试（Epic E · E2）。

覆盖 ``GET /tasks/{id}/layout``：任务不存在 404 / 有 sidecar 返回 LayoutPayload
（字段与类型符合契约）/ 无 sidecar 404 优雅 / 多文档按 doc_dir 取对页集 /
doc_dir 边界守卫（``..`` 越界 → 404）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.output.layout_sidecar import (
    DocLayout,
    LayoutBlock,
    LayoutPage,
    write_doc_layout,
)
from docrestore.pipeline.task_manager import Task, TaskStatus


def _sample_layout() -> DocLayout:
    """一页、两块（标题 + 正文）的样例版面。"""
    return DocLayout(pages=[
        LayoutPage(
            filename="IMG_0001.jpg",
            image_size=(3024, 4032),
            blocks=[
                LayoutBlock((120, 88, 2900, 240), "paragraph_title", "第一章"),
                LayoutBlock((120, 260, 2900, 980), "text", "正文一"),
            ],
        ),
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
async def test_returns_404_when_task_missing(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/tasks/ghost/layout")
    assert resp.status_code == 404
    assert "任务不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_returns_payload_when_sidecar_present(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """有 .layout.json → 200，字段/类型符合契约（bbox 四元、image_size 二元）。"""
    out = tmp_path / "out_ok"
    out.mkdir()
    write_doc_layout(out, _sample_layout())
    _inject_task("t-layout", out)

    resp = await api_client.get("/api/v1/tasks/t-layout/layout")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pages"]) == 1
    page = data["pages"][0]
    assert page["filename"] == "IMG_0001.jpg"
    assert page["image_size"] == [3024, 4032]
    assert len(page["blocks"]) == 2
    first = page["blocks"][0]
    assert first["bbox"] == [120, 88, 2900, 240]
    assert first["label"] == "paragraph_title"
    assert first["text"] == "第一章"


@pytest.mark.asyncio
async def test_returns_404_when_no_sidecar(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """无 sidecar（非 VL / 老任务）→ 404 优雅。"""
    out = tmp_path / "out_empty"
    out.mkdir()
    _inject_task("t-nolayout", out)

    resp = await api_client.get("/api/v1/tasks/t-nolayout/layout")
    assert resp.status_code == 404
    assert "版面数据不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_multi_doc_selects_by_doc_dir(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """多文档：doc_dir 指向子目录，取该子目录的 sidecar。"""
    out = tmp_path / "out_multi"
    sub = out / "docA"
    sub.mkdir(parents=True)
    # 子目录有 sidecar，根目录没有
    layout = DocLayout(pages=[LayoutPage(
        filename="P1.jpg", image_size=(800, 600),
        blocks=[LayoutBlock((0, 0, 10, 10), "text", "子目录块")],
    )])
    write_doc_layout(sub, layout)
    _inject_task("t-multi", out)

    # 不带 doc_dir → 根目录无 sidecar → 404
    resp_root = await api_client.get("/api/v1/tasks/t-multi/layout")
    assert resp_root.status_code == 404

    # 带 doc_dir=docA → 取子目录 sidecar
    resp_sub = await api_client.get(
        "/api/v1/tasks/t-multi/layout", params={"doc_dir": "docA"},
    )
    assert resp_sub.status_code == 200
    data = resp_sub.json()
    assert data["pages"][0]["blocks"][0]["text"] == "子目录块"


@pytest.mark.asyncio
async def test_doc_dir_traversal_rejected(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """doc_dir 含 .. 越界 → 守卫拒绝 → 404（不读到 output_dir 之外）。"""
    out = tmp_path / "out_guard"
    out.mkdir()
    write_doc_layout(out, _sample_layout())  # 根有 sidecar
    _inject_task("t-guard", out)

    resp = await api_client.get(
        "/api/v1/tasks/t-guard/layout", params={"doc_dir": "../out_guard"},
    )
    assert resp.status_code == 404
