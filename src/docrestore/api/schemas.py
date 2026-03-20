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

"""API 请求/响应 pydantic 模型"""

from __future__ import annotations

from pydantic import BaseModel


class LLMConfigRequest(BaseModel):
    """LLM 配置（请求级覆盖）"""

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    max_chars_per_segment: int | None = None


class CreateTaskRequest(BaseModel):
    """创建任务请求"""

    image_dir: str
    output_dir: str | None = None
    llm: LLMConfigRequest | None = None


class ProgressResponse(BaseModel):
    """进度信息"""

    stage: str
    current: int
    total: int
    percent: float
    message: str


class TaskResponse(BaseModel):
    """任务状态响应"""

    task_id: str
    status: str
    progress: ProgressResponse | None = None
    error: str | None = None


class TaskResultResponse(BaseModel):
    """任务结果响应"""

    task_id: str
    output_path: str
    markdown: str
