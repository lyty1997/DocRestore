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

"""docx 导出器（D2 用 pandoc 实现）。

D1 阶段先以 stub 打通契约（route → exporter → 缓存 → zip → 前端），
``export()`` 写占位文件、``ensure_available()`` 恒可用。D2 将 ``export()`` 替换为
``pandoc <md> -o <docx> --resource-path=<assets>``，``ensure_available()`` 改
``shutil.which("pandoc")`` 检查。详见 ``docs/zh/export-mode.md`` §6。
"""

from __future__ import annotations

from pathlib import Path

#: D1 占位文件头（D2 替换为真实 pandoc 转换后即不再出现）
_STUB_HEADER = (
    "DocRestore 导出占位（D1 stub）。"
    "真实 docx 由 D2 pandoc 实现，详见 docs/zh/export-mode.md。\n\n"
)


class DocxExporter:
    """``document.md`` → docx。D1 stub；D2 接 pandoc。"""

    suffix = "docx"
    tool = "pandoc"

    def ensure_available(self) -> None:
        """D1 stub 无外部依赖恒可用；D2 改为 ``shutil.which("pandoc")`` 检查。"""
        return

    def export(
        self,
        doc_md: Path,
        assets_dir: Path,  # noqa: ARG002 — D1 stub 不用素材目录；D2 传 --resource-path
        out_path: Path,
    ) -> None:
        """D1：写占位文件（含源 markdown，便于契约测试从输入派生断言）。"""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body = doc_md.read_text(encoding="utf-8")
        out_path.write_text(_STUB_HEADER + body, encoding="utf-8")
