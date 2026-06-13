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

"""output_dir 边界守卫单测 + create 端点接线（#34 任意目录删除防护）。

两道防线：
1. 建任务（``routes._resolve_output_dir`` / POST /tasks）——越界 400 fail-fast。
2. 删除 sink（``task_manager.delete_task`` rmtree 前二次校验）——见
   ``tests/pipeline/test_task_manager.py::TestDeleteTaskBoundary``。

全部路径从 ``tmp_path`` 派生、工作根用 ``work_root=`` 参数或 env 显式注入，不写死
任何真实目录（尤其不碰真实工作区）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from docrestore.api.errors import APIErrorCode, ApiBusinessError
from docrestore.api.routes import _resolve_output_dir
from docrestore.api.schemas import CreateTaskRequest
from docrestore.pipeline.path_guard import (
    OutputDirRejected,
    output_dir_within_root,
    resolve_work_root,
    validate_output_dir,
)


class TestValidateOutputDir:
    """纯路径边界判定（显式传 work_root，摆脱真实 env / tempdir）。"""

    def test_accepts_strict_subdir(self, tmp_path: Path) -> None:
        """工作根的严格子目录被放行，返回解析后的绝对路径。"""
        root = tmp_path / "root"
        root.mkdir()
        target = root / "task-out"
        assert validate_output_dir(str(target), work_root=root) == target.resolve()

    def test_rejects_root_itself(self, tmp_path: Path) -> None:
        """output_dir 等于工作根本身 = 授权删整个根，拒。"""
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(OutputDirRejected):
            validate_output_dir(str(root), work_root=root)

    def test_rejects_outside_sibling(self, tmp_path: Path) -> None:
        """工作根之外的兄弟目录被拒（核心攻击面：任意目录）。"""
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(OutputDirRejected):
            validate_output_dir(str(tmp_path / "outside"), work_root=root)

    def test_rejects_parent_escape(self, tmp_path: Path) -> None:
        """`..` 逃逸：root/../secret 解析到根外被拒。"""
        root = tmp_path / "root"
        root.mkdir()
        escape = root / ".." / "secret"
        with pytest.raises(OutputDirRejected):
            validate_output_dir(str(escape), work_root=root)

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        """根下的符号链接指向根外 → resolve 跟随后越界被拒（TOCTOU 旁路）。"""
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "link"
        link.symlink_to(outside)
        with pytest.raises(OutputDirRejected):
            validate_output_dir(str(link), work_root=root)

    def test_rejects_empty(self, tmp_path: Path) -> None:
        """空 / 纯空白路径被拒（不退化成当前工作目录）。"""
        with pytest.raises(OutputDirRejected):
            validate_output_dir("   ", work_root=tmp_path)


class TestOutputDirWithinRoot:
    """非抛版（删除 sink 用）：越界 / 根本身 False，根下 True。"""

    def test_true_for_subdir(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        assert output_dir_within_root(str(root / "x"), work_root=root) is True

    def test_false_for_outside(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        assert (
            output_dir_within_root(str(tmp_path / "y"), work_root=root) is False
        )

    def test_false_for_root_itself(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        assert output_dir_within_root(str(root), work_root=root) is False


class TestResolveWorkRoot:
    """工作根来源：env override / 默认 tempdir / 空 env 回退。"""

    def test_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DOCRESTORE_WORK_ROOT", str(tmp_path))
        assert resolve_work_root() == tmp_path.resolve()

    def test_default_is_tempdir(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DOCRESTORE_WORK_ROOT", raising=False)
        assert resolve_work_root() == Path(tempfile.gettempdir()).resolve()

    def test_blank_env_falls_back_to_tempdir(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DOCRESTORE_WORK_ROOT", "   ")
        assert resolve_work_root() == Path(tempfile.gettempdir()).resolve()


class TestResolveOutputDirHelper:
    """routes._resolve_output_dir：空串→None、越界→ApiBusinessError 400。"""

    def test_blank_becomes_none(self) -> None:
        """纯空白 output_dir 归一为 None（让 create_task 走安全默认）。"""
        req = CreateTaskRequest.model_validate(
            {"image_dir": "in", "output_dir": "   "},
        )
        assert _resolve_output_dir(req) is None

    def test_none_stays_none(self) -> None:
        """未提供 output_dir → None。"""
        req = CreateTaskRequest.model_validate({"image_dir": "in"})
        assert _resolve_output_dir(req) is None

    def test_inbounds_passes_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """根下的合法 output_dir 原样透传（去空白后）。"""
        monkeypatch.setenv("DOCRESTORE_WORK_ROOT", str(tmp_path))
        target = str(tmp_path / "out")
        req = CreateTaskRequest.model_validate(
            {"image_dir": "in", "output_dir": target},
        )
        assert _resolve_output_dir(req) == target

    def test_outbounds_raises_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """越界 output_dir → ApiBusinessError(OUTPUT_DIR_REJECTED, 400)。"""
        root = tmp_path / "root"
        root.mkdir()
        monkeypatch.setenv("DOCRESTORE_WORK_ROOT", str(root))
        req = CreateTaskRequest.model_validate(
            {"image_dir": "in", "output_dir": str(tmp_path / "outside")},
        )
        with pytest.raises(ApiBusinessError) as ei:
            _resolve_output_dir(req)
        assert ei.value.status_code == 400
        assert ei.value.code is APIErrorCode.OUTPUT_DIR_REJECTED


class TestCreateEndpointBoundary:
    """端点级：POST /tasks 对越界 output_dir 在建任务前 400 拦截（#34 wiring）。"""

    @pytest.mark.asyncio
    async def test_outbounds_output_dir_rejected(
        self,
        api_client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """带工作根外 output_dir 的 POST → 400，code=OUTPUT_DIR_REJECTED。

        守卫在合成阶段触发、早于真正建任务，故 image_dir 用占位串即可。
        把工作根经 env 收窄到 tmp_path/root，使 tmp_path/outside 必然越界——
        不依赖真实 tempdir 与 tmp_path 的相对关系，结果确定。状态码 400 区别于
        pydantic 422；detail 含"输出目录"坐实是本守卫拒绝（机器可读 code 已在
        TestResolveOutputDirHelper 单元层断言，此处 fixture 用默认异常处理器、
        响应体仅含 detail）。
        """
        root = tmp_path / "root"
        root.mkdir()
        monkeypatch.setenv("DOCRESTORE_WORK_ROOT", str(root))
        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": "input-dir",
                "output_dir": str(tmp_path / "outside"),
            },
        )
        assert resp.status_code == 400
        assert "输出目录" in resp.json()["detail"]
