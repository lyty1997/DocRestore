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

"""#96：降级（VL 退本地 / PDF 缺页）透到 PipelineResult.warnings 的单元测试。

只测纯逻辑（不起 OCR / 子进程）：缺页按 doc_dir 匹配挂 warning，引擎降级码转任务级
warning。两者均保持"降级不失败"——只动 warnings、不动 error。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.models import PipelineResult
from docrestore.pipeline.pipeline import Pipeline, _apply_pdf_missing_warnings


def _result(doc_dir: str) -> PipelineResult:
    return PipelineResult(
        output_path=Path("document.md"), markdown="", doc_dir=doc_dir,
    )


def test_apply_pdf_missing_warnings_matches_by_doc_dir() -> None:
    """缺页按 doc_dir 匹配（多 PDF=stem），命中挂 warning、不误伤其他、不动 error。"""
    results = [_result(""), _result("a"), _result("b")]

    _apply_pdf_missing_warnings(results, {"a": 3})

    assert results[0].warnings == []
    assert len(results[1].warnings) == 1
    assert results[1].warnings[0].code == "pdf_pages_missing"
    assert results[1].warnings[0].params["count"] == 3  # 从输入 {"a": 3} 派生
    assert results[1].error == ""  # 仍 COMPLETED，不翻失败
    assert results[2].warnings == []


def test_apply_pdf_missing_warnings_single_pdf_root_key() -> None:
    """单 PDF 落根：doc_dir "" 命中根缺页。"""
    results = [_result("")]

    _apply_pdf_missing_warnings(results, {"": 2})

    assert len(results[0].warnings) == 1
    assert results[0].warnings[0].code == "pdf_pages_missing"
    assert results[0].warnings[0].params["count"] == 2  # 从输入 {"": 2} 派生


def test_apply_pdf_missing_warnings_empty_map_is_noop() -> None:
    """无缺页 → 不挂任何 warning。"""
    results = [_result("")]

    _apply_pdf_missing_warnings(results, {})

    assert results[0].warnings == []


def test_engine_degraded_warnings_maps_captured_reason() -> None:
    """降级原因码 → 任务级 warning（空原因→空）。

    #96 修复后该方法是纯静态映射，原因由生产者在本任务 ensure() 时刻同步捕获后透传，
    不再读引擎全局 live 标志（杜绝并发混模式任务互相污染的误报/漏报）。
    """
    # 空原因（未降级 / 无 engine_manager 时生产者写入 ""）→ 空
    assert Pipeline._engine_degraded_warnings("") == []

    for code in ("vl_no_server_python", "vl_server_python_missing"):
        out = Pipeline._engine_degraded_warnings(code)
        assert len(out) == 1
        assert out[0].code == "vl_fell_back_to_local"

    # 未知码也透出、不静默
    assert Pipeline._engine_degraded_warnings("something_else") != []
