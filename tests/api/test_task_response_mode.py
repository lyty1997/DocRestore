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

"""TaskResponse 的 mode / enable_refine 字段单元测试。

供前端进度区按任务调整「LLM 精修」第二轨展示（关精修时文档模式隐藏、
PPT/代码模式改名「后处理」）。
"""

from __future__ import annotations

from docrestore.api.routes import _resolve_task_mode
from docrestore.api.schemas import TaskResponse
from docrestore.pipeline.config import (
    CodeRestoreConfig,
    PowerPointRestoreConfig,
)
from docrestore.pipeline.task_manager import Task, TaskStatus


def _make_task(
    *,
    code: CodeRestoreConfig | None = None,
    ppt: PowerPointRestoreConfig | None = None,
) -> Task:
    """构造最小 Task（仅填模式判定需要的 code/ppt 配置）。"""
    return Task(
        task_id="t1",
        status=TaskStatus.PENDING,
        image_dir="in",
        output_dir="out",
        code=code,
        ppt=ppt,
    )


class TestResolveTaskMode:
    """_resolve_task_mode：从任务配置推导 doc/code/ppt。"""

    def test_no_config_is_doc(self) -> None:
        """未带 code/ppt 配置 → 文档模式。"""
        assert _resolve_task_mode(_make_task()) == "doc"

    def test_code_enabled_is_code(self) -> None:
        """code.enable=True → 代码模式。"""
        task = _make_task(code=CodeRestoreConfig(enable=True))
        assert _resolve_task_mode(task) == "code"

    def test_ppt_enabled_is_ppt(self) -> None:
        """ppt.enable=True → PPT 模式。"""
        task = _make_task(ppt=PowerPointRestoreConfig(enable=True))
        assert _resolve_task_mode(task) == "ppt"

    def test_disabled_flags_fall_back_to_doc(self) -> None:
        """带 code/ppt 配置但 enable=False → 仍为文档模式。"""
        task = _make_task(
            code=CodeRestoreConfig(enable=False),
            ppt=PowerPointRestoreConfig(enable=False),
        )
        assert _resolve_task_mode(task) == "doc"


class TestTaskResponseDefaults:
    """TaskResponse 新字段默认值：兼容仅含 task_id+status 的创建响应/旧前端。"""

    def test_defaults_refine_on_doc(self) -> None:
        """仅给 task_id+status → enable_refine 默认 True、mode 默认 doc。"""
        resp = TaskResponse(task_id="t1", status="pending")
        assert resp.enable_refine is True
        assert resp.mode == "doc"
