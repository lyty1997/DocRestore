# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Epic D · D1 导出层骨架：下载 zip 按 ``?formats=`` 追加导出产物测试。

覆盖：formats 解析（fail-closed 白名单 / 去重 / 大小写）、产物入 zip、
多文档命名空间隔离、缓存复用与缓存目录不泄漏、依赖缺失 / 导出失败 fail-closed。

断言「输出含关键内容」时从**输入派生**关键行（CLAUDE.md 测试规则），不写死数据集关键词。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from docrestore.api import routes
from docrestore.api.errors import APIErrorCode, ApiBusinessError
from docrestore.api.routes import _build_result_zip_bytes, _parse_export_formats
from docrestore.output.exporters import EXPORT_CACHE_DIRNAME
from docrestore.output.exporters.base import ExportFailed, ExportToolUnavailable

#: 输入 markdown 的关键标题行（断言从此派生，不写死数据集关键词）
_HEADING = "# 季度营收汇总"
_MARKDOWN = f"{_HEADING}\n\n正文一段。\n\n![图](images/p1.jpg)\n"


def _make_doc(out: Path) -> None:
    """构造一个最小 document.md + images/ 的 output_dir。"""
    out.mkdir(parents=True, exist_ok=True)
    (out / "document.md").write_text(_MARKDOWN, encoding="utf-8")
    images = out / "images"
    images.mkdir()
    (images / "p1.jpg").write_bytes(b"\xff\xd8")  # JPEG magic


def _names(data: bytes) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(data)).namelist())


class _FakeOkExporter:
    """确定性假导出器（zip 装配 plumbing 测试用，不依赖 pandoc/weasyprint）。

    suffix 跟随请求格式，``export`` 内嵌源 markdown，便于断言从输入派生关键内容。
    """

    tool = "fake"

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix

    def ensure_available(self) -> None:
        return

    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None:  # noqa: ARG002
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body = doc_md.read_text(encoding="utf-8")
        out_path.write_text(f"FAKE-{self.suffix}\n{body}", encoding="utf-8")


@pytest.fixture
def fake_exporters(monkeypatch: pytest.MonkeyPatch) -> None:
    """把注册表换成确定性假导出器，隔离 zip 装配 plumbing 与真实工具。"""
    monkeypatch.setattr(routes, "get_exporter", lambda fmt: _FakeOkExporter(fmt))


class TestParseExportFormats:
    """``_parse_export_formats``：fail-closed 白名单 + 去重 + 大小写归一。"""

    def test_empty_is_noop(self) -> None:
        assert _parse_export_formats(None) == []
        assert _parse_export_formats("") == []

    def test_dedup_and_casefold(self) -> None:
        assert _parse_export_formats("docx, PDF ,docx") == ["docx", "pdf"]

    def test_unknown_format_rejected(self) -> None:
        with pytest.raises(ApiBusinessError) as ei:
            _parse_export_formats("docx,rtf")
        assert ei.value.code is APIErrorCode.EXPORT_FORMAT_UNSUPPORTED
        assert ei.value.status_code == 400


@pytest.mark.usefixtures("fake_exporters")
class TestExportsInZip:
    """选定格式 → ``document.{ext}`` 进 zip；空 formats 行为不变。

    用假导出器隔离 zip 装配 plumbing，不依赖 pandoc/weasyprint。
    """

    def test_no_formats_unchanged(self, tmp_path: Path) -> None:
        out = tmp_path / "doc"
        _make_doc(out)
        names = _names(_build_result_zip_bytes(out, [], export_formats=None))
        assert names == {"document.md", "images/p1.jpg"}

    def test_docx_added_with_derived_content(self, tmp_path: Path) -> None:
        out = tmp_path / "doc"
        _make_doc(out)
        data = _build_result_zip_bytes(out, [], export_formats=["docx"])
        names = _names(data)
        assert "document.docx" in names
        # D1 stub 内嵌源 markdown：断言含从输入派生的标题行
        body = zipfile.ZipFile(io.BytesIO(data)).read("document.docx").decode()
        assert _HEADING in body

    def test_docx_and_pdf_added(self, tmp_path: Path) -> None:
        out = tmp_path / "doc"
        _make_doc(out)
        names = _names(
            _build_result_zip_bytes(out, [], export_formats=["docx", "pdf"]),
        )
        assert {"document.docx", "document.pdf"} <= names

    def test_multi_doc_namespaced(self, tmp_path: Path) -> None:
        out = tmp_path / "multi"
        out.mkdir()
        _make_doc(out / "a")
        _make_doc(out / "b")
        names = _names(
            _build_result_zip_bytes(out, ["a", "b"], export_formats=["docx"]),
        )
        assert {"a/document.docx", "b/document.docx"} <= names

    def test_cache_reused_and_not_leaked(self, tmp_path: Path) -> None:
        out = tmp_path / "doc"
        _make_doc(out)
        # 首次导出 → 落缓存
        _build_result_zip_bytes(out, [], export_formats=["docx"])
        cache_dir = out / EXPORT_CACHE_DIRNAME
        cached = list(cache_dir.glob("*.docx"))
        assert len(cached) == 1
        # 再次导出命中缓存（不新增文件）
        data = _build_result_zip_bytes(out, [], export_formats=["docx"])
        assert len(list(cache_dir.glob("*.docx"))) == 1
        # 缓存目录不裸打进 zip，只暴露干净的 document.docx
        assert not any(
            n.startswith(EXPORT_CACHE_DIRNAME) for n in _names(data)
        )


class _StubExporter:
    """可注入异常的假导出器（fail-closed 路径测试用）。"""

    suffix = "docx"
    tool = "pandoc"

    def __init__(
        self, *, on_ensure: Exception | None, on_export: Exception | None,
    ) -> None:
        self._on_ensure = on_ensure
        self._on_export = on_export

    def ensure_available(self) -> None:
        if self._on_ensure is not None:
            raise self._on_ensure

    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None:  # noqa: ARG002
        if self._on_export is not None:
            raise self._on_export
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("ok", encoding="utf-8")


class TestExportFailClosed:
    """缺依赖 → 503；导出失败 → 500（均 fail-closed 带 i18n params）。"""

    def test_tool_unavailable_maps_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out = tmp_path / "doc"
        _make_doc(out)
        stub = _StubExporter(
            on_ensure=ExportToolUnavailable("pandoc"), on_export=None,
        )
        monkeypatch.setattr(routes, "get_exporter", lambda _fmt: stub)
        with pytest.raises(ApiBusinessError) as ei:
            _build_result_zip_bytes(out, [], export_formats=["docx"])
        assert ei.value.code is APIErrorCode.EXPORT_TOOL_UNAVAILABLE
        assert ei.value.status_code == 503
        assert ei.value.params.get("tool") == "pandoc"

    def test_export_failed_maps_500(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out = tmp_path / "doc"
        _make_doc(out)
        stub = _StubExporter(
            on_ensure=None,
            on_export=ExportFailed("pandoc", "docx", "boom"),
        )
        monkeypatch.setattr(routes, "get_exporter", lambda _fmt: stub)
        with pytest.raises(ApiBusinessError) as ei:
            _build_result_zip_bytes(out, [], export_formats=["docx"])
        assert ei.value.code is APIErrorCode.EXPORT_FAILED
        assert ei.value.status_code == 500
