# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""CodeContextProvider 单元测试。"""

from __future__ import annotations

from pathlib import Path

from docrestore.processing.code_context import (
    LocalCodeContextProvider,
    create_code_context_provider,
    detect_language,
)


def test_default_provider_disabled() -> None:
    assert create_code_context_provider(None) is None
    assert create_code_context_provider("") is None
    assert create_code_context_provider("/path/not/exist") is None


def test_list_files_detects_multiple_languages(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "src" / "main.ts").write_text("export const x = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    provider = LocalCodeContextProvider(tmp_path)
    files = provider.list_files()
    paths = {item.path for item in files}
    assert paths == {"src/app.py", "src/main.ts"}
    assert {item.language for item in files} == {"python", "typescript"}


def test_search_paths_fuzzy_matches_filename(tmp_path: Path) -> None:
    (tmp_path / "media" / "gpu").mkdir(parents=True)
    (tmp_path / "media" / "gpu" / "widget_video.cc").write_text(
        "int x;\n", encoding="utf-8",
    )
    provider = LocalCodeContextProvider(tmp_path)
    matches = provider.search_paths("widget_video.cc", language="cpp")
    assert matches[0].path == "media/gpu/widget_video.cc"
    assert matches[0].source == "reference"
    assert matches[0].score > 0.8


def test_search_snippets_returns_reference_excerpt(tmp_path: Path) -> None:
    source = tmp_path / "src" / "codec.py"
    source.parent.mkdir()
    source.write_text(
        "class Codec:\n"
        "    def decode_frame(self):\n"
        "        return 42\n",
        encoding="utf-8",
    )
    provider = LocalCodeContextProvider(tmp_path)
    snippets = provider.search_snippets("decode_frame()", language="python")
    assert snippets[0].path == "src/codec.py"
    assert "decode_frame" in snippets[0].text
    assert snippets[0].start_line == 1


def test_search_snippets_skips_unreadable_file_without_crashing(
    tmp_path: Path,
) -> None:
    """list_files 缓存后源文件被目录替换时，search_snippets 不得崩溃（B7 C7）。

    _is_small_file 的 stat 守卫挡不住 read_text 的 IsADirectoryError/PermissionError，
    旧实现会让该 OSError 冒泡，拖垮整条 repair 上下文构建链。
    """
    source = tmp_path / "src" / "codec.py"
    source.parent.mkdir()
    source.write_text(
        "def decode_frame():\n    return 1\n", encoding="utf-8",
    )
    provider = LocalCodeContextProvider(tmp_path)
    provider.list_files()  # 先缓存文件列表
    # 把文件替换成目录：stat 仍成功，但 read_text 抛 IsADirectoryError(OSError)。
    source.unlink()
    source.mkdir()
    # 不抛异常即视为修复（命中失败、返回空是可接受结果）。
    assert provider.search_snippets("decode_frame()", language="python") == []


def test_detect_language_from_shebang(tmp_path: Path) -> None:
    script = tmp_path / "tool"
    script.write_text("#!/usr/bin/env python3\nprint('x')\n", encoding="utf-8")
    assert detect_language(script) == "python"


def test_safe_boundary_blocks_path_escape(tmp_path: Path) -> None:
    provider = LocalCodeContextProvider(tmp_path)
    assert provider._safe_abs_path("../x") is None  # noqa: SLF001
