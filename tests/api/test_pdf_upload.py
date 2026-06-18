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

"""Epic A A1-③：PDF 上传放行 + 全图片 xor 全 PDF 互斥双闸。

- 闸一（upload_files）：同一会话混传图片 + PDF → 异类进 failed
- 闸二（create_task）：image_dir 同时含 PDF 与图片 → 400（兜底绕过上传层的直传）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api.routes import _has_mixed_input


def test_has_mixed_input(tmp_path: Path) -> None:
    """兜底闸判定：仅 PDF/仅图片不算混合；同时含 PDF 与图片（含子目录）才算。"""
    pure_pdf = tmp_path / "pdf"
    pure_pdf.mkdir()
    (pure_pdf / "a.pdf").write_bytes(b"x")
    assert _has_mixed_input(str(pure_pdf)) is False

    pure_img = tmp_path / "img"
    pure_img.mkdir()
    (pure_img / "a.jpg").write_bytes(b"x")
    assert _has_mixed_input(str(pure_img)) is False

    mixed_root = tmp_path / "mix"
    mixed_root.mkdir()
    (mixed_root / "a.pdf").write_bytes(b"x")
    (mixed_root / "b.jpg").write_bytes(b"x")
    assert _has_mixed_input(str(mixed_root)) is True

    mixed_sub = tmp_path / "mix_sub"
    mixed_sub.mkdir()
    (mixed_sub / "a.pdf").write_bytes(b"x")
    sub = mixed_sub / "sub"
    sub.mkdir()
    (sub / "c.png").write_bytes(b"x")
    assert _has_mixed_input(str(mixed_sub)) is True

    assert _has_mixed_input(str(tmp_path / "nonexistent")) is False


@pytest.mark.asyncio
async def test_pdf_extension_in_allowed(api_client: AsyncClient) -> None:
    """新建上传会话的 allowed_extensions 透出 .pdf。"""
    resp = await api_client.post("/api/v1/uploads")
    assert resp.status_code == 200
    assert ".pdf" in resp.json()["allowed_extensions"]


@pytest.mark.asyncio
async def test_upload_pdf_accepted(api_client: AsyncClient) -> None:
    """单独上传 PDF 被放行（不进 failed）。"""
    sid = (await api_client.post("/api/v1/uploads")).json()["session_id"]
    files = [("files", ("doc.pdf", b"%PDF-1.4 fake", "application/pdf"))]
    up = await api_client.post(f"/api/v1/uploads/{sid}/files", files=files)
    assert up.status_code == 200
    body = up.json()
    assert body["total_uploaded"] == 1
    assert body["failed"] == []


@pytest.mark.asyncio
async def test_mixed_upload_rejected_gate_one(api_client: AsyncClient) -> None:
    """同一会话混传 jpg + pdf → 首文件确立类型，异类进 failed（闸一）。"""
    sid = (await api_client.post("/api/v1/uploads")).json()["session_id"]
    files = [
        ("files", ("a.jpg", b"img-bytes", "image/jpeg")),
        ("files", ("b.pdf", b"%PDF-1.4 fake", "application/pdf")),
    ]
    up = await api_client.post(f"/api/v1/uploads/{sid}/files", files=files)
    assert up.status_code == 200
    body = up.json()
    assert body["total_uploaded"] == 1  # 仅 jpg 入库
    assert "b.pdf" in body["failed"]  # pdf 异类被拒


@pytest.mark.asyncio
async def test_create_task_mixed_dir_rejected_gate_two(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """直传含 PDF + 图片的 image_dir → 400（闸二兜底）。"""
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "a.pdf").write_bytes(b"x")
    (mixed / "b.jpg").write_bytes(b"x")
    create = await api_client.post(
        "/api/v1/tasks", json={"image_dir": str(mixed)},
    )
    assert create.status_code == 400


@pytest.mark.asyncio
async def test_create_task_pure_pdf_dir_ok(
    api_client: AsyncClient, tmp_path: Path,
) -> None:
    """纯 PDF 目录 → 闸二放行，任务可创建。"""
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "a.pdf").write_bytes(b"x")
    create = await api_client.post(
        "/api/v1/tasks", json={"image_dir": str(pdfs)},
    )
    assert create.status_code == 200
