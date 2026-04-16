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

"""sanitize_dirname 单元测试"""

from __future__ import annotations

from docrestore.utils.paths import sanitize_dirname


class TestSanitizeDirname:
    """sanitize_dirname 测试"""

    def test_normal_title(self) -> None:
        """普通中文标题保持不变"""
        assert sanitize_dirname("第一章 Linux开发环境") == "第一章 Linux开发环境"

    def test_empty_string(self) -> None:
        """空字符串返回空"""
        assert sanitize_dirname("") == ""
        assert sanitize_dirname("   ") == ""

    def test_path_separator(self) -> None:
        """路径分隔符替换为下划线"""
        result = sanitize_dirname("foo/bar\\baz")
        assert "/" not in result
        assert "\\" not in result

    def test_dangerous_chars(self) -> None:
        """危险字符替换为下划线"""
        result = sanitize_dirname('a:b*c?"d<e>f|g')
        for ch in ':*?"<>|':
            assert ch not in result

    def test_dot_prefix(self) -> None:
        """以 . 开头的标题去除前导点"""
        result = sanitize_dirname("..hidden")
        assert not result.startswith(".")

    def test_traversal_attempt(self) -> None:
        """路径穿越尝试被安全化"""
        result = sanitize_dirname("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_truncation(self) -> None:
        """超长标题截断到 64 字符"""
        long_title = "A" * 100
        result = sanitize_dirname(long_title)
        assert len(result) <= 64

    def test_consecutive_underscores(self) -> None:
        """连续下划线折叠"""
        result = sanitize_dirname("a///b")
        assert "___" not in result
        assert "__" not in result


class TestSanitizeDirnameBoundaries:
    """sanitize_dirname 边界与特殊输入"""

    def test_exact_64_char_boundary(self) -> None:
        """恰好 64 字符（边界内）不截断。"""
        title = "A" * 64
        assert sanitize_dirname(title) == "A" * 64

    def test_65_chars_truncates_to_64(self) -> None:
        """65 字符截断到 64。"""
        title = "A" * 65
        assert len(sanitize_dirname(title)) == 64

    def test_truncation_strips_trailing_underscore(self) -> None:
        """截断切到下划线上时应 rstrip。"""
        # 前 63 字符 + 1 下划线 + 更多字符 → 截断后末尾是下划线 → strip 掉
        title = "A" * 63 + "_" + "B" * 10
        result = sanitize_dirname(title)
        assert len(result) <= 64
        assert not result.endswith("_")

    def test_control_chars_replaced(self) -> None:
        """\\x00-\\x1f 控制字符被替换为下划线并折叠。"""
        title = "a\x00b\x01c\x1fd"
        result = sanitize_dirname(title)
        # 控制字符不留
        for ch in "\x00\x01\x1f":
            assert ch not in result
        # 下划线不连续
        assert "__" not in result

    def test_tab_and_newline_replaced(self) -> None:
        """Tab、换行等属于控制字符范围。"""
        result = sanitize_dirname("a\tb\nc\rd")
        for ch in "\t\n\r":
            assert ch not in result

    def test_only_dangerous_chars_returns_empty(self) -> None:
        """全是危险字符 → 先替换为下划线 → strip → 空。"""
        result = sanitize_dirname('/\\:*?"<>|')
        assert result == ""

    def test_only_underscores_returns_empty(self) -> None:
        """纯下划线输入 strip 后为空。"""
        assert sanitize_dirname("___") == ""

    def test_only_dots_returns_empty(self) -> None:
        """纯点（多为路径穿越残留）去掉后为空。"""
        assert sanitize_dirname("...") == ""
        assert sanitize_dirname("..") == ""
        assert sanitize_dirname(".") == ""

    def test_mixed_traversal_with_dangerous(self) -> None:
        """混合路径穿越 + 危险字符被完整清理。"""
        result = sanitize_dirname("../../usr/bin:sh")
        assert ".." not in result
        assert "/" not in result
        assert ":" not in result

    def test_leading_trailing_whitespace_stripped(self) -> None:
        """前后空白 strip。"""
        assert sanitize_dirname("  hello  ") == "hello"

    def test_internal_spaces_preserved(self) -> None:
        """内部空格应保留（sanitize 不改空格）。"""
        assert sanitize_dirname("第 1 章") == "第 1 章"

    def test_unicode_chars_preserved(self) -> None:
        """中文/其他 Unicode 字符不被替换。"""
        assert sanitize_dirname("章节ABC一二三") == "章节ABC一二三"

    def test_emoji_preserved(self) -> None:
        """emoji 不在危险字符集内，保留。"""
        # 注意：这里不主动测试 emoji 在文件系统的表现，只验证 sanitize 不删它
        assert sanitize_dirname("报告📊Q1") == "报告📊Q1"

    def test_double_dots_only_in_middle(self) -> None:
        """内嵌 .. 被移除但前后字符保留。"""
        result = sanitize_dirname("foo..bar")
        assert ".." not in result
        # 前后合法字符保留
        assert "foo" in result
        assert "bar" in result

    def test_trailing_dots_allowed_inside(self) -> None:
        """普通单个点（非 .. 非开头）保留。"""
        result = sanitize_dirname("v1.2.3")
        # 单个点不是危险字符，且不构成 ..
        assert result == "v1.2.3"

    def test_result_no_path_separator_ever(self) -> None:
        """任何输入结果都不含路径分隔符。"""
        for weird in [
            "a/b", "a\\b", "a//b", "a\\\\b",
            "/", "\\", "///", "a/b/c/d/e",
        ]:
            result = sanitize_dirname(weird)
            assert "/" not in result
            assert "\\" not in result

    def test_idempotent(self) -> None:
        """对已 sanitize 的结果再次调用保持不变。"""
        once = sanitize_dirname("foo/bar*baz")
        twice = sanitize_dirname(once)
        assert once == twice
