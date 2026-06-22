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

"""PDF 导出器（D3 用 weasyprint 实现）。

D1 阶段先以 stub 打通契约。D3 将链路实现为
``md → HTML(pandoc --mathml) → PDF(weasyprint)``，``ensure_available()`` 改为探测
weasyprint 可导入（缺 cairo/pango 系统库时 fail-closed）。公式机制
（weasyprint MathML vs KaTeX 预渲染）由 D3 实现期 spike 定，详见
``docs/zh/export-mode.md`` §7。
"""

from __future__ import annotations

from pathlib import Path

#: D1 占位文件头（D3 替换为真实 weasyprint 渲染后即不再出现）
_STUB_HEADER = (
    "DocRestore 导出占位（D1 stub）。"
    "真实 PDF 由 D3 weasyprint 实现，详见 docs/zh/export-mode.md。\n\n"
)


class PdfExporter:
    """``document.md`` → PDF。D1 stub；D3 接 weasyprint。"""

    suffix = "pdf"
    tool = "weasyprint"

    def ensure_available(self) -> None:
        """D1 stub 无外部依赖恒可用；D3 改为探测 ``import weasyprint``。"""
        return

    def export(
        self,
        doc_md: Path,
        assets_dir: Path,  # noqa: ARG002 — D1 stub 不用素材目录；D3 内联 images/
        out_path: Path,
    ) -> None:
        """D1：写占位文件（含源 markdown，便于契约测试从输入派生断言）。"""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body = doc_md.read_text(encoding="utf-8")
        out_path.write_text(_STUB_HEADER + body, encoding="utf-8")
