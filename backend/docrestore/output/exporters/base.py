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

"""导出器协议与公共工具（Epic D）。

导出在**下载环节**按需运行：输入是已落盘的 ``document.md`` + ``images/``，
输出对应格式文件（docx/pdf/...）。不进 pipeline，不碰任务配置。
详见 ``docs/zh/export-mode.md``。

所有导出器都是阻塞的子进程 / 库调用，由下载路由用 ``asyncio.to_thread`` 包裹后调用。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

#: 导出产物缓存目录名（落在每个 doc_dir 下，不进 asset 白名单、不裸打进 zip）
EXPORT_CACHE_DIRNAME = ".exports"


class ExportError(Exception):
    """导出相关异常基类。"""


class ExportToolUnavailable(ExportError):
    """外部导出依赖（pandoc / weasyprint）缺失 —— fail-closed。"""

    def __init__(self, tool: str) -> None:
        """记录缺失的工具名，供上层映射 503 + i18n params。"""
        super().__init__(f"导出依赖不可用: {tool}")
        self.tool = tool


class ExportFailed(ExportError):
    """导出过程失败（子进程非零退出 / 渲染异常）。"""

    def __init__(self, tool: str, fmt: str, reason: str) -> None:
        """携带工具名、格式与原因，供上层映射 500 + i18n params。"""
        super().__init__(f"导出失败[{fmt}/{tool}]: {reason}")
        self.tool = tool
        self.fmt = fmt
        self.reason = reason


@runtime_checkable
class Exporter(Protocol):
    """把 ``document.md`` (+``images/``) 导出为某格式的协议。

    实现类需提供：
    - ``suffix``：输出文件扩展名（不含点），如 ``"docx"`` / ``"pdf"``。
    - ``tool``：底层工具名（错误提示 / i18n params 用）。
    - ``ensure_available()``：依赖缺失时抛 :class:`ExportToolUnavailable`。
    - ``export()``：生成产物；失败抛 :class:`ExportFailed`。
    """

    #: 输出文件扩展名（不含点）
    suffix: str
    #: 底层工具名（错误提示用）
    tool: str

    def ensure_available(self) -> None:
        """依赖缺失时抛 :class:`ExportToolUnavailable`。"""
        ...

    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None:
        """从 ``doc_md`` (+``assets_dir``) 生成 ``out_path``。

        失败抛 :class:`ExportFailed`；依赖缺失抛 :class:`ExportToolUnavailable`。
        """
        ...


def export_content_hash(doc_md: Path) -> str:
    """以 ``document.md`` 内容的 sha256 前 16 位做缓存键。

    内容不变即复用产物，避免每次下载都重跑导出子进程。
    """
    digest = hashlib.sha256(doc_md.read_bytes()).hexdigest()
    return digest[:16]


def export_cache_path(doc_dir: Path, suffix: str, content_hash: str) -> Path:
    """导出产物缓存路径：``{doc_dir}/.exports/{content_hash}.{suffix}``。"""
    return doc_dir / EXPORT_CACHE_DIRNAME / f"{content_hash}.{suffix}"
