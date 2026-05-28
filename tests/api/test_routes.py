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

"""API 路由测试

使用公共 fixture 提供 AsyncClient 与测试工作目录，不依赖 GPU。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.mark.usefixtures("require_ocr_data")
class TestRoutes:
    """API 路由功能测试"""

    @pytest.mark.asyncio
    async def test_create_and_get_task(
        self,
        api_client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """POST 创建任务 → GET 查询状态

        POST 和 GET 的 task_id 必须一致；列表接口里也必须能看到它，
        并且列表项里的 image_dir / output_dir 必须原样回显请求体。
        （TaskResponse 不含这两个字段，所以用列表接口交叉验证。）
        """
        image_dir = str(work_dir / "input")
        output_dir = str(work_dir / "output")
        resp = await api_client.post(
            "/api/v1/tasks",
            json={"image_dir": image_dir, "output_dir": output_dir},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["task_id"]
        assert data["status"] == "pending"

        task_id = data["task_id"]

        resp = await api_client.get(
            f"/api/v1/tasks/{task_id}"
        )
        assert resp.status_code == 200
        get_data = resp.json()
        assert get_data["task_id"] == task_id
        assert get_data["status"] in (
            "pending", "processing", "completed", "failed",
        )

        # 列表接口交叉验证请求体字段没有被静默丢弃
        list_resp = await api_client.get("/api/v1/tasks")
        item = next(
            t for t in list_resp.json()["tasks"]
            if t["task_id"] == task_id
        )
        assert item["image_dir"] == image_dir
        assert item["output_dir"] == output_dir

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(
        self, api_client: AsyncClient
    ) -> None:
        """查询不存在的任务返回 404"""
        resp = await api_client.get(
            "/api/v1/tasks/nonexistent"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_and_get_result(
        self,
        api_client: AsyncClient,
        work_dir: Path,
    ) -> None:
        """创建任务 → 等待完成 → 获取结果"""
        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": str(work_dir / "input"),
                "output_dir": str(work_dir / "output"),
            },
        )
        task_id = resp.json()["task_id"]
        status = "pending"

        for _ in range(50):
            resp = await api_client.get(
                f"/api/v1/tasks/{task_id}"
            )
            status = resp.json()["status"]
            if status in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert status == "completed"

        resp = await api_client.get(
            f"/api/v1/tasks/{task_id}/result"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["markdown"] != ""
        assert isinstance(data["doc_title"], str)
        assert isinstance(data["doc_dir"], str)
        # output_path 必须指向真实存在的产物文件
        assert data["output_path"] != ""
        output_path = Path(data["output_path"])
        assert output_path.exists()  # noqa: ASYNC240
        assert output_path.is_file()  # noqa: ASYNC240
        # 产物内容与接口返回的 markdown 去掉 page 锚点后一致。
        # 2026-04-23 起 API markdown 保留 `<!-- page: xxx -->` 锚点供前端
        # 左右同步滚动对齐；disk 版本剥除注释让下载用户看到干净文本。
        import re
        on_disk = output_path.read_text(encoding="utf-8")  # noqa: ASYNC240
        api_stripped = re.sub(
            r"<!--\s*page:\s*[^>]*-->\n?", "", data["markdown"],
        )
        api_stripped = re.sub(r"\n{3,}", "\n\n", api_stripped).strip() + "\n"
        assert on_disk == api_stripped

    @pytest.mark.asyncio
    async def test_get_result_nonexistent(
        self, api_client: AsyncClient
    ) -> None:
        """不存在的任务获取结果返回 404"""
        resp = await api_client.get(
            "/api/v1/tasks/nonexistent/result"
        )
        assert resp.status_code == 404


class TestValidation:
    """请求校验测试"""

    @pytest.mark.asyncio
    async def test_empty_body(
        self, api_client: AsyncClient
    ) -> None:
        """空 body 返回 422"""
        resp = await api_client.post(
            "/api/v1/tasks", json={}
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_image_dir(
        self, api_client: AsyncClient
    ) -> None:
        """缺少 image_dir 返回 422"""
        resp = await api_client.post(
            "/api/v1/tasks",
            json={"output_dir": str(Path(tempfile.gettempdir()) / "out")},
        )
        assert resp.status_code == 422


class TestFilesystemBrowse:
    """/filesystem/dirs 浏览接口"""

    @pytest.mark.asyncio
    async def test_browse_dirs_default_excludes_files(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """默认不带 include_files 只返回目录。"""
        (tmp_path / "sub1").mkdir()
        (tmp_path / "photo.jpg").write_bytes(b"x")
        (tmp_path / "photo.png").write_bytes(b"x")

        resp = await api_client.get(
            "/api/v1/filesystem/dirs",
            params={"path": str(tmp_path)},
        )
        assert resp.status_code == 200
        data = resp.json()
        names = {e["name"] for e in data["entries"]}
        assert "sub1" in names
        assert "photo.jpg" not in names
        assert "photo.png" not in names

    @pytest.mark.asyncio
    async def test_browse_dirs_include_files(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """include_files=true 时同时返回图片文件，附带 size_bytes。"""
        (tmp_path / "sub1").mkdir()
        (tmp_path / "photo.jpg").write_bytes(b"jpegdata")
        (tmp_path / "note.txt").write_bytes(b"skip")

        resp = await api_client.get(
            "/api/v1/filesystem/dirs",
            params={"path": str(tmp_path), "include_files": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        by_name = {e["name"]: e for e in data["entries"]}
        assert "sub1" in by_name
        assert by_name["sub1"]["is_dir"] is True
        assert "photo.jpg" in by_name
        assert by_name["photo.jpg"]["is_dir"] is False
        assert by_name["photo.jpg"]["size_bytes"] == len(b"jpegdata")
        # 非图片扩展名必须被过滤掉
        assert "note.txt" not in by_name

    @pytest.mark.asyncio
    async def test_browse_dirs_image_count_preview(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """目录条目在 include_files=true 时携带 image_count（顶层图片数）。"""
        sub = tmp_path / "album"
        sub.mkdir()
        (sub / "a.jpg").write_bytes(b"1")
        (sub / "b.PNG").write_bytes(b"2")
        (sub / "note.txt").write_bytes(b"skip")
        # 子目录里的图片不应计入顶层
        nested = sub / "inner"
        nested.mkdir()
        (nested / "c.jpg").write_bytes(b"3")

        empty = tmp_path / "empty"
        empty.mkdir()

        resp = await api_client.get(
            "/api/v1/filesystem/dirs",
            params={"path": str(tmp_path), "include_files": "true"},
        )
        assert resp.status_code == 200
        by_name = {e["name"]: e for e in resp.json()["entries"]}
        assert by_name["album"]["image_count"] == 2
        assert by_name["empty"]["image_count"] == 0


class TestStageServerSource:
    """/sources/server stage 接口"""

    @pytest.mark.asyncio
    async def test_stage_files_creates_symlinks(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """合法图片路径 → 返回 image_dir，目录内存在对应符号链接。"""
        src1 = tmp_path / "a.jpg"
        src2 = tmp_path / "b.png"
        src1.write_bytes(b"jpeg")
        src2.write_bytes(b"png")

        resp = await api_client.post(
            "/api/v1/sources/server",
            json={"paths": [str(src1), str(src2)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_count"] == 2

        image_dir = Path(data["image_dir"])
        assert image_dir.is_dir()  # noqa: ASYNC240
        # 符号链接指向原文件
        linked_a = image_dir / "a.jpg"
        linked_b = image_dir / "b.png"
        assert linked_a.is_symlink()  # noqa: ASYNC240
        assert linked_b.is_symlink()  # noqa: ASYNC240
        assert linked_a.resolve() == src1.resolve()  # noqa: ASYNC240
        assert linked_b.resolve() == src2.resolve()  # noqa: ASYNC240

    @pytest.mark.asyncio
    async def test_stage_empty_paths_rejected(
        self,
        api_client: AsyncClient,
    ) -> None:
        """空 paths 返回 400。"""
        resp = await api_client.post(
            "/api/v1/sources/server",
            json={"paths": []},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_stage_rejects_non_image(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """非图片扩展名返回 400。"""
        bad = tmp_path / "doc.md"
        bad.write_bytes(b"#")
        resp = await api_client.post(
            "/api/v1/sources/server",
            json={"paths": [str(bad)]},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_stage_rejects_relative_path(
        self,
        api_client: AsyncClient,
    ) -> None:
        """相对路径返回 400。"""
        resp = await api_client.post(
            "/api/v1/sources/server",
            json={"paths": ["not/absolute.jpg"]},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_stage_dedupes_duplicate_filenames(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """两个同名文件位于不同目录 → 第二个以 _1 后缀落链。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "x.jpg").write_bytes(b"1")
        (d2 / "x.jpg").write_bytes(b"2")

        resp = await api_client.post(
            "/api/v1/sources/server",
            json={"paths": [str(d1 / "x.jpg"), str(d2 / "x.jpg")]},
        )
        assert resp.status_code == 200
        image_dir = Path(resp.json()["image_dir"])
        names = sorted(p.name for p in image_dir.iterdir())  # noqa: ASYNC240
        assert names == ["x.jpg", "x_1.jpg"]
