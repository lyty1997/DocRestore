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

"""PDF 渲染模块 render_pdf_to_dir / safe_pdf_stem 的单元测试。

造 PDF fixture 用 Pillow（runtime 依赖）：``Image.save(save_all=True)``。
不可用 reportlab（非依赖）/ pypdfium2（只渲不造）。断言全部从入参派生，
不写死任何数据集标识符。
"""

from __future__ import annotations

import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from docrestore.pipeline.config import PdfRenderConfig
from docrestore.pipeline.render.pdf import render_pdf_to_dir, safe_pdf_stem
from tests.support.pdf_fixtures import make_pdf


def test_safe_pdf_stem_sanitizes() -> None:
    """净化：Path.stem 剥目录、括号/空格转下划线折叠、保留 CJK、空回退 pdf。"""
    assert safe_pdf_stem("report.pdf") == "report"
    assert safe_pdf_stem("a b).pdf") == "a_b"  # 空格 + ) → _ 并折叠去尾
    assert safe_pdf_stem("../../etc/passwd.pdf") == "passwd"  # Path.stem 剥目录
    assert safe_pdf_stem("中文报告.pdf") == "中文报告"  # CJK 保留
    assert safe_pdf_stem(".pdf") == "pdf"  # 空 stem 回退


def test_safe_pdf_stem_collision_is_callers_job() -> None:
    """文档化：净化后撞名（去重由调用方负责，本函数不去重）。"""
    assert safe_pdf_stem("a b.pdf") == safe_pdf_stem("a_b.pdf") == "a_b"


def test_render_basic(tmp_path: Path) -> None:
    """3 页 PDF → 3 个零填充命名 PNG，字典序 = 页序，均为有效 RGB 图。"""
    labels = ["PG-ONE", "PG-TWO", "PG-THREE"]
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, labels)
    out = tmp_path / "out"

    count = render_pdf_to_dir(pdf, out, cfg=PdfRenderConfig(), name_prefix="doc_")

    assert count == len(labels)
    names = sorted(p.name for p in out.glob("doc_page_*.png"))
    assert names == [
        f"doc_page_{i:04d}.png" for i in range(1, len(labels) + 1)
    ]
    for name in names:
        with Image.open(out / name) as im:
            assert im.mode == "RGB"
            assert min(im.size) > 0


def test_sentinel_records_digest(tmp_path: Path) -> None:
    """渲染完成落 .render_done.json，记录哈希 + 页数。"""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["A", "B"])
    out = tmp_path / "out"

    render_pdf_to_dir(pdf, out, cfg=PdfRenderConfig(), name_prefix="d_")

    data = json.loads((out / ".render_done.json").read_text(encoding="utf-8"))
    assert data["rendered"] == 2
    assert data["source_pages"] == 2
    assert data["expected_pages"] == 2  # 完整渲染：expected == rendered
    assert len(data["pdf_sha256"]) == 64


def test_idempotent_short_circuit(tmp_path: Path) -> None:
    """sentinel 命中则整本跳过：删掉一页 PNG 后二次调用不重渲染。"""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["A", "B"])
    out = tmp_path / "out"
    cfg = PdfRenderConfig()

    first = render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")
    (out / "d_page_0002.png").unlink()  # 故意删除一页

    second = render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")

    assert first == second == 2  # 返回缓存页数
    assert not (out / "d_page_0002.png").exists()  # 幂等短路，未重渲染


def test_sentinel_busts_on_content_change(tmp_path: Path) -> None:
    """PDF 内容变（哈希变）则缓存失效，重新渲染新页数。"""
    pdf = tmp_path / "doc.pdf"
    out = tmp_path / "out"
    cfg = PdfRenderConfig()
    make_pdf(pdf, ["A", "B"])
    render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")

    make_pdf(pdf, ["A", "B", "C"])  # 覆盖同名 PDF，内容变 3 页
    count = render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")

    assert count == 3
    assert (out / "d_page_0003.png").exists()


def test_max_pages_truncates(tmp_path: Path) -> None:
    """页数超 max_pages 截断前 N 页。"""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["A", "B", "C", "D", "E"])
    out = tmp_path / "out"

    count = render_pdf_to_dir(
        pdf, out, cfg=PdfRenderConfig(max_pages=2), name_prefix="d_",
    )

    assert count == 2
    names = sorted(p.name for p in out.glob("d_page_*.png"))
    assert names == ["d_page_0001.png", "d_page_0002.png"]


def test_zero_pad_auto_widen(tmp_path: Path) -> None:
    """零填充位数不足时按总页数自动加宽，保证字典序 = 页序。"""
    labels = [str(i) for i in range(12)]
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, labels)
    out = tmp_path / "out"

    count = render_pdf_to_dir(
        pdf, out, cfg=PdfRenderConfig(zero_pad=1), name_prefix="",
    )

    assert count == 12
    names = sorted(p.name for p in out.glob("page_*.png"))
    # width = max(1, len("12")) = 2 → page_01..page_12
    assert names == [f"page_{i:02d}.png" for i in range(1, 13)]


def test_long_side_downscale(tmp_path: Path) -> None:
    """超大幅面页按比例降采样到 max_long_side。"""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["A"], size=(300, 400))  # 200dpi 渲染长边约 1112
    out = tmp_path / "out"

    render_pdf_to_dir(
        pdf, out, cfg=PdfRenderConfig(max_long_side=400), name_prefix="",
    )

    with Image.open(out / "page_0001.png") as im:
        assert max(im.size) == 400


def test_corrupt_pdf_propagates(tmp_path: Path) -> None:
    """损坏 / 加密 PDF 的 PdfDocument 构造异常上浮，交调用方处理。"""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf \x00\x01")
    out = tmp_path / "out"

    with pytest.raises(pdfium.PdfiumError):
        render_pdf_to_dir(bad, out, cfg=PdfRenderConfig(), name_prefix="")


def test_incomplete_render_not_cached_as_complete(tmp_path: Path) -> None:
    """缺页的 sentinel（rendered < expected_pages）不算命中：重跑重试缺页。

    防止"坏页被跳过 → sentinel 把残缺渲染永久标记为完成 → resume 不再补渲"。
    """
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["A", "B"])
    out = tmp_path / "out"
    cfg = PdfRenderConfig()

    render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")
    # 篡改 sentinel 模拟"上次只成功渲染 1/2 页"，并删掉第 2 页 PNG
    sentinel = out / ".render_done.json"
    data = json.loads(sentinel.read_text(encoding="utf-8"))
    data["rendered"] = 1
    sentinel.write_text(json.dumps(data), encoding="utf-8")
    (out / "d_page_0002.png").unlink()

    count = render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")

    assert count == 2  # 不短路，重渲染补回缺页
    assert (out / "d_page_0002.png").exists()


def test_legacy_sentinel_without_expected_is_honored(tmp_path: Path) -> None:
    """旧 sentinel 无 expected_pages 字段 → 向后兼容视为完成，不强制重渲。"""
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, ["A", "B"])
    out = tmp_path / "out"
    cfg = PdfRenderConfig()

    render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")
    sentinel = out / ".render_done.json"
    data = json.loads(sentinel.read_text(encoding="utf-8"))
    del data["expected_pages"]  # 模拟旧格式 sentinel
    sentinel.write_text(json.dumps(data), encoding="utf-8")
    (out / "d_page_0002.png").unlink()

    count = render_pdf_to_dir(pdf, out, cfg=cfg, name_prefix="d_")

    assert count == 2  # 命中缓存返回页数
    assert not (out / "d_page_0002.png").exists()  # 未重渲（向后兼容短路）
