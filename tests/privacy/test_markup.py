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

"""privacy.markup 结构跨度识别测试（脱敏保护区单一真相源）。

覆盖两条隐私正确性修复，断言均从输入变量派生、不写死任何数据集标识：
- markdown 链接只保护 ``](目标)``、放出可见 label（label 是正文，须进 NER + 替换）；
  图片仍整段保护。
- LaTeX 独立公式 ``$$...$$`` 整段保护（不暴露给云端 NER、不被替换破坏公式）。

split_protected：奇数下标=保护段、偶数下标=自由文本；mask_structure：保护段抹白、
正文原样（决定 spaCy NER 能看到什么）。两者共用同一组 _STRUCTURE_PATTERNS。
"""

from __future__ import annotations

from docrestore.privacy.markup import mask_structure, split_protected


def _protected_spans(text: str) -> list[str]:
    """返回 split_protected 的保护段（奇数下标）。"""
    return split_protected(text)[1::2]


def _free_text(text: str) -> str:
    """拼接 split_protected 的自由文本段（偶数下标）供子串断言。"""
    return "".join(split_protected(text)[0::2])


class TestLinkLabelFreeImageProtected:
    """链接 label 放行进脱敏/NER；链接目标与图片整段仍保护。"""

    def test_link_label_is_free_target_protected(self) -> None:
        """``[label](url)``：label 落自由段、``](url)`` 落保护段。"""
        name, url = "林墨", "https://example.com/p"
        text = f"[{name}]({url}) tail"
        assert name in _free_text(text)
        assert f"]({url})" in _protected_spans(text)

    def test_link_label_visible_to_ner(self) -> None:
        """mask_structure 后 label 文字仍可见（NER 能检测），目标被抹白。"""
        name, url = "林墨", "https://example.com/p"
        masked = mask_structure(f"[{name}]({url})")
        assert name in masked  # NER 看得到 label
        assert url not in masked  # 链接目标被抹白（保护「目标」原意）

    def test_image_whole_span_protected(self) -> None:
        """图片整段（alt+src）仍受保护、不被拆出，NER 路径整段抹白。"""
        alt = "alttext"
        text = f"![{alt}](images/x.jpg)"
        assert text in _protected_spans(text)
        assert alt not in mask_structure(text)


class TestDisplayMathProtected:
    """独立公式 ``$$...$$`` 整段保护；行内 ``$...$`` 不回归。"""

    def test_display_math_whole_span_protected(self) -> None:
        """``$$ ... $$`` 成单一保护段，内部 token 不落自由文本。"""
        ent = "alpha"
        text = f"head $$ {ent} = x $$ tail"
        assert f"$$ {ent} = x $$" in _protected_spans(text)
        assert ent not in _free_text(text)

    def test_display_math_masked_for_ner(self) -> None:
        """mask_structure 抹白整段公式，内容不暴露给云端 NER。"""
        ent = "alpha"
        assert ent not in mask_structure(f"head $$ {ent} $$ tail")

    def test_inline_math_still_protected(self) -> None:
        """行内 ``$ ... $`` 不回归，仍整段保护。"""
        ent = "beta"
        text = f"x $ {ent} $ y"
        assert f"$ {ent} $" in _protected_spans(text)
        assert ent not in _free_text(text)

    def test_two_display_blocks_free_text_between(self) -> None:
        """连续两段 ``$$..$$`` 之间的正文落自由段（非贪婪不吞中间）。"""
        text = "$$ a $$ middle $$ b $$"
        assert "middle" in _free_text(text)
        assert "$$ a $$" in _protected_spans(text)
        assert "$$ b $$" in _protected_spans(text)
