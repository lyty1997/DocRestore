# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式多语言诊断单元测试。"""

from __future__ import annotations

from pathlib import Path

from docrestore.processing.code_diagnostics import (
    CodeDiagnosticRunner,
    CodeDiagnosticTarget,
    CommandRunResult,
)


def _target(path: Path, language: str | None = None) -> CodeDiagnosticTarget:
    return CodeDiagnosticTarget(
        path=path.name,
        file_path=path,
        language=language,
    )


class TestParserDiagnostics:
    def test_python_syntax_clean(self, tmp_path: Path) -> None:
        source = tmp_path / "ok.py"
        source.write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
        result = CodeDiagnosticRunner().run_target(_target(source))
        assert result.status == "syntax_clean"
        assert result.category == "syntax"

    def test_python_syntax_dirty_line(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.py"
        source.write_text("def run(:\n    return 1\n", encoding="utf-8")
        result = CodeDiagnosticRunner().run_target(_target(source))
        assert result.status == "syntax_dirty"
        assert result.failing_lines == [1]
        assert result.syntax_errors == 1
        assert result.items[0].category == "syntax"

    def test_json_syntax_dirty_line(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.json"
        source.write_text('{"a": 1,\n', encoding="utf-8")
        result = CodeDiagnosticRunner().run_target(_target(source))
        assert result.status == "syntax_dirty"
        assert result.failing_lines == [2]

    def test_toml_syntax_clean(self, tmp_path: Path) -> None:
        source = tmp_path / "pyproject.toml"
        source.write_text("[tool.demo]\nvalue = 1\n", encoding="utf-8")
        result = CodeDiagnosticRunner().run_target(_target(source))
        assert result.status == "syntax_clean"


class TestToolDiagnostics:
    def test_tool_unavailable(self, tmp_path: Path) -> None:
        source = tmp_path / "app.js"
        source.write_text("console.log(1)\n", encoding="utf-8")
        runner = CodeDiagnosticRunner(tool_resolver=lambda _tool: None)
        result = runner.run_target(_target(source))
        assert result.status == "tool_unavailable"
        assert result.category == "tool_unavailable"
        assert result.tool == "node"

    def test_tool_success_when_available(self, tmp_path: Path) -> None:
        source = tmp_path / "app.js"
        source.write_text("console.log(1)\n", encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            assert cmd[:2] == ["node", "--check"]
            assert cwd == tmp_path
            assert timeout_s == 10
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source))
        assert result.status == "syntax_clean"
        assert result.tool == "node"

    def test_tool_syntax_failure_extracts_lines(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.cc"
        source.write_text("int main(\n", encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            return CommandRunResult(
                returncode=1,
                stderr="bad.cc:3:5: error: expected ')' before end of input",
            )

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))
        assert result.status == "syntax_dirty"
        assert result.category == "syntax"
        assert result.failing_lines == [3]
        assert result.syntax_errors == 1
        assert result.items[0].line == 3
        assert result.items[0].category == "syntax"

    def test_tool_semantic_failure_classified(self, tmp_path: Path) -> None:
        source = tmp_path / "bad.cc"
        source.write_text("UnknownType x;\n", encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            return CommandRunResult(
                returncode=1,
                stderr="bad.cc:1:1: error: UnknownType was not declared",
            )

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))
        assert result.status == "semantic_dirty"
        assert result.category == "semantic"
        assert result.semantic_errors == 1

    def test_cpp_missing_include_classified_as_dependency(
        self, tmp_path: Path,
    ) -> None:
        source = tmp_path / "bad.cc"
        source.write_text('#include "missing.h"\n', encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            return CommandRunResult(
                returncode=1,
                stderr=(
                    "bad.cc:1:10: fatal error: missing.h: "
                    "No such file or directory"
                ),
            )

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))
        assert result.status == "dependency_dirty"
        assert result.category == "dependency"
        assert result.failing_lines == [1]
        assert result.dependency_errors == 1
        assert result.syntax_errors == 0
        assert result.items[0].code == "missing_include"

    def test_cpp_include_root_is_passed_to_command(
        self, tmp_path: Path,
    ) -> None:
        source = tmp_path / "src" / "bad.cc"
        source.parent.mkdir()
        source.write_text('#include "src/good.h"\n', encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            assert "-I" in cmd
            assert str(tmp_path) in cmd
            assert cwd == source.parent
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        target = CodeDiagnosticTarget(
            path="src/bad.cc",
            file_path=source,
            language="cpp",
            include_root=tmp_path,
        )
        result = runner.run_target(target)
        assert result.status == "syntax_clean"
