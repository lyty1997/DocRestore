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

"""文件上传 API 测试"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from docrestore.api.upload import _sessions


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    """每个测试前清空上传会话。"""
    _sessions.clear()


class TestUpload:
    """上传流程测试"""

    @pytest.mark.asyncio
    async def test_create_session(
        self, api_client: AsyncClient
    ) -> None:
        """POST /uploads 创建上传会话"""
        resp = await api_client.post("/api/v1/uploads")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"].startswith("upl_")
        assert data["max_file_size_mb"] > 0
        assert len(data["allowed_extensions"]) > 0

    @pytest.mark.asyncio
    async def test_upload_valid_file(
        self, api_client: AsyncClient
    ) -> None:
        """上传有效图片文件"""
        # 创建会话
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        # 上传文件
        resp = await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[("files", ("test.jpg", b"fake-image-data", "image/jpeg"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert len(data["uploaded"]) == 1
        assert data["total_uploaded"] == 1
        assert len(data["failed"]) == 0

    @pytest.mark.asyncio
    async def test_upload_invalid_extension_rejected(
        self, api_client: AsyncClient
    ) -> None:
        """上传不支持的扩展名文件应失败"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        resp = await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[("files", ("doc.pdf", b"fake-pdf", "application/pdf"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["uploaded"]) == 0
        assert "doc.pdf" in data["failed"]

    @pytest.mark.asyncio
    async def test_upload_mixed_files(
        self, api_client: AsyncClient
    ) -> None:
        """混合上传：有效和无效文件"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        resp = await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[
                ("files", ("a.png", b"png-data", "image/png")),
                ("files", ("b.txt", b"text-data", "text/plain")),
                ("files", ("c.jpg", b"jpg-data", "image/jpeg")),
            ],
        )
        data = resp.json()
        assert len(data["uploaded"]) == 2
        assert data["total_uploaded"] == 2
        assert "b.txt" in data["failed"]

    @pytest.mark.asyncio
    async def test_complete_upload(
        self, api_client: AsyncClient
    ) -> None:
        """完成上传会话返回 image_dir"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[("files", ("img.jpg", b"data", "image/jpeg"))],
        )

        resp = await api_client.post(f"/api/v1/uploads/{sid}/complete")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert data["file_count"] == 1
        assert data["total_size_bytes"] > 0
        assert len(data["image_dir"]) > 0

    @pytest.mark.asyncio
    async def test_complete_empty_session_rejected(
        self, api_client: AsyncClient
    ) -> None:
        """空会话不可完成"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        resp = await api_client.post(f"/api/v1/uploads/{sid}/complete")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_to_completed_session_rejected(
        self, api_client: AsyncClient
    ) -> None:
        """已完成会话不可再上传"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[("files", ("img.jpg", b"data", "image/jpeg"))],
        )
        await api_client.post(f"/api/v1/uploads/{sid}/complete")

        resp = await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[("files", ("img2.jpg", b"data2", "image/jpeg"))],
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_to_nonexistent_session(
        self, api_client: AsyncClient
    ) -> None:
        """不存在的会话返回 404"""
        resp = await api_client.post(
            "/api/v1/uploads/upl_nonexistent/files",
            files=[("files", ("img.jpg", b"data", "image/jpeg"))],
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_complete_nonexistent_session(
        self, api_client: AsyncClient
    ) -> None:
        """不存在的会话完成返回 404"""
        resp = await api_client.post(
            "/api/v1/uploads/upl_nonexistent/complete"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_with_directory_structure(
        self, api_client: AsyncClient
    ) -> None:
        """上传文件并指定相对路径，保留子目录结构"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        # httpx 混合 files + data 需要统一放 files 参数
        resp = await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[
                ("files", ("a.jpg", b"img-a", "image/jpeg")),
                ("files", ("b.jpg", b"img-b", "image/jpeg")),
                ("files", ("c.jpg", b"img-c", "image/jpeg")),
                ("paths", (None, "sub1/a.jpg")),
                ("paths", (None, "sub1/b.jpg")),
                ("paths", (None, "sub2/c.jpg")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_uploaded"] == 3
        # 返回的 uploaded 应包含子目录路径
        assert "sub1/a.jpg" in data["uploaded"]
        assert "sub2/c.jpg" in data["uploaded"]

        # 完成并检查目录结构
        resp = await api_client.post(f"/api/v1/uploads/{sid}/complete")
        assert resp.status_code == 200
        image_dir = resp.json()["image_dir"]

        from pathlib import Path
        upload_dir = Path(image_dir)
        assert (upload_dir / "sub1" / "a.jpg").exists()
        assert (upload_dir / "sub1" / "b.jpg").exists()
        assert (upload_dir / "sub2" / "c.jpg").exists()

    @pytest.mark.asyncio
    async def test_upload_paths_traversal_rejected(
        self, api_client: AsyncClient
    ) -> None:
        """路径穿越攻击应被拒绝，回退到安全文件名"""
        resp = await api_client.post("/api/v1/uploads")
        sid = resp.json()["session_id"]

        resp = await api_client.post(
            f"/api/v1/uploads/{sid}/files",
            files=[
                ("files", ("evil.jpg", b"img-data", "image/jpeg")),
                ("paths", (None, "../../etc/evil.jpg")),
            ],
        )
        assert resp.status_code == 200
        data = resp.json()
        # 路径穿越被拒绝后回退到安全文件名
        assert data["total_uploaded"] == 1
        # 文件应保存在会话目录内，不会穿越
        session = _sessions[sid]
        for f in session.upload_dir.rglob("*"):
            if f.is_file():
                assert str(f).startswith(str(session.upload_dir))


class TestCleanupExpiredSessions:
    """cleanup_expired_sessions 行为：TTL + 引用保护。

    回归测试对应 2026-04-23 的 bug：task.image_dir 复用 upload_dir，
    1h 后 cleanup_expired_sessions 把目录整个 rmtree，导致前端"原图预览"
    变烂图。修复后引用中的 upload_dir 必须被跳过。
    """

    @pytest.mark.asyncio
    async def test_expired_session_without_reference_is_removed(
        self,
    ) -> None:
        """无引用的过期 session：rmtree + 从 _sessions 弹出。"""
        import tempfile
        from datetime import datetime, timedelta
        from pathlib import Path

        from docrestore.api.upload import (
            UploadSession,
            _SESSION_TTL_SECONDS,
            cleanup_expired_sessions,
        )

        sid = "upl_orphan"
        upload_dir = Path(tempfile.mkdtemp(prefix="docrestore_test_"))
        (upload_dir / "a.jpg").write_bytes(b"x")
        _sessions[sid] = UploadSession(
            session_id=sid,
            upload_dir=upload_dir,
            created_at=datetime.now() - timedelta(
                seconds=_SESSION_TTL_SECONDS + 10,
            ),
        )

        await cleanup_expired_sessions()  # provider=None，退回纯 TTL

        assert sid not in _sessions
        assert not upload_dir.exists()  # noqa: ASYNC240

    @pytest.mark.asyncio
    async def test_expired_session_with_reference_is_preserved(
        self,
    ) -> None:
        """被任务引用的过期 session：保留目录 + 保留 _sessions 条目。

        关键场景：task.image_dir = upload_dir，用户在 1h+ 后仍预览原图，
        cleanup 必须跳过。
        """
        import tempfile
        from datetime import datetime, timedelta
        from pathlib import Path

        from docrestore.api.upload import (
            UploadSession,
            _SESSION_TTL_SECONDS,
            cleanup_expired_sessions,
        )

        sid = "upl_referenced"
        upload_dir = Path(tempfile.mkdtemp(prefix="docrestore_test_"))
        (upload_dir / "a.jpg").write_bytes(b"x")
        _sessions[sid] = UploadSession(
            session_id=sid,
            upload_dir=upload_dir,
            created_at=datetime.now() - timedelta(
                seconds=_SESSION_TTL_SECONDS + 10,
            ),
        )

        async def referenced() -> set[str]:
            return {str(upload_dir)}

        await cleanup_expired_sessions(referenced)

        assert sid in _sessions, "被引用的 session 不应从内存弹出"
        assert upload_dir.exists(), (  # noqa: ASYNC240
            "被引用的 upload_dir 不应被 rmtree"
        )
        assert (upload_dir / "a.jpg").exists()  # noqa: ASYNC240

        # 清理测试副作用
        import shutil
        _sessions.pop(sid, None)
        shutil.rmtree(upload_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_provider_exception_defers_cleanup(self) -> None:
        """provider 抛异常时保守跳过本轮，不误删已引用目录。"""
        import tempfile
        from datetime import datetime, timedelta
        from pathlib import Path

        from docrestore.api.upload import (
            UploadSession,
            _SESSION_TTL_SECONDS,
            cleanup_expired_sessions,
        )

        sid = "upl_probe_fail"
        upload_dir = Path(tempfile.mkdtemp(prefix="docrestore_test_"))
        _sessions[sid] = UploadSession(
            session_id=sid,
            upload_dir=upload_dir,
            created_at=datetime.now() - timedelta(
                seconds=_SESSION_TTL_SECONDS + 10,
            ),
        )

        async def broken() -> set[str]:
            raise RuntimeError("DB unavailable")

        await cleanup_expired_sessions(broken)

        assert sid in _sessions
        assert upload_dir.exists()  # noqa: ASYNC240

        import shutil
        _sessions.pop(sid, None)
        shutil.rmtree(upload_dir, ignore_errors=True)
