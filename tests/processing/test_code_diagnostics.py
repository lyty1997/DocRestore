# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式多语言诊断单元测试。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from docrestore.processing.code_diagnostics import (
    CodeDiagnosticRunner,
    CodeDiagnosticTarget,
    CommandRunResult,
    _run_command,
    diagnose_text,
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

    def test_python_recovers_and_reports_later_syntax_lines(
        self, tmp_path: Path,
    ) -> None:
        source = tmp_path / "bad.py"
        source.write_text(
            "\n".join([
                "def first(:",
                "    return 1",
                "",
                "def second(:",
                "    return 2",
            ]),
            encoding="utf-8",
        )

        result = CodeDiagnosticRunner().run_target(_target(source))

        assert result.status == "syntax_dirty"
        assert result.failing_lines == [1, 4]
        assert result.syntax_errors == 2
        assert [item.line for item in result.items] == [1, 4]
        assert all(item.category == "syntax" for item in result.items)

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

    def test_node_syntax_failure_seeds_failing_line(
        self, tmp_path: Path,
    ) -> None:
        """node 定位行无关键词，仍应抽出失败行触发修复（B7 C8）。"""
        source = tmp_path / "app.js"
        source.write_text("const x = ;\n", encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            return CommandRunResult(
                returncode=1,
                stderr=(
                    "app.js:1\n"
                    "const x = ;\n"
                    "          ^\n\n"
                    "SyntaxError: Unexpected token ';'\n"
                ),
            )

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/node",
            command_runner=run,
        )
        result = runner.run_target(_target(source))
        assert result.status == "syntax_dirty"
        assert result.failing_lines == [1]
        assert result.syntax_errors >= 1

    def test_syntax_only_tool_unrecognized_failure_is_syntax(
        self, tmp_path: Path,
    ) -> None:
        """纯语法工具非零退出且不匹配模式时应归 syntax 而非 semantic（B7 C9）。"""
        source = tmp_path / "bad.cc"
        source.write_text("int main(){}\n", encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            return CommandRunResult(
                returncode=1,
                stderr="bad.cc:7 output that matches none of the patterns",
            )

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/g++",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))
        assert result.status == "syntax_dirty"
        assert result.failing_lines == [7]

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

    def test_tool_recovers_and_reports_later_syntax_lines(
        self, tmp_path: Path,
    ) -> None:
        source = tmp_path / "bad.cc"
        source.write_text(
            "\n".join([
                "int first() {",
                "  BAD_ONE",
                "}",
                "int second() {",
                "  BAD_TWO",
                "}",
            ]),
            encoding="utf-8",
        )

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            checked = Path(cmd[-1])
            text = checked.read_text(encoding="utf-8")
            assert cwd == checked.parent
            assert timeout_s == 10
            if "BAD_ONE" in text:
                return CommandRunResult(
                    returncode=1,
                    stderr=(
                        "bad.cc:2:3: error: expected ';' before 'BAD_ONE'"
                    ),
                )
            if "BAD_TWO" in text:
                return CommandRunResult(
                    returncode=1,
                    stderr=(
                        "bad.cc:5:3: error: expected ';' before 'BAD_TWO'"
                    ),
                )
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))

        assert result.status == "syntax_dirty"
        assert result.category == "syntax"
        assert result.failing_lines == [2, 5]
        assert result.syntax_errors == 2
        assert [item.line for item in result.items] == [2, 5]

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

    def test_cpp_missing_include_recovers_later_syntax_error(
        self, tmp_path: Path,
    ) -> None:
        source = tmp_path / "bad.cc"
        source.write_text(
            '#include "missing.h"\nint main() {\n  BAD_TOKEN\n}\n',
            encoding="utf-8",
        )

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            checked = Path(cmd[-1])
            text = checked.read_text(encoding="utf-8")
            assert cwd == checked.parent
            assert timeout_s == 10
            has_missing_stub = any(
                Path(cmd[index + 1], "missing.h").exists()
                for index, part in enumerate(cmd[:-1])
                if part == "-I"
            )
            if '#include "missing.h"' in text and not has_missing_stub:
                return CommandRunResult(
                    returncode=1,
                    stderr=(
                        "bad.cc:1:10: fatal error: missing.h: "
                        "No such file or directory"
                    ),
                )
            if "BAD_TOKEN" in text:
                return CommandRunResult(
                    returncode=1,
                    stderr="bad.cc:3:3: error: expected ';' before 'BAD_TOKEN'",
                )
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))

        assert result.status == "syntax_dirty"
        assert result.failing_lines == [1, 3]
        assert result.dependency_errors == 1
        assert result.syntax_errors == 1
        assert [item.category for item in result.items] == ["dependency", "syntax"]

    def test_cpp_ocr_chinese_noise_marked_even_when_dependencies_block(
        self, tmp_path: Path,
    ) -> None:
        source = tmp_path / "bad.cc"
        source.write_text(
            '#include "missing.h"\n'
            "// 注释里的中文不应标为语法错误 王\n"
            "if(hEglImage 二 EGL_NO_IMAGE_KHR){ 王\n",
            encoding="utf-8",
        )

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            checked = Path(cmd[-1])
            assert cwd == checked.parent
            assert timeout_s == 10
            return CommandRunResult(
                returncode=1,
                stderr=(
                    f"{checked.name}:1:10: fatal error: missing.h: "
                    "No such file or directory"
                ),
            )

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))

        assert result.status == "syntax_dirty"
        assert result.dependency_errors == 1
        assert result.syntax_errors == 1
        assert result.failing_lines == [1, 3]
        assert [(item.line, item.code) for item in result.items] == [
            (1, "missing_include"),
            (3, "ocr_noise_non_ascii"),
        ]

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

    def test_diagnose_text_passes_extra_include_roots(
        self, tmp_path: Path,
    ) -> None:
        """真实兄弟目录应作为 -I 传入，使同目录 #include 可解析（B7 C19）。"""
        sibling_dir = tmp_path / "src"
        sibling_dir.mkdir()
        captured: dict[str, list[str]] = {}

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            captured["cmd"] = cmd
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/g++",
            command_runner=run,
        )
        diagnose_text(
            path="src/foo.cpp",
            language="cpp",
            text='#include "bar.h"\nint main(){}\n',
            include_root=tmp_path,
            extra_include_roots=[sibling_dir],
            runner=runner,
        )
        cmd = captured["cmd"]
        assert "-I" in cmd
        # 兄弟目录与 include_root 都进入 -I 搜索路径。
        assert str(sibling_dir) in cmd
        assert str(tmp_path) in cmd


@pytest.mark.skipif(os.name != "posix", reason="进程组兜底依赖 POSIX killpg")
class TestRunCommandProcessGroup:
    """_run_command 超时进程组兜底（B7 C13）。"""

    def test_timeout_isolates_session_and_kills_process_group(
        self, tmp_path: Path,
    ) -> None:
        """超时应抛 TimeoutExpired，子进程独占进程组且被 killpg 收割（不留孤儿）。"""
        info = tmp_path / "info.txt"
        script = (
            "import os\n"
            f"open({str(info)!r}, 'w').write(f'{{os.getpid()}} {{os.getpgid(0)}}')\n"
            "import time\n"
            "time.sleep(30)\n"
        )
        with pytest.raises(subprocess.TimeoutExpired):
            _run_command([sys.executable, "-c", script], tmp_path, timeout_s=1)

        pid, pgid = (int(part) for part in info.read_text().split())
        # start_new_session 让子进程成为会话/组长，pgid == pid。
        assert pid == pgid
        # killpg 后子进程应被收割，轮询确认不残留。
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("子进程超时后未被进程组清理，存在孤儿")
