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

"""LLM 凭据从环境变量恢复（#37）。

持久化层不再把明文 ``api_key`` 写入 SQLite（见 ``persistence/database.py``），
因此从 DB 还原（resume / 重启水合）出来的 ``LLMConfig`` 其 ``api_key`` 为空。
本模块提供单点回填：仅当配置内 ``api_key`` 为空、且环境变量存在时从环境补回，
绝不覆盖请求里显式带入的 key。环境变量名集中在此，避免多处硬编码漂移。
"""

from __future__ import annotations

import os

from docrestore.pipeline.config import LLMConfig

#: 云端 LLM API 密钥的环境变量名。应用启动回填全局默认配置、任务水合回填均读
#: 此键，保持单一真相源（改名只此一处）。
ENV_LLM_API_KEY = "DOCRESTORE_LLM_API_KEY"


def refill_api_key_from_env(llm: LLMConfig) -> LLMConfig:
    """从环境变量回填 LLM ``api_key``，返回（可能更新的）配置。

    仅在 ``llm.api_key`` 为空时回填——已显式带 key 的配置原样返回，绝不被环境
    覆盖；环境变量缺失或纯空白时同样原样返回。回填走 ``model_copy`` 返回新对象，
    不原地修改入参（不可变约定）。

    参数:
        llm: 从 DB 还原或默认构造的 LLM 配置（``api_key`` 可能为空）。

    返回:
        需回填时为带 ``api_key`` 的新 ``LLMConfig``；否则为原对象。
    """
    if llm.api_key:
        return llm
    env_key = os.environ.get(ENV_LLM_API_KEY, "").strip()
    if not env_key:
        return llm
    return llm.model_copy(update={"api_key": env_key})
