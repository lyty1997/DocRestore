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

"""PII 实体检测 prompt 结构测试"""

from __future__ import annotations

from docrestore.llm.prompts import (
    PII_DETECT_SYSTEM_PROMPT,
    build_pii_detect_prompt,
)


class TestPIIDetectPrompt:
    """PII 实体检测 prompt 测试"""

    def test_messages_structure(self) -> None:
        """messages 包含 system + user 两条"""
        messages = build_pii_detect_prompt("测试文本")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_system_contains_json_format(self) -> None:
        """system prompt 包含 JSON 格式要求"""
        assert "JSON" in PII_DETECT_SYSTEM_PROMPT
        assert "person_names" in PII_DETECT_SYSTEM_PROMPT
        assert "org_names" in PII_DETECT_SYSTEM_PROMPT

    def test_user_contains_input_text(self) -> None:
        """user message 就是待检测文本本身（无包装 / 无截断）。"""
        sample = "张三在腾讯公司工作"
        messages = build_pii_detect_prompt(sample)
        # 等值断言：PII 检测 prompt 故意不对文本做 wrapping，
        # 防止未来偷偷改成 f"请识别：{sample}" 时降低检测精度。
        assert messages[1]["content"] == sample

    def test_user_preserves_placeholders_verbatim(self) -> None:
        """带方括号占位符的文本必须原样透传，不可被改写或转义。"""
        sample = "张三的 [手机号] 是 xxx，邮箱见 [邮箱]。"
        messages = build_pii_detect_prompt(sample)
        assert messages[1]["content"] == sample
        # 占位符原样保留
        assert "[手机号]" in messages[1]["content"]
        assert "[邮箱]" in messages[1]["content"]

    def test_system_mentions_ignore_placeholders(self) -> None:
        """system prompt 提到忽略占位符"""
        assert "占位符" in PII_DETECT_SYSTEM_PROMPT
