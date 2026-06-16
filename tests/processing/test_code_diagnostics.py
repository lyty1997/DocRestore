# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式多语言诊断单元测试。"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from docrestore.processing import code_diagnostics as cd
from docrestore.processing.code_diagnostics import (
    CodeDiagnosticRunner,
    CodeDiagnosticTarget,
    CommandRunResult,
    _neutralize_unsafe_includes,
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

    def test_ocr_noise_column_uses_source_column(
        self, tmp_path: Path,
    ) -> None:
        """噪声字符前有被剥离的字符串时，列号应是源列而非压缩子串下标（B7 C23）。"""
        source = tmp_path / "bad.cc"
        # 工 在源行第 13 列；旧实现会把它算成剥离 "ab" 后的子串下标 9。
        source.write_text('  x = "ab"; 工\n', encoding="utf-8")

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            return CommandRunResult(returncode=1, stderr="")

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/g++",
            command_runner=run,
        )
        result = runner.run_target(_target(source, "cpp"))
        noise = [
            item for item in result.items
            if item.code == "ocr_noise_non_ascii"
        ]
        assert noise
        assert noise[0].line == 1
        assert noise[0].column == 13

    def test_cpp_include_root_mirrored_into_command(
        self, tmp_path: Path,
    ) -> None:
        """include_root 以中和影子镜像进 -I（不再直传真实目录），目标编译影子副本。

        影子树是临时目录，诊断返回即清理；对其内容的检查全在命令回调内完成。
        """
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "good.h").write_text("int g(void);\n", encoding="utf-8")
        source = src_dir / "bad.cc"
        source.write_text('#include "good.h"\n', encoding="utf-8")
        captured: dict[str, object] = {}

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            include_dirs = [
                Path(cmd[idx + 1])
                for idx, arg in enumerate(cmd)
                if arg == "-I"
            ]
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["compiled"] = cmd[-1]
            captured["sibling_mirrored"] = any(
                (d / "src" / "good.h").is_file() or (d / "good.h").is_file()
                for d in include_dirs
            )
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/tool",
            command_runner=run,
        )
        result = runner.run_target(CodeDiagnosticTarget(
            path="src/bad.cc",
            file_path=source,
            language="cpp",
            include_root=tmp_path,
        ))
        assert result.status == "syntax_clean"
        cmd = captured["cmd"]
        assert isinstance(cmd, list)
        assert "-I" in cmd
        # 真实 include_root 不再逐字直传（已重映射为影子）。
        assert str(tmp_path) not in cmd
        # 编译的是影子副本，cwd 是其父目录，磁盘原文件零改写。
        assert captured["compiled"] != str(source)
        assert captured["cwd"] == Path(str(captured["compiled"])).parent
        assert captured["sibling_mirrored"] is True
        assert source.read_text(encoding="utf-8") == '#include "good.h"\n'

    def test_diagnose_text_mirrors_include_roots(
        self, tmp_path: Path,
    ) -> None:
        """include_root 与兄弟目录都以中和影子镜像进 -I（不再直传真实目录，B7 C19）。"""
        sibling_dir = tmp_path / "src"
        sibling_dir.mkdir()
        (sibling_dir / "bar.h").write_text("int bar(void);\n", encoding="utf-8")
        captured: dict[str, object] = {}

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            include_dirs = [
                Path(cmd[idx + 1])
                for idx, arg in enumerate(cmd)
                if arg == "-I"
            ]
            captured["cmd"] = cmd
            captured["n_includes"] = len(include_dirs)
            captured["bar_mirrored"] = any(
                (d / "bar.h").is_file() for d in include_dirs
            )
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
        assert isinstance(cmd, list)
        assert "-I" in cmd
        # 真实目录不再逐字直传（已重映射为影子）。
        assert str(tmp_path) not in cmd
        assert str(sibling_dir) not in cmd
        # include_root + 兄弟目录 + 草稿父目录都镜像进 -I（>=2）。
        assert isinstance(captured["n_includes"], int)
        assert captured["n_includes"] >= 2
        # 兄弟头 bar.h 经影子可解析。
        assert captured["bar_mirrored"] is True

    def test_diagnose_text_writes_sibling_files(self) -> None:
        """sibling_files 写入同一临时根使同目录 #include 可解析（自审 followup）。"""
        seen: dict[str, bool] = {}

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            # target 为 src/foo.cc → cwd 是临时根下的 src/，兄弟 src/bar.h 应同目录可见
            seen["sibling_present"] = (cwd / "bar.h").exists()
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda _tool: "/usr/bin/g++",
            command_runner=run,
        )
        diagnose_text(
            path="src/foo.cc",
            language="cpp",
            text='#include "bar.h"\nint main(){}\n',
            sibling_files=[
                ("src/bar.h", "int bar();\n"),
                ("src/foo.cc", "SELF_SHOULD_BE_SKIPPED"),  # 同 path 不覆盖目标
            ],
            runner=runner,
        )
        assert seen["sibling_present"] is True


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


class _CaptureRunner:
    """伪命令运行器：读取真正送编译的文件内容并捕获，零退出码。"""

    def __init__(self) -> None:
        self.compiled_text: str | None = None
        self.cmd: list[str] | None = None

    def __call__(
        self, cmd: list[str], cwd: Path, timeout_s: int,
    ) -> CommandRunResult:
        self.cmd = cmd
        # file_arg 恒为 cmd 末位（见 _tool_spec）。
        self.compiled_text = Path(cmd[-1]).read_text(encoding="utf-8")
        return CommandRunResult(returncode=0)


class TestUnsafeIncludeNeutralization:
    """g++/gcc #include LFI 防护：不安全 include 中和后才送预处理器。"""

    @pytest.mark.parametrize(
        ("line", "language", "blocked"),
        [
            ('#include "/etc/passwd"', "c", 1),
            ("#include </etc/passwd>", "cpp", 1),
            ('#include "../../etc/passwd"', "c", 1),
            ('#  include   "../secret.h"', "c", 1),
            ('#include "C:/Windows/win.ini"', "cpp", 1),
            ("#include <stdio.h>", "c", 0),
            ('#include "sibling.h"', "c", 0),
            ('#include "sub/local.h"', "cpp", 0),
            # #import / #include_next 同样按路径真实读文件 → 同等中和（#1d）。
            ('#import "/etc/passwd"', "c", 1),
            ("#import </etc/passwd>", "cpp", 1),
            ('#  import   "../secret.h"', "cpp", 1),
            ('#include_next "/etc/passwd"', "c", 1),
            ("#include_next </etc/passwd>", "cpp", 1),
            ('#include_next "../secret.h"', "c", 1),
            ("#import MACRO", "c", 1),
            ("#include_next FOO_HDR", "cpp", 1),
            ('#import "sibling.h"', "c", 0),
            ("#include_next <stdio.h>", "cpp", 0),
            # 形似但非读文件指令：#define IMPORT / 标识符截断不应误中和。
            ('#define IMPORT "/etc/passwd"', "c", 0),
            ('#includex "/etc/passwd"', "c", 0),
            ('include_str!("/etc/passwd")', "rust", 1),
            ('include!("../mod.rs")', "rust", 1),
            ('#[path = "/abs/mod.rs"]', "rust", 1),
            ('let s = include_str!("data.txt");', "rust", 0),
            ("int main(void){return 0;}", "c", 0),
            # 宏 / 动态表达式 include：目标静态不可知，一律中和（#1c）。
            ("#include MACRO", "c", 1),
            ("#  include FOO_HDR", "cpp", 1),
            ('include!(concat!("a", "b"))', "rust", 1),
            ('include_str!(env!("OUT"))', "rust", 1),
            # #define 自身无害（不读文件），不应被中和。
            ('#define EVIL "/etc/passwd"', "c", 0),
        ],
    )
    def test_neutralize_unit(
        self, line: str, language: str, blocked: int,
    ) -> None:
        text = f"{line}\nint main(void){{return 0;}}\n"
        out, count = _neutralize_unsafe_includes(text, language)
        assert count == blocked
        # 行号守恒：中和是整行替换，不增删行。
        assert len(out.split("\n")) == len(text.split("\n"))
        if blocked:
            assert "/etc" not in out
            assert "blocked unsafe include" in out

    def test_neutralized_before_compile_and_original_untouched(
        self, tmp_path: Path,
    ) -> None:
        src = tmp_path / "evil.c"
        original = '#include "/etc/passwd"\nint main(void){return 0;}\n'
        src.write_text(original, encoding="utf-8")
        capture = _CaptureRunner()
        runner = CodeDiagnosticRunner(
            tool_resolver=lambda tool: f"/usr/bin/{tool}",
            command_runner=capture,
        )
        runner.run_target(_target(src, "c"))
        # 送编译的内容已中和，预处理器读不到目标文件。
        assert capture.compiled_text is not None
        assert "/etc/passwd" not in capture.compiled_text
        # 原文件未被就地改写（只读 NAS / 交付物安全）。
        assert src.read_text(encoding="utf-8") == original

    def test_safe_source_compiled_from_sanitized_shadow(
        self, tmp_path: Path,
    ) -> None:
        """无不安全 include 也走中和影子副本：内容原样、原文件零改写。

        兄弟文件可能藏不安全 include，故安全目标也必须从影子树编译，不能就地编译磁盘
        原文件（否则同目录恶意头会被传递性预处理，#1b）。
        """
        src = tmp_path / "ok.c"
        original = "#include <stdio.h>\nint main(void){return 0;}\n"
        src.write_text(original, encoding="utf-8")
        capture = _CaptureRunner()
        runner = CodeDiagnosticRunner(
            tool_resolver=lambda tool: f"/usr/bin/{tool}",
            command_runner=capture,
        )
        runner.run_target(_target(src, "c"))
        assert capture.cmd is not None
        # 编译的是影子副本而非磁盘原文件。
        assert capture.cmd[-1] != str(src)
        # 安全 include 不被改动，影子副本内容与原文件一致。
        assert capture.compiled_text == original
        # 原文件零改写。
        assert src.read_text(encoding="utf-8") == original

    def test_transitive_sibling_include_neutralized(
        self, tmp_path: Path,
    ) -> None:
        """兄弟头文件里的不安全 include 也被中和：堵传递性 LFI（#1b）。

        目标 main.c 只写合法的 ``#include "evil.h"``（不会被直接中和），evil.h 里藏
        ``#include "/etc/passwd"``。-I 必须指向中和影子树，使预处理器经 evil.h 也读
        不到目标文件；磁盘上的 evil.h 保持原样（交付物零改写）。
        """
        evil_header = tmp_path / "evil.h"
        evil_original = '#include "/etc/passwd"\n'
        evil_header.write_text(evil_original, encoding="utf-8")
        main = tmp_path / "main.c"
        main.write_text(
            '#include "evil.h"\nint main(void){return 0;}\n', encoding="utf-8",
        )
        captured: dict[str, object] = {}

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            # 影子树是临时目录，诊断返回后即清理；必须在命令回调内读取其内容。
            include_dirs = [
                Path(cmd[idx + 1])
                for idx, arg in enumerate(cmd)
                if arg == "-I"
            ]
            captured["headers"] = [
                (d / "evil.h").read_text(encoding="utf-8")
                for d in include_dirs
                if (d / "evil.h").is_file()
            ]
            captured["compiled"] = Path(cmd[-1]).read_text(encoding="utf-8")
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda tool: f"/usr/bin/{tool}",
            command_runner=run,
        )
        runner.run_target(CodeDiagnosticTarget(
            path="main.c",
            file_path=main,
            language="c",
            include_root=tmp_path,
        ))
        headers = captured["headers"]
        assert isinstance(headers, list)
        assert headers, "影子树未包含兄弟头文件 evil.h"
        for content in headers:
            assert "/etc/passwd" not in content
            assert "blocked unsafe include" in content
        # 磁盘上的原兄弟头文件零改写。
        assert evil_header.read_text(encoding="utf-8") == evil_original
        # 编译目标本身（合法 include 保留）也不含 /etc/passwd。
        compiled = captured["compiled"]
        assert isinstance(compiled, str)
        assert "/etc/passwd" not in compiled

    def test_macro_include_directive_neutralized(self, tmp_path: Path) -> None:
        """宏 include（``#define P "/abs"`` + ``#include P``）整行中和（#1c）。

        非字面量 include 无法静态解析目标，预处理器会按宏展开读任意文件；字面量扫描
        挡不住它，故任何非字面量 #include 一律中和。``#define`` 本身保留（无害）。
        """
        src = tmp_path / "macro.c"
        original = (
            '#define EVIL "/etc/passwd"\n'
            "#include EVIL\n"
            "int main(void){return 0;}\n"
        )
        src.write_text(original, encoding="utf-8")
        capture = _CaptureRunner()
        runner = CodeDiagnosticRunner(
            tool_resolver=lambda tool: f"/usr/bin/{tool}",
            command_runner=capture,
        )
        runner.run_target(_target(src, "c"))
        compiled = capture.compiled_text
        assert compiled is not None
        # #include EVIL 已被整行中和，预处理器不会展开宏去读文件。
        assert "#include EVIL" not in compiled
        assert "blocked unsafe include" in compiled
        # 原文件零改写。
        assert src.read_text(encoding="utf-8") == original

    def test_non_utf8_sibling_header_still_neutralized(
        self, tmp_path: Path,
    ) -> None:
        """非 UTF-8 兄弟头里的危险 include 也被中和：堵 copyfile 旁路（#1c）。

        危险 ``#include`` 行本身是 ASCII，文件尾混入非法字节即可触发旧版"解码失败→
        copyfile 原样保留"的旁路。新版用 ``errors="replace"`` 解码后照样中和。
        """
        evil_header = tmp_path / "evil.h"
        evil_bytes = b'#include "/etc/passwd"\n// non-utf8 tail \xff\xfe\n'
        evil_header.write_bytes(evil_bytes)
        main = tmp_path / "main.c"
        main.write_text(
            '#include "evil.h"\nint main(void){return 0;}\n', encoding="utf-8",
        )
        captured: dict[str, object] = {}

        def run(
            cmd: list[str], cwd: Path, timeout_s: int,
        ) -> CommandRunResult:
            include_dirs = [
                Path(cmd[idx + 1])
                for idx, arg in enumerate(cmd)
                if arg == "-I"
            ]
            captured["headers"] = [
                (d / "evil.h").read_text(encoding="utf-8", errors="replace")
                for d in include_dirs
                if (d / "evil.h").is_file()
            ]
            return CommandRunResult(returncode=0)

        runner = CodeDiagnosticRunner(
            tool_resolver=lambda tool: f"/usr/bin/{tool}",
            command_runner=run,
        )
        runner.run_target(CodeDiagnosticTarget(
            path="main.c",
            file_path=main,
            language="c",
            include_root=tmp_path,
        ))
        headers = captured["headers"]
        assert isinstance(headers, list)
        assert headers, "影子树未包含非 UTF-8 兄弟头 evil.h"
        for content in headers:
            assert "/etc/passwd" not in content
            assert "blocked unsafe include" in content
        # 磁盘原文件零改写（仍是原始非法字节）。
        assert evil_header.read_bytes() == evil_bytes

    @pytest.mark.skipif(
        shutil.which("gcc") is None,
        reason="需要真实 gcc 验证宏 include 经预处理器不泄漏文件内容",
    )
    def test_macro_include_no_leak_real_compiler(self, tmp_path: Path) -> None:
        """真 gcc 端到端：宏 include 指向 sentinel，中和后内容不回显进诊断（#1c）。

        未中和时预处理器会真实读取 sentinel 并把内容当 C token 报错回显（LFI）；中和
        后该行变注释，sentinel 不被读取，诊断里不应出现其内容。
        """
        marker = "TOPSECRET_MACRO_LEAK_4f3a"
        sentinel = tmp_path / "secret.txt"
        sentinel.write_text(marker + "\n", encoding="utf-8")
        evil_header = tmp_path / "evil.h"
        evil_header.write_text(
            f'#define P "{sentinel}"\n#include P\n', encoding="utf-8",
        )
        main = tmp_path / "main.c"
        main.write_text(
            '#include "evil.h"\nint main(void){return 0;}\n', encoding="utf-8",
        )
        runner = CodeDiagnosticRunner()  # 真实 tool_resolver + command_runner
        result = runner.run_target(CodeDiagnosticTarget(
            path="main.c",
            file_path=main,
            language="c",
            include_root=tmp_path,
        ))
        blob = result.summary + "\n" + "\n".join(
            item.message for item in result.items
        )
        assert marker not in blob, "宏 include 经真实 gcc 泄漏了 sentinel 内容"
        # sentinel 与磁盘原文件零改写。
        assert sentinel.read_text(encoding="utf-8") == marker + "\n"

    @pytest.mark.skipif(
        shutil.which("gcc") is None,
        reason="需要真实 gcc 验证 #import/#include_next 经预处理器不泄漏文件内容",
    )
    @pytest.mark.parametrize(
        ("directive", "language", "suffix"),
        [
            ("#import", "c", "c"),
            ("#import", "cpp", "cpp"),
            ("#include_next", "c", "c"),
            ("#include_next", "cpp", "cpp"),
        ],
    )
    def test_import_include_next_no_leak_real_compiler(
        self, tmp_path: Path, directive: str, language: str, suffix: str,
    ) -> None:
        """真 gcc 端到端：``#import`` / ``#include_next`` 指向绝对路径 sentinel，中和后
        内容不回显进诊断（#1d）。

        真实 gcc 处理这两条指令时同样会按路径真实读取外部文件，并把内容当编译错误上
        下文回显——只认 ``#include`` 会漏掉这两个同类 LFI 面。中和后该行变注释，
        sentinel 不被读取，诊断里不应出现其内容。
        """
        marker = "TOPSECRET_DIRECTIVE_LEAK_7b1e"
        sentinel = tmp_path / "secret.txt"
        sentinel.write_text(marker + "\n", encoding="utf-8")
        main = tmp_path / f"main.{suffix}"
        main.write_text(
            f'{directive} "{sentinel}"\nint main(void){{return 0;}}\n',
            encoding="utf-8",
        )
        runner = CodeDiagnosticRunner()  # 真实 tool_resolver + command_runner
        result = runner.run_target(CodeDiagnosticTarget(
            path=main.name,
            file_path=main,
            language=language,
            include_root=tmp_path,
        ))
        blob = result.summary + "\n" + "\n".join(
            item.message for item in result.items
        )
        assert marker not in blob, (
            f"{directive} 经真实 gcc 泄漏了 sentinel 内容"
        )
        # sentinel 与磁盘原文件零改写。
        assert sentinel.read_text(encoding="utf-8") == marker + "\n"


class _FakeTimeoutProc:
    """伪 Popen 上下文管理器：communicate 前几次超时，验证 #42 二次 drain 有界。"""

    def __init__(self, *, drain_hangs: bool) -> None:
        """drain_hangs=True 模拟 killpg 后子进程不退、二次 communicate 仍超时。"""
        self.pid = 0
        self.returncode: int | None = None
        self.killed = False
        self.communicate_calls = 0
        self._drain_hangs = drain_hangs

    def __enter__(self) -> _FakeTimeoutProc:
        """进入 with。"""
        return self

    def __exit__(self, *_exc: object) -> None:
        """退出 with，不吞异常。"""
        return

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        """第 1 次（正常等待）必超时；第 2 次（drain）按 drain_hangs 决定。"""
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(cmd="diag", timeout=timeout or 0)
        if self.communicate_calls == 2 and self._drain_hangs:
            raise subprocess.TimeoutExpired(cmd="diag", timeout=timeout or 0)
        self.returncode = -9
        return "", ""

    def kill(self) -> None:
        """强杀本体。"""
        self.killed = True
        self.returncode = -9

    def poll(self) -> int | None:
        """返回退出码（None=仍在跑）。"""
        return self.returncode


class TestRunCommandBoundedDrain:
    """#42：killpg 后二次 communicate 带 timeout，不永挂。"""

    def test_drain_hang_falls_back_to_kill(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """二次 drain 仍超时 → proc.kill() 兜底再短超时回收，整体不阻塞。"""
        fake = _FakeTimeoutProc(drain_hangs=True)
        monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: fake)
        # killpg 真发会误杀测试进程组（pid=0），桩成 no-op
        monkeypatch.setattr(cd, "_kill_process_group", lambda _proc: None)

        with pytest.raises(subprocess.TimeoutExpired):
            _run_command(["x"], tmp_path, 1)

        assert fake.killed  # 二次 drain 超时 → 强杀本体兜底（防永挂）
        assert fake.communicate_calls == 3  # 正常 + 二次 drain + kill 后回收

    def test_drain_success_no_kill(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """二次 drain 成功回收 → 无需强杀本体。"""
        fake = _FakeTimeoutProc(drain_hangs=False)
        monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: fake)
        monkeypatch.setattr(cd, "_kill_process_group", lambda _proc: None)

        with pytest.raises(subprocess.TimeoutExpired):
            _run_command(["x"], tmp_path, 1)

        assert not fake.killed
        assert fake.communicate_calls == 2  # 正常 + 二次 drain 即回收


class TestDiagPreexecMemoryLimit:
    """#42：诊断子进程 preexec 设 RLIMIT_DATA 堆内存上限。"""

    def test_preexec_sets_data_limit(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_diag_preexec 对 RLIMIT_DATA 调 setrlimit 为配置的堆上限。"""
        calls: list[tuple[int, tuple[int, int]]] = []

        class _FakeResource:
            RLIMIT_CPU = 0
            RLIMIT_FSIZE = 1
            RLIMIT_DATA = 2

            @staticmethod
            def setrlimit(res: int, limits: tuple[int, int]) -> None:
                calls.append((res, limits))

        monkeypatch.setattr(cd, "_resource", _FakeResource)
        cd._diag_preexec()

        data = [lim for res, lim in calls if res == _FakeResource.RLIMIT_DATA]
        assert data == [
            (cd._DIAG_RLIMIT_DATA_BYTES, cd._DIAG_RLIMIT_DATA_BYTES),
        ]


class TestNeutralizeIntoSizeCap:
    """#65：兜底中和路径同样施加 5MB 大小上限。"""

    def test_oversize_returns_none_fail_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超大文件 → _neutralize_into 返回 None（fail-closed），不写影子文件。"""
        big = tmp_path / "big.c"
        big.write_text("int main(){}", encoding="utf-8")
        # 把上限调到 0 模拟"超大"，避免真造 5MB 文件
        monkeypatch.setattr(cd, "_DIAG_MIRROR_MAX_FILE_BYTES", 0)
        shadow = tmp_path / "shadow"
        shadow.mkdir()

        assert cd._neutralize_into(big, shadow, "c") is None
        assert list(shadow.iterdir()) == []  # 未写出任何影子文件


class TestSanitizeGateRegistry:
    """#65：中和门改为显式登记表，go 等显式登记不需中和。"""

    def test_registry_consistent_and_excludes_go(self) -> None:
        """c/cpp/rust 需中和；go/js/ts 显式登记为不需；派生集与登记表一致。"""
        assert cd._LANG_NEEDS_INCLUDE_SANITIZE["c"] is True
        assert cd._LANG_NEEDS_INCLUDE_SANITIZE["cpp"] is True
        assert cd._LANG_NEEDS_INCLUDE_SANITIZE["rust"] is True
        assert cd._LANG_NEEDS_INCLUDE_SANITIZE["go"] is False
        derived = {
            lang for lang, needs in cd._LANG_NEEDS_INCLUDE_SANITIZE.items()
            if needs
        }
        assert derived == cd._SANITIZED_LANGUAGES

    def test_go_target_not_shadowed(self, tmp_path: Path) -> None:
        """go 不在中和门 → _sanitize_target 原样返回（不进影子树）。"""
        runner = CodeDiagnosticRunner()
        target = _target(tmp_path / "main.go", "go")
        with contextlib.ExitStack() as stack:
            result = runner._sanitize_target(target, "go", stack)
        assert result is target


class TestIsTraversalOrAbsolute:
    """#66：分段越级/空判定 helper（三处统一复用，安全关键路径）。"""

    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            ([], True),  # 空分段 → 不安全（fail-closed）
            (["a"], False),
            (["a", "b"], False),
            ([".."], True),
            (["a", "..", "b"], True),
            (["..", "etc", "passwd"], True),
        ],
    )
    def test_cases(self, parts: list[str], expected: bool) -> None:
        assert cd._is_traversal_or_absolute(parts) is expected


class TestMirrorCache:
    """#66：run 级 -I 根镜像缓存——同 (root, language) 只镜像一次，跨语言分开。"""

    def test_dedups_same_root_language(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[Path, str]] = []

        def _spy(src: Path, _dest: Path, lang: str) -> int:
            calls.append((src, lang))
            return 0

        monkeypatch.setattr(cd, "_mirror_sanitized_tree", _spy)
        run_shadow = tmp_path / "run"
        run_shadow.mkdir()
        cache = cd._MirrorCache(run_shadow)
        root = (tmp_path / "inc").resolve()

        d1, _ = cache.mirror(root, "c")
        d2, _ = cache.mirror(root, "c")  # 命中缓存
        assert d1 == d2
        assert len(calls) == 1  # 只镜像一次

        # 不同语言（中和规则不同）→ 单独镜像、独立影子
        d3, _ = cache.mirror(root, "rust")
        assert d3 != d1
        assert len(calls) == 2
