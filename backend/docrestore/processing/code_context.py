# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""离线代码库上下文检索。"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodePathCandidate:
    """参考源码中的路径候选。"""

    path: str
    filename: str
    language: str | None
    score: float
    source: Literal["reference"] = "reference"


@dataclass(frozen=True)
class CodeSnippetCandidate:
    """参考源码中的片段候选。"""

    path: str
    language: str | None
    start_line: int
    end_line: int
    text: str
    score: float
    source: Literal["reference"] = "reference"


class CodeContextProvider(Protocol):
    """代码库上下文提供者。"""

    def list_files(self) -> list[CodePathCandidate]:
        """列出可检索源文件。"""
        ...

    def search_paths(
        self,
        query: str,
        *,
        language: str | None = None,
        limit: int = 5,
    ) -> list[CodePathCandidate]:
        """按 OCR 路径/文件名 fuzzy match 参考源码路径。"""
        ...

    def search_snippets(
        self,
        query: str,
        *,
        language: str | None = None,
        limit: int = 3,
    ) -> list[CodeSnippetCandidate]:
        """按代码文本/符号检索参考源码片段。"""
        ...


_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".venv", "venv",
}
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".c": "c",
    ".h": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".gn": "gn",
    ".gni": "gn",
}
_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")


class LocalCodeContextProvider:
    """本地参考源码目录 provider，默认只读、离线。"""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 20000,
        max_file_bytes: int = 300_000,
    ) -> None:
        self._root = root.resolve()
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._files: list[CodePathCandidate] | None = None

    def list_files(self) -> list[CodePathCandidate]:
        """列出源码文件，结果缓存到内存。"""
        if self._files is not None:
            return list(self._files)
        if not self._root.exists() or not self._root.is_dir():
            self._files = []
            return []
        out: list[CodePathCandidate] = []
        for path in self._iter_source_files():
            rel = path.relative_to(self._root).as_posix()
            out.append(CodePathCandidate(
                path=rel,
                filename=path.name,
                language=detect_language(path),
                score=1.0,
            ))
            if len(out) >= self._max_files:
                break
        self._files = out
        return list(out)

    def search_paths(
        self,
        query: str,
        *,
        language: str | None = None,
        limit: int = 5,
    ) -> list[CodePathCandidate]:
        """按路径 fuzzy match。"""
        needle = _norm_query(query)
        if not needle:
            return []
        scored: list[CodePathCandidate] = []
        for item in self.list_files():
            if language and item.language and item.language != language:
                continue
            haystack = _norm_query(item.path)
            filename_score = difflib.SequenceMatcher(
                None, needle, _norm_query(item.filename),
            ).ratio()
            path_score = difflib.SequenceMatcher(None, needle, haystack).ratio()
            score = max(filename_score, path_score)
            if item.filename.lower() in query.lower():
                score = max(score, 0.85)
            if score <= 0.2:
                continue
            scored.append(CodePathCandidate(
                path=item.path,
                filename=item.filename,
                language=item.language,
                score=round(score, 3),
            ))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def search_snippets(
        self,
        query: str,
        *,
        language: str | None = None,
        limit: int = 3,
    ) -> list[CodeSnippetCandidate]:
        """按 token 检索源码片段。"""
        tokens = _query_tokens(query)
        if not tokens:
            return []
        results: list[CodeSnippetCandidate] = []
        for item in self.list_files():
            if language and item.language and item.language != language:
                continue
            abs_path = self._safe_abs_path(item.path)
            if abs_path is None or not _is_small_file(abs_path, self._max_file_bytes):
                continue
            try:
                text = abs_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                # 文件在 list_files 缓存后被删除/改权限/目录化时会抛 OSError，
                # 跳过该文件即可，不能让它冒泡拖垮整条 repair 上下文构建链（B7 C7）。
                continue
            lines = text.splitlines()
            for idx, line in enumerate(lines):
                score = _line_token_score(line, tokens)
                if score <= 0:
                    continue
                start = max(1, idx + 1 - 2)
                end = min(len(lines), idx + 1 + 2)
                snippet = "\n".join(lines[start - 1:end])
                results.append(CodeSnippetCandidate(
                    path=item.path,
                    language=item.language,
                    start_line=start,
                    end_line=end,
                    text=snippet,
                    score=round(score, 3),
                ))
                break
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    def _iter_source_files(self) -> list[Path]:
        files: list[Path] = []
        for path in sorted(self._root.rglob("*")):
            if len(files) >= self._max_files:
                break
            if any(part in _SKIP_DIRS for part in path.relative_to(self._root).parts):
                continue
            if not path.is_file():
                continue
            if detect_language(path) is None:
                continue
            if not _is_small_file(path, self._max_file_bytes):
                continue
            files.append(path)
        return files

    def _safe_abs_path(self, rel_path: str) -> Path | None:
        candidate = (self._root / rel_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            return None
        return candidate


def create_code_context_provider(
    root: str | None,
) -> CodeContextProvider | None:
    """根据配置创建 provider；空路径或非法路径返回 None。"""
    if root is None or not root.strip():
        return None
    path = Path(root).expanduser()
    if not path.exists() or not path.is_dir():
        # 配了非空 root 但路径不存在/非目录，几乎一定是 typo：与"未配置"区分，
        # 记 warning，否则 repair 静默失去参考上下文、修复质量下降而用户不知。
        logger.warning(
            "代码参考源码根无效（路径不存在或非目录），不启用参考上下文: %s",
            root,
        )
        return None
    return LocalCodeContextProvider(path)


def detect_language(path: Path) -> str | None:
    """轻量语言识别：扩展名 + shebang。"""
    ext = path.suffix.lower()
    if ext in _LANG_BY_EXT:
        return _LANG_BY_EXT[ext]
    if path.name in {"BUILD", "BUCK"}:
        return "starlark"
    try:
        first = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
    except OSError:
        return None
    if not first:
        return None
    shebang = first[0].lower()
    if shebang.startswith("#!"):
        if "python" in shebang:
            return "python"
        if "bash" in shebang or " sh" in shebang:
            return "shell"
        if "node" in shebang:
            return "javascript"
    return None


def _norm_query(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _query_tokens(query: str) -> set[str]:
    tokens = {
        token for token in _TOKEN_RE.findall(query)
        if len(token) >= 3 and not token.isupper()
    }
    if not tokens:
        tokens = {
            token for token in _TOKEN_RE.findall(query)
            if len(token) >= 4
        }
    return set(list(tokens)[:30])


def _line_token_score(line: str, tokens: set[str]) -> float:
    if not line.strip():
        return 0.0
    lower = line.lower()
    hits = sum(1 for token in tokens if token.lower() in lower)
    if hits == 0:
        return 0.0
    return hits / max(1, len(tokens))


def _is_small_file(path: Path, max_file_bytes: int) -> bool:
    try:
        return path.stat().st_size <= max_file_bytes
    except OSError:
        return False
