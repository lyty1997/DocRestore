# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""下载 zip 在代码模式下需带上 ``files/`` + ``files-index.json`` 测试。

回归用户实测：跑完代码模式任务，下载 zip 里只看到 document.md 没有源文件。
``_build_result_zip_bytes`` 必须把 ``files/`` 整树和 ``files-index.json``
也打进去；非代码模式（无这些产物）应静默跳过、不影响文档模式行为。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from docrestore.api.routes import _build_result_zip_bytes


def _make_doc_only(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "document.md").write_text("# hello", encoding="utf-8")
    images = out / "images"
    images.mkdir()
    (images / "p1.jpg").write_bytes(b"\xff\xd8")  # JPEG magic


def _make_code_mode(out: Path) -> None:
    """构造一个带代码模式产物的 output_dir。"""
    _make_doc_only(out)
    files = out / "files" / "media" / "gpu"
    files.mkdir(parents=True)
    (files / "foo.cc").write_text("int main(){return 0;}\n", encoding="utf-8")
    (files / "foo.h").write_text("#pragma once\n", encoding="utf-8")
    (out / "files-index.json").write_text(
        json.dumps([
            {"path": "media/gpu/foo.cc", "filename": "foo.cc"},
            {"path": "media/gpu/foo.h", "filename": "foo.h"},
        ]),
        encoding="utf-8",
    )
    (out / ".quality_report.json").write_text(
        json.dumps({"summary": {"total": 0}, "issues": []}),
        encoding="utf-8",
    )


class TestZipCodeMode:
    def test_doc_only_zip_unchanged(self, tmp_path: Path) -> None:
        """非代码模式（无 files/）的 zip 行为不变：只有 document.md + images/。"""
        out = tmp_path / "doc"
        _make_doc_only(out)

        data = _build_result_zip_bytes(out, [])
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())

        assert "document.md" in names
        assert "images/p1.jpg" in names
        # 没有 files/ 也没有 files-index.json
        assert not any(n.startswith("files/") for n in names)
        assert "files-index.json" not in names

    def test_code_mode_zip_includes_files_and_index(
        self, tmp_path: Path,
    ) -> None:
        """代码模式：files/ 整树 + files-index.json + .quality_report.json 入包。"""
        out = tmp_path / "code"
        _make_code_mode(out)

        data = _build_result_zip_bytes(out, [])
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())

        assert "document.md" in names
        assert "images/p1.jpg" in names
        assert "files/media/gpu/foo.cc" in names
        assert "files/media/gpu/foo.h" in names
        assert "files-index.json" in names
        assert ".quality_report.json" in names

    def test_code_mode_zip_preserves_file_content(
        self, tmp_path: Path,
    ) -> None:
        """zip 里的源文件字节应等同磁盘原文件。"""
        out = tmp_path / "code2"
        _make_code_mode(out)

        data = _build_result_zip_bytes(out, [])
        zf = zipfile.ZipFile(io.BytesIO(data))
        assert zf.read("files/media/gpu/foo.cc").decode("utf-8") == (
            "int main(){return 0;}\n"
        )
        idx = json.loads(zf.read("files-index.json").decode("utf-8"))
        assert isinstance(idx, list)
        assert any(e["filename"] == "foo.cc" for e in idx)

    def test_multi_doc_zip_namespaced_per_subdir(
        self, tmp_path: Path,
    ) -> None:
        """process_tree 多子目录场景：每个子目录的 files/ 都按 prefix 命名空间隔离。"""
        out = tmp_path / "multi"
        out.mkdir()
        sub_a = out / "a"
        sub_b = out / "b"
        _make_code_mode(sub_a)
        _make_doc_only(sub_b)  # b 是非代码模式

        data = _build_result_zip_bytes(out, ["a", "b"])
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())

        assert "a/document.md" in names
        assert "a/files/media/gpu/foo.cc" in names
        assert "a/files-index.json" in names
        assert "b/document.md" in names
        assert "b/images/p1.jpg" in names
        # b 没有 files/，不应混入
        assert not any(n.startswith("b/files/") for n in names)
