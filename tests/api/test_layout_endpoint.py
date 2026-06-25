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
from docrestore.output.ppt_layout import (
    PptLayoutRegion,
    build_ppt_layout,
    write_ppt_layout,
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
    # 无预处理目录 → bbox 原图坐标 → processed=False（前端显原图）
    assert data["processed"] is False
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


# ── §13/§15：PPT 回退 + 处理图端点（PPT 矫正 / content_crop 裁剪）────────


def _jpg_bytes() -> bytes:
    """最小占位 jpg（端点只 FileResponse，不校验像素）。"""
    return b"\xff\xd8\xff\xd9"


@pytest.mark.asyncio
async def test_ppt_fallback_returns_processed_layout(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """无 .layout.json 但有 .ppt_layout.json → 回退、content→text；有 .rectified
    处理图 → processed=True（前端改显处理图）。"""
    out = tmp_path / "out_ppt"
    out.mkdir()
    ppt = build_ppt_layout([
        ("IMG_0001.jpg", (1205, 809), [
            PptLayoutRegion((311, 79, 909, 131), "paragraph_title", "标题块"),
            PptLayoutRegion((100, 200, 500, 400), "text", "正文块"),
        ]),
    ])
    assert ppt is not None
    write_ppt_layout(out, ppt)
    # 矫正图存在 → processed=True（探处理图目录有文件）
    rect_dir = out / ".rectified"
    rect_dir.mkdir()
    (rect_dir / "IMG_0001_after.jpg").write_bytes(_jpg_bytes())
    _inject_task("t-ppt", out)

    resp = await api_client.get("/api/v1/tasks/t-ppt/layout")
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] is True
    page = data["pages"][0]
    assert page["filename"] == "IMG_0001.jpg"
    assert page["image_size"] == [1205, 809]
    # regions[].content 映射成 blocks[].text
    assert page["blocks"][0]["text"] == "标题块"
    assert page["blocks"][0]["bbox"] == [311, 79, 909, 131]
    assert page["blocks"][1]["text"] == "正文块"


@pytest.mark.asyncio
async def test_content_crop_doc_layout_marks_processed(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """文档模式有 .layout.json + .content_crop 裁剪图 → processed=True（§15）。"""
    out = tmp_path / "out_cc"
    out.mkdir()
    write_doc_layout(out, _sample_layout())
    cc_dir = out / ".content_crop"
    cc_dir.mkdir()
    (cc_dir / "IMG_0001_crop.jpg").write_bytes(_jpg_bytes())
    _inject_task("t-cc", out)

    resp = await api_client.get("/api/v1/tasks/t-cc/layout")
    assert resp.status_code == 200
    assert resp.json()["processed"] is True


@pytest.mark.asyncio
async def test_processed_image_served_rectified(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """原图名 → .rectified/{stem}_after{suffix}（PPT 矫正）→ 200。"""
    out = tmp_path / "out_rect"
    rect_dir = out / ".rectified"
    rect_dir.mkdir(parents=True)
    (rect_dir / "IMG_0001_after.jpg").write_bytes(_jpg_bytes())
    _inject_task("t-rect", out)

    resp = await api_client.get(
        "/api/v1/tasks/t-rect/processed-image",
        params={"name": "IMG_0001.jpg"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_processed_image_served_content_crop(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """原图名 → .content_crop/{stem}_crop{suffix}（文档裁剪）→ 200（§15）。"""
    out = tmp_path / "out_cc_img"
    cc_dir = out / ".content_crop"
    cc_dir.mkdir(parents=True)
    (cc_dir / "IMG_0001_crop.jpg").write_bytes(_jpg_bytes())
    _inject_task("t-cc-img", out)

    resp = await api_client.get(
        "/api/v1/tasks/t-cc-img/processed-image",
        params={"name": "IMG_0001.jpg"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_processed_image_prefers_chained_after_crop(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """PPT 矫正+裁剪串联：同时有 _after 与 _after_crop → 优先返回链末 _after_crop
    （bbox 在裁剪后坐标系，§14.2）。"""
    out = tmp_path / "out_chain"
    (out / ".rectified").mkdir(parents=True)
    (out / ".rectified" / "IMG_0001_after.jpg").write_bytes(_jpg_bytes())
    cc = out / ".content_crop"
    cc.mkdir(parents=True)
    (cc / "IMG_0001_after_crop.jpg").write_bytes(_jpg_bytes() + b"\x00")
    _inject_task("t-chain", out)

    resp = await api_client.get(
        "/api/v1/tasks/t-chain/processed-image",
        params={"name": "IMG_0001.jpg"},
    )
    assert resp.status_code == 200
    # 命中链末 _after_crop（内容比 _after 多 1 字节）而非仅矫正图
    assert len(resp.content) == len(_jpg_bytes()) + 1


@pytest.mark.asyncio
async def test_processed_image_404_when_missing(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """该页无任何处理图（矫正/裁剪都没）→ 404（前端 onError 回退原图）。"""
    out = tmp_path / "out_proc_miss"
    (out / ".content_crop").mkdir(parents=True)
    _inject_task("t-proc-miss", out)

    resp = await api_client.get(
        "/api/v1/tasks/t-proc-miss/processed-image",
        params={"name": "NOPE.jpg"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_processed_image_rejects_traversal(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """name 含路径分隔/穿越 → 400（不越界到 output_dir 之外）。"""
    out = tmp_path / "out_proc_guard"
    out.mkdir()
    _inject_task("t-proc-guard", out)

    for bad in ("../secret.jpg", "sub/IMG.jpg"):
        resp = await api_client.get(
            "/api/v1/tasks/t-proc-guard/processed-image",
            params={"name": bad},
        )
        assert resp.status_code == 400
