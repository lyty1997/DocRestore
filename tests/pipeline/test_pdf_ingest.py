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

"""process_tree 的 PDF 摄取入口展开端到端测试（纯 mock OCR，CI 友好）。

覆盖 Epic A A1-②：
- 单 PDF → 落 image_dir 根命中 process_many 快路，单 PipelineResult（doc_dir 空）
- 多 PDF → 分子目录走多文档分支，每个 PDF 一个结果（doc_dir = 净化 stem）
- 坏 / 损坏 PDF → 占位失败结果合入，复用部分失败聚合
- PDF 渲染页跳过 content_crop（无屏摄侧栏 UI，据 sentinel 判定，D8）
- 页标记链：anchored sidecar 保留 ``<!-- page: {stem}_page_NNNN.png -->``

断言均从输入派生（mock OCR 文本含渲染页文件名），不写死数据集标识符。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import PageOCR
from docrestore.pipeline.config import LLMConfig, PIIConfig, PipelineConfig
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.rate_controller import RateController
from tests.support.pdf_fixtures import make_pdf


@pytest.fixture(autouse=True)
def _fast_cold_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """冷启动超时缩到 0.5s，避免 mock 路径样本不足时白等。"""
    monkeypatch.setattr(RateController, "COLD_START_TIMEOUT_S", 0.5)


def _build_pipeline() -> Pipeline:
    """带 mock OCR 引擎的 Pipeline：每页 OCR 文本含其渲染文件名，关精修 / PII。"""
    cfg = PipelineConfig(
        llm=LLMConfig(model=""),  # 空 model → 无 refiner → 跳过精修
        pii=PIIConfig(enable=False),
    )
    pipeline = Pipeline(cfg)

    mock_engine = MagicMock()

    async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=f"正文 {image_path.name}",
            cleaned_text=f"正文 {image_path.name}",
        )

    mock_engine.ocr = AsyncMock(side_effect=_ocr)
    mock_engine.shutdown = AsyncMock(return_value=None)
    pipeline.set_ocr_engine(mock_engine)
    return pipeline


@pytest.mark.asyncio
async def test_single_pdf_root_fastpath(tmp_path: Path) -> None:
    """单 PDF → 落根命中快路，单结果（doc_dir 空），两页有序 OCR。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    make_pdf(image_dir / "doc.pdf", ["one", "two"])  # 2 页

    results = await _build_pipeline().process_tree(image_dir, output_dir)

    assert len(results) == 1
    assert results[0].error == ""
    assert results[0].doc_dir == ""
    # 渲染产物落 image_dir 根（单 PDF），零填充命名
    assert (image_dir / "doc_page_0001.png").exists()
    assert (image_dir / "doc_page_0002.png").exists()
    # mock OCR 文本含渲染页文件名 → markdown 含两页且保序
    md = results[0].markdown
    assert "doc_page_0001.png" in md
    assert "doc_page_0002.png" in md
    assert md.index("doc_page_0001.png") < md.index("doc_page_0002.png")
    # content_crop 对 PDF 关闭 → 无裁剪 debug 目录
    assert not (output_dir / ".content_crop").exists()


@pytest.mark.asyncio
async def test_single_pdf_anchored_markers(tmp_path: Path) -> None:
    """anchored sidecar 保留页标记，指向渲染页文件名（页标识契约）。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    make_pdf(image_dir / "doc.pdf", ["one", "two"])

    await _build_pipeline().process_tree(image_dir, output_dir)

    anchored = sorted(output_dir.glob("**/*anchored.md"))
    assert anchored, "应生成 anchored sidecar"
    marker_text = anchored[0].read_text(encoding="utf-8")
    assert "<!-- page: doc_page_0001.png -->" in marker_text
    assert "<!-- page: doc_page_0002.png -->" in marker_text


@pytest.mark.asyncio
async def test_multi_pdf_two_results(tmp_path: Path) -> None:
    """多 PDF → 分子目录，每个 PDF 一个结果（doc_dir = stem）。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    make_pdf(image_dir / "alpha.pdf", ["a"])  # 1 页
    make_pdf(image_dir / "beta.pdf", ["b"])  # 1 页

    results = await _build_pipeline().process_tree(image_dir, output_dir)

    assert len(results) == 2
    by_dir = {r.doc_dir: r for r in results}
    assert set(by_dir) == {"alpha", "beta"}
    assert by_dir["alpha"].error == ""
    assert "alpha_page_0001.png" in by_dir["alpha"].markdown
    assert by_dir["beta"].error == ""
    assert "beta_page_0001.png" in by_dir["beta"].markdown
    # 多 PDF 分子目录
    assert (image_dir / "alpha" / "alpha_page_0001.png").exists()
    assert (image_dir / "beta" / "beta_page_0001.png").exists()


@pytest.mark.asyncio
async def test_corrupt_pdf_partial_failure(tmp_path: Path) -> None:
    """坏 PDF（多 PDF 批中之一）→ 占位失败结果，其他正常产出。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    make_pdf(image_dir / "good.pdf", ["g"])  # 1 页
    (image_dir / "bad.pdf").write_bytes(b"%PDF-1.4 broken \x00\x01")

    results = await _build_pipeline().process_tree(image_dir, output_dir)

    assert len(results) == 2
    by_dir = {r.doc_dir: r for r in results}
    assert by_dir["good"].error == ""
    assert "good_page_0001.png" in by_dir["good"].markdown
    assert by_dir["bad"].error != ""  # 坏 PDF → 占位失败
    assert by_dir["bad"].markdown == ""


@pytest.mark.asyncio
async def test_pure_image_dir_unaffected(tmp_path: Path) -> None:
    """无 PDF 的纯图片目录：展开零作用，照常单结果。"""
    image_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    image_dir.mkdir()
    (image_dir / "p1.jpg").write_bytes(b"fake")
    (image_dir / "p2.jpg").write_bytes(b"fake")

    results = await _build_pipeline().process_tree(image_dir, output_dir)

    assert len(results) == 1
    assert results[0].doc_dir == ""
    assert results[0].error == ""
    # 无 sentinel → content_crop 未被 PDF 分支关闭（不在此断言其行为，仅证未崩）
