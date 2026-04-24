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

"""OCRCleaner 单元测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from docrestore.models import PageOCR
from docrestore.processing.cleaner import OCRCleaner


@pytest.fixture
def cleaner() -> OCRCleaner:
    """创建清洗器实例"""
    return OCRCleaner()


class TestRemoveRepetitions:
    """段落去重测试"""

    def test_removes_duplicate_paragraphs(
        self, cleaner: OCRCleaner
    ) -> None:
        """相邻重复段落只保留第一个"""
        text = "段落A内容很长很长\n\n段落A内容很长很长\n\n段落B不同"
        result = cleaner.remove_repetitions(text)
        assert result.count("段落A内容很长很长") == 1
        assert "段落B不同" in result

    def test_keeps_different_paragraphs(
        self, cleaner: OCRCleaner
    ) -> None:
        """不同段落全部保留"""
        text = "段落A\n\n段落B\n\n段落C"
        result = cleaner.remove_repetitions(text)
        assert "段落A" in result
        assert "段落B" in result
        assert "段落C" in result

    def test_single_paragraph(
        self, cleaner: OCRCleaner
    ) -> None:
        """单段落原样返回"""
        text = "只有一个段落"
        result = cleaner.remove_repetitions(text)
        assert result == text


class TestRemoveGarbage:
    """乱码移除测试"""

    def test_removes_long_garbage(
        self, cleaner: OCRCleaner
    ) -> None:
        """移除超过阈值的乱码"""
        garbage = "\U0001f600" * 25  # 25 个 emoji
        text = f"正常文本{garbage}后续文本"
        result = cleaner.remove_garbage(text)
        assert "正常文本" in result
        assert "后续文本" in result
        assert garbage not in result

    def test_keeps_normal_text(
        self, cleaner: OCRCleaner
    ) -> None:
        """正常中英文混合文本保留"""
        text = "Hello 你好，这是 Python 3.11 的代码！"
        result = cleaner.remove_garbage(text)
        assert result == text


class TestRemoveUINoise:
    """网页代码框 UI 噪音清理测试"""

    def test_removes_plain_text_copy_line(
        self, cleaner: OCRCleaner
    ) -> None:
        """"Plain Text 复制代码" 整行删除"""
        text = (
            "SPL 正常启动 log:\n"
            "Plain Text 复制代码\n"
            "U-Boot SPL 2020.01\n"
        )
        result = cleaner.remove_ui_noise(text)
        assert "Plain Text 复制代码" not in result
        assert "SPL 正常启动 log:" in result
        assert "U-Boot SPL 2020.01" in result

    def test_removes_various_language_labels(
        self, cleaner: OCRCleaner
    ) -> None:
        """多种语言标签 + 复制代码都要删"""
        for lang in ["Bash", "Python", "Shell", "SQL", "JSON", "YAML"]:
            text = f"正文\n{lang} 复制代码\n代码行"
            result = cleaner.remove_ui_noise(text)
            assert f"{lang} 复制代码" not in result

    def test_removes_bullet_prefixed_ui(
        self, cleaner: OCRCleaner
    ) -> None:
        """▶/▼/☐ 符号开头的 UI 标签行删除"""
        text = "正文\n▶ Plain Text 复制代码\n代码"
        result = cleaner.remove_ui_noise(text)
        assert "复制代码" not in result
        assert "正文" in result

    def test_removes_bare_copy_line(
        self, cleaner: OCRCleaner
    ) -> None:
        """独立一行的"复制代码"也删"""
        text = "上文\n复制代码\n下文"
        result = cleaner.remove_ui_noise(text)
        assert "复制代码" not in result
        assert "上文" in result
        assert "下文" in result

    def test_preserves_inline_copy_text(
        self, cleaner: OCRCleaner
    ) -> None:
        """正文中含"复制代码"的句子不受影响"""
        text = "使用 Ctrl+C 复制代码，然后粘贴到终端"
        result = cleaner.remove_ui_noise(text)
        assert result == text

    def test_preserves_language_in_prose(
        self, cleaner: OCRCleaner
    ) -> None:
        """正文提到"Python 代码"这种不能误删"""
        text = "下面是一段 Python 代码的例子"
        result = cleaner.remove_ui_noise(text)
        assert result == text


class TestNormalizeWhitespace:
    """空行规范化测试"""

    def test_compress_multiple_blank_lines(
        self, cleaner: OCRCleaner
    ) -> None:
        """3+ 空行压缩为 2 个"""
        text = "段落A\n\n\n\n\n段落B"
        result = cleaner.normalize_whitespace(text)
        assert result == "段落A\n\n段落B"

    def test_keeps_double_blank_lines(
        self, cleaner: OCRCleaner
    ) -> None:
        """2 个空行保持不变"""
        text = "段落A\n\n段落B"
        result = cleaner.normalize_whitespace(text)
        assert result == text


class TestClean:
    """clean() 集成测试"""

    @pytest.mark.asyncio
    async def test_clean_with_fake_data(
        self, cleaner: OCRCleaner
    ) -> None:
        """用构造的假数据测试完整清洗流程"""
        raw = (
            "标题\n\n\n\n\n"  # 多余空行
            "段落A内容比较长的一段话\n\n"
            "段落A内容比较长的一段话\n\n"  # 重复段落
            "段落B"
        )
        page = PageOCR(
            image_path=Path("/img/test.jpg"),
            image_size=(1603, 1720),
            raw_text=raw,
        )
        result = await cleaner.clean(page)
        assert result is page
        assert result.cleaned_text != ""
        assert "\n\n\n" not in result.cleaned_text
        assert (
            result.cleaned_text.count("段落A内容比较长的一段话")
            == 1
        )

    @pytest.mark.asyncio
    async def test_clean_with_sample_data(
        self, cleaner: OCRCleaner, sample_ocr_dir: Path | None
    ) -> None:
        """用真实样例数据 smoke test"""
        if sample_ocr_dir is None:
            pytest.skip("样例 OCR 数据不存在")

        page = PageOCR(
            image_path=sample_ocr_dir.parent
            / f"{sample_ocr_dir.stem.removesuffix('_OCR')}.JPG",
            image_size=(1603, 1720),
            raw_text="",
            output_dir=sample_ocr_dir,
        )
        result = await cleaner.clean(page)
        assert result.cleaned_text != ""
