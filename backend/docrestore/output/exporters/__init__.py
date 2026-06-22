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

"""导出器注册表（Epic D）。

phase-2 新增 xlsx/pptx 时只需在此追加一行注册，下载路由零改动。
详见 ``docs/zh/export-mode.md``。
"""

from __future__ import annotations

from docrestore.output.exporters.base import (
    EXPORT_CACHE_DIRNAME,
    Exporter,
    ExportError,
    ExportFailed,
    ExportToolUnavailable,
    export_cache_path,
    export_content_hash,
)
from docrestore.output.exporters.docx import DocxExporter
from docrestore.output.exporters.pdf import PdfExporter

#: 格式名 → 导出器实例。导出器无状态，模块加载时实例化即可。
EXPORTERS: dict[str, Exporter] = {
    "docx": DocxExporter(),
    "pdf": PdfExporter(),
}

#: 支持的导出格式集合（供下载路由 fail-closed 白名单校验）
SUPPORTED_FORMATS: frozenset[str] = frozenset(EXPORTERS)


def get_exporter(fmt: str) -> Exporter | None:
    """按格式名取导出器；未知格式返回 ``None``。"""
    return EXPORTERS.get(fmt)


__all__ = [
    "EXPORT_CACHE_DIRNAME",
    "EXPORTERS",
    "SUPPORTED_FORMATS",
    "Exporter",
    "ExportError",
    "ExportFailed",
    "ExportToolUnavailable",
    "export_cache_path",
    "export_content_hash",
    "get_exporter",
]
