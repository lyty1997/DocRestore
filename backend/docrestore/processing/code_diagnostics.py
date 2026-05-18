# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式多语言轻量诊断。

诊断目标是为 OCR/LLM 修复提供低成本定位信号，不追求项目级完整编译。
能用标准库解析的语言优先用标准库；需要外部工具时，工具缺失会降级为
``tool_unavailable``，不让代码模式任务失败。
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docrestore.processing.code_file_grouping import SourceFile


@dataclass(frozen=True)
class CodeDiagnosticTarget:
    """待诊断的单个代码文件。"""

    path: str
    file_path: Path
    language: str | None = None
    include_root: Path | None = None


@dataclass(frozen=True)
class CommandRunResult:
    """外部诊断命令执行结果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CodeDiagnosticItem:
    """面向人工审查的单条诊断标注。"""

    line: int
    column: int = 0
    severity: str = "error"
    category: str = "syntax"
    code: str = ""
    message: str = ""
    source: str = ""


@dataclass(frozen=True)
class CodeDiagnostic:
    """单文件诊断结果。"""

    path: str
    language: str
    status: str
    category: str
    summary: str = ""
    failing_lines: list[int] = field(default_factory=list)
    syntax_errors: int = 0
    semantic_errors: int = 0
    dependency_errors: int = 0
    items: list[CodeDiagnosticItem] = field(default_factory=list)
    tool: str = ""
    duration_ms: int = 0

    def to_index_dict(self) -> dict[str, object]:
        """转换为 files-index.json 可序列化结构。"""
        return asdict(self)


ToolResolver = Callable[[str], str | None]
CommandRunner = Callable[[list[str], Path, int], CommandRunResult]


_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".c": "c",
    ".h": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".go": "go",
    ".rs": "rust",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
}

_LINE_COL_RE = re.compile(r"(?::|\()(\d+)(?::|,)(\d+)")
_CPP_LINE_RE = re.compile(
    r":(?P<line>\d+):(?P<col>\d+):\s*(?:(?:fatal\s+)?error|warning):\s*(?P<msg>.*)",
    re.IGNORECASE,
)
_SYNTAX_ERROR_PATTERNS = (
    "syntax error",
    "expected",
    "stray ",
    "unterminated",
    "missing terminating",
    "invalid preprocessing directive",
    "unexpected token",
    "invalid character",
)
_SEMANTIC_ERROR_PATTERNS = (
    "not declared",
    "has not been declared",
    "unknown type",
    "no member named",
    "no type named",
    "does not name a type",
    "cannot find",
    "unresolved import",
)
_DEPENDENCY_ERROR_PATTERNS = (
    "no such file or directory",
    "file not found",
    "cannot open include file",
    "没有那个文件或目录",
)


@dataclass(frozen=True)
class _ClassifiedToolOutput:
    """外部工具输出的轻量分类结果。"""

    syntax_errors: int = 0
    semantic_errors: int = 0
    dependency_errors: int = 0
    failing_lines: list[int] = field(default_factory=list)
    items: list[CodeDiagnosticItem] = field(default_factory=list)


class CodeDiagnosticRunner:
    """多语言轻量语法/解析诊断运行器。"""

    def __init__(
        self,
        *,
        tool_resolver: ToolResolver | None = None,
        command_runner: CommandRunner | None = None,
        timeout_s: int = 10,
    ) -> None:
        self._tool_resolver = tool_resolver or shutil.which
        self._command_runner = command_runner or _run_command
        self._timeout_s = timeout_s

    def run_targets(
        self, targets: list[CodeDiagnosticTarget],
    ) -> list[CodeDiagnostic]:
        """批量诊断文件，单文件失败不影响其他文件。"""
        return [self.run_target(target) for target in targets]

    def run_target(self, target: CodeDiagnosticTarget) -> CodeDiagnostic:
        """诊断单个文件。"""
        language = _resolve_language(target)
        if language in {"python", "json", "toml", "xml", "yaml"}:
            return self._run_parser(target, language)
        if language in {"javascript", "typescript", "c", "cpp", "go", "rust"}:
            return self._run_tool(target, language)
        return CodeDiagnostic(
            path=target.path,
            language=language,
            status="unsupported",
            category="tool_unavailable",
            summary=f"unsupported language: {language}",
        )

    def _run_parser(
        self, target: CodeDiagnosticTarget, language: str,
    ) -> CodeDiagnostic:
        start = time.monotonic()
        try:
            text = target.file_path.read_text(encoding="utf-8")
            if language == "python":
                ast.parse(text, filename=target.path)
            elif language == "json":
                json.loads(text)
            elif language == "toml":
                tomllib.loads(text)
            elif language == "xml":
                return self._run_xml_parser(target, text, start)
            elif language == "yaml":
                return self._run_yaml_parser(target, text, start)
        except SyntaxError as exc:
            line = exc.lineno or 0
            return _syntax_dirty(
                target, language, _format_exception(exc), [line], start,
            )
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            return _syntax_dirty(
                target, language, _format_exception(exc),
                [_line_from_parse_error(exc)], start,
            )
        except OSError as exc:
            return _failed(target, language, _format_exception(exc), start)
        return _syntax_clean(target, language, start)

    def _run_xml_parser(
        self, target: CodeDiagnosticTarget, text: str, start: float,
    ) -> CodeDiagnostic:
        try:
            from defusedxml import ElementTree as DET  # type: ignore[import-untyped]
        except ImportError:
            return CodeDiagnostic(
                path=target.path,
                language="xml",
                status="tool_unavailable",
                category="tool_unavailable",
                summary="defusedxml unavailable",
                tool="defusedxml",
                duration_ms=_elapsed_ms(start),
            )
        try:
            DET.fromstring(text)
        except DET.ParseError as exc:
            return _syntax_dirty(
                target, "xml", _format_exception(exc),
                [_line_from_parse_error(exc)], start,
            )
        return _syntax_clean(target, "xml", start)

    def _run_yaml_parser(
        self, target: CodeDiagnosticTarget, text: str, start: float,
    ) -> CodeDiagnostic:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return CodeDiagnostic(
                path=target.path,
                language="yaml",
                status="tool_unavailable",
                category="tool_unavailable",
                summary="PyYAML unavailable",
                tool="pyyaml",
                duration_ms=_elapsed_ms(start),
            )
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            line = _yaml_error_line(exc)
            return _syntax_dirty(
                target, "yaml", _format_exception(exc), [line], start,
            )
        return _syntax_clean(target, "yaml", start)

    def _run_tool(
        self, target: CodeDiagnosticTarget, language: str,
    ) -> CodeDiagnostic:
        spec = _tool_spec(language, target.file_path, target.include_root)
        if spec is None:
            return CodeDiagnostic(
                path=target.path,
                language=language,
                status="unsupported",
                category="tool_unavailable",
                summary=f"unsupported language: {language}",
            )
        tool, cmd = spec
        if self._tool_resolver(tool) is None:
            return CodeDiagnostic(
                path=target.path,
                language=language,
                status="tool_unavailable",
                category="tool_unavailable",
                summary=f"tool {tool} unavailable",
                tool=tool,
            )

        start = time.monotonic()
        try:
            result = self._command_runner(
                cmd, target.file_path.parent, self._timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _failed(target, language, _format_exception(exc), start, tool)

        output = (result.stderr or result.stdout).strip()
        if result.returncode == 0:
            return _syntax_clean(target, language, start, tool)

        classified = _classify_tool_output(output, tool)
        if classified.syntax_errors:
            status = "syntax_dirty"
            category = "syntax"
        elif classified.dependency_errors:
            status = "dependency_dirty"
            category = "dependency"
        else:
            status = "semantic_dirty"
            category = "semantic"
        return CodeDiagnostic(
            path=target.path,
            language=language,
            status=status,
            category=category,
            summary=output[:1000],
            failing_lines=classified.failing_lines,
            syntax_errors=classified.syntax_errors,
            semantic_errors=classified.semantic_errors,
            dependency_errors=classified.dependency_errors,
            items=classified.items,
            tool=tool,
            duration_ms=_elapsed_ms(start),
        )


def diagnose_source_files(
    sources: list[SourceFile],
    *,
    runner: CodeDiagnosticRunner | None = None,
) -> list[CodeDiagnostic]:
    """把内存中的 SourceFile 写入临时目录后运行诊断。"""
    active_runner = runner or CodeDiagnosticRunner()
    with tempfile.TemporaryDirectory(prefix="docrestore-code-diag-") as tmp:
        root = Path(tmp)
        targets: list[CodeDiagnosticTarget] = []
        for index, source in enumerate(sources):
            rel_path = _safe_diagnostic_rel_path(source.path, index)
            file_path = root / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(source.merged_text, encoding="utf-8")
            targets.append(CodeDiagnosticTarget(
                path=rel_path,
                file_path=file_path,
                language=source.language,
                include_root=root,
            ))
        return active_runner.run_targets(targets)


def diagnose_text(
    *,
    path: str,
    language: str | None,
    text: str,
    runner: CodeDiagnosticRunner | None = None,
) -> CodeDiagnostic:
    """诊断一段内存文本，供 scoped repair 应用 patch 后验证。"""
    active_runner = runner or CodeDiagnosticRunner()
    with tempfile.TemporaryDirectory(prefix="docrestore-code-diag-") as tmp:
        root = Path(tmp)
        rel_path = _safe_diagnostic_rel_path(path, 0)
        file_path = root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")
        return active_runner.run_target(CodeDiagnosticTarget(
            path=rel_path,
            file_path=file_path,
            language=language,
            include_root=root,
        ))


def _run_command(
    cmd: list[str], cwd: Path, timeout_s: int,
) -> CommandRunResult:
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    return CommandRunResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _resolve_language(target: CodeDiagnosticTarget) -> str:
    if target.language:
        language = target.language.strip().lower()
        if language:
            return language
    return _LANG_BY_EXT.get(target.file_path.suffix.lower(), "unknown")


def _safe_diagnostic_rel_path(raw_path: str, index: int) -> str:
    """把 SourceFile.path 转为临时诊断目录内的安全相对路径。"""
    fallback = f"unknown_{index}.txt"
    if not raw_path or not raw_path.strip():
        return fallback
    path = Path(raw_path)
    if path.is_absolute():
        return path.name or fallback
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return path.name or fallback
    return "/".join(parts)


def _tool_spec(
    language: str,
    file_path: Path,
    include_root: Path | None = None,
) -> tuple[str, list[str]] | None:
    file_arg = str(file_path)
    if language == "javascript":
        return "node", ["node", "--check", file_arg]
    if language == "typescript":
        return "tsc", ["tsc", "--noEmit", "--pretty", "false", file_arg]
    if language == "c":
        cmd = ["gcc", "-fsyntax-only", "-w"]
        if include_root is not None:
            cmd.extend(["-I", str(include_root)])
        cmd.append(file_arg)
        return "gcc", cmd
    if language == "cpp":
        kind = (
            "c++-header"
            if file_path.suffix.lower() in {".h", ".hh", ".hpp"}
            else "c++"
        )
        cmd = ["g++", "-fsyntax-only", "-std=c++17", "-w", "-fpermissive"]
        if include_root is not None:
            cmd.extend(["-I", str(include_root)])
        cmd.extend(["-x", kind, file_arg])
        return "g++", cmd
    if language == "go":
        return "go", ["go", "tool", "compile", file_arg]
    if language == "rust":
        return "rustc", ["rustc", "--emit=metadata", file_arg]
    return None


def _syntax_clean(
    target: CodeDiagnosticTarget,
    language: str,
    start: float,
    tool: str = "",
) -> CodeDiagnostic:
    return CodeDiagnostic(
        path=target.path,
        language=language,
        status="syntax_clean",
        category="syntax",
        tool=tool,
        duration_ms=_elapsed_ms(start),
    )


def _syntax_dirty(
    target: CodeDiagnosticTarget,
    language: str,
    summary: str,
    failing_lines: list[int],
    start: float,
) -> CodeDiagnostic:
    lines = sorted({line for line in failing_lines if line > 0})
    items = [
        CodeDiagnosticItem(
            line=line,
            category="syntax",
            code="parse_error",
            message=summary[:200],
        )
        for line in lines
    ]
    return CodeDiagnostic(
        path=target.path,
        language=language,
        status="syntax_dirty",
        category="syntax",
        summary=summary[:1000],
        failing_lines=lines,
        syntax_errors=max(1, len(lines)),
        items=items,
        duration_ms=_elapsed_ms(start),
    )


def _failed(
    target: CodeDiagnosticTarget,
    language: str,
    summary: str,
    start: float,
    tool: str = "",
) -> CodeDiagnostic:
    return CodeDiagnostic(
        path=target.path,
        language=language,
        status="failed",
        category="unknown",
        summary=summary[:1000],
        tool=tool,
        duration_ms=_elapsed_ms(start),
    )


def _classify_tool_output(output: str, tool: str) -> _ClassifiedToolOutput:
    syntax = 0
    semantic = 0
    dependency = 0
    lines: set[int] = set()
    items: list[CodeDiagnosticItem] = []
    for raw_line in output.splitlines():
        category, item = _classify_tool_line(raw_line, tool)
        if category == "dependency":
            dependency += 1
        elif category == "syntax":
            syntax += 1
        elif category == "semantic":
            semantic += 1
        if item is not None:
            lines.add(item.line)
            items.append(item)
    if syntax == 0 and semantic == 0 and dependency == 0 and output:
        semantic = 1
    return _ClassifiedToolOutput(
        syntax_errors=syntax,
        semantic_errors=semantic,
        dependency_errors=dependency,
        failing_lines=sorted(lines),
        items=items,
    )


def _classify_tool_line(
    raw_line: str,
    tool: str,
) -> tuple[str, CodeDiagnosticItem | None]:
    line = raw_line.lower()
    line_no, column = _extract_line_col(raw_line)
    message = _extract_tool_message(raw_line)
    if any(pattern in line for pattern in _DEPENDENCY_ERROR_PATTERNS):
        return "dependency", _tool_item(
            line_no, column, "warn", "dependency",
            "missing_include", message, tool,
        )
    if any(pattern in line for pattern in _SYNTAX_ERROR_PATTERNS):
        return "syntax", _tool_item(
            line_no, column, "error", "syntax",
            "syntax_error", message, tool,
        )
    if any(pattern in line for pattern in _SEMANTIC_ERROR_PATTERNS):
        return "semantic", _tool_item(
            line_no, column, "warn", "semantic",
            "semantic_error", message, tool,
        )
    if "error" in line:
        return "syntax", _tool_item(
            line_no, column, "error", "syntax",
            "tool_error", message, tool,
        )
    return "", None


def _tool_item(
    line: int,
    column: int,
    severity: str,
    category: str,
    code: str,
    message: str,
    tool: str,
) -> CodeDiagnosticItem | None:
    if line <= 0:
        return None
    return CodeDiagnosticItem(
        line=line,
        column=column,
        severity=severity,
        category=category,
        code=code,
        message=message,
        source=tool,
    )


def _extract_line_no(text: str) -> int:
    line_no, _column = _extract_line_col(text)
    return line_no


def _extract_line_col(text: str) -> tuple[int, int]:
    match = _CPP_LINE_RE.search(text)
    if match is not None:
        return int(match.group("line")), int(match.group("col"))
    match = _LINE_COL_RE.search(text)
    if match is not None:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def _extract_tool_message(text: str) -> str:
    match = _CPP_LINE_RE.search(text)
    if match is not None:
        return match.group("msg").strip()
    return text.strip()


def _line_from_parse_error(exc: BaseException) -> int:
    if isinstance(exc, json.JSONDecodeError):
        return exc.lineno
    position = getattr(exc, "position", None)
    if (
        isinstance(position, tuple)
        and len(position) >= 1
        and isinstance(position[0], int)
    ):
        return position[0]
    return 0


def _yaml_error_line(exc: BaseException) -> int:
    problem_mark = getattr(exc, "problem_mark", None)
    line = getattr(problem_mark, "line", None)
    if isinstance(line, int):
        return line + 1
    return 0


def _format_exception(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
