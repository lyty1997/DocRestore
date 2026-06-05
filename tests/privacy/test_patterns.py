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

"""结构化 PII 正则检测测试"""

from __future__ import annotations

from docrestore.pipeline.config import PIIConfig
from docrestore.privacy.patterns import redact_structured_pii


class TestPhoneRedaction:
    """手机号检测测试"""

    def test_standard_phone(self) -> None:
        """标准 11 位手机号"""
        cfg = PIIConfig(enable=True)
        text = "联系电话：13812345678"
        result, records = redact_structured_pii(text, cfg)
        assert "13812345678" not in result
        assert cfg.phone_placeholder in result
        assert any(r.kind == "phone" for r in records)

    def test_phone_with_86_prefix(self) -> None:
        """+86 前缀手机号"""
        cfg = PIIConfig(enable=True)
        text = "电话 +8613912345678"
        result, records = redact_structured_pii(text, cfg)
        assert "13912345678" not in result
        assert cfg.phone_placeholder in result

    def test_phone_with_spaces(self) -> None:
        """带空格/短横线的手机号"""
        cfg = PIIConfig(enable=True)
        text = "手机：139 1234 5678"
        result, records = redact_structured_pii(text, cfg)
        assert cfg.phone_placeholder in result

    def test_phone_boundary_no_match(self) -> None:
        """边界不匹配：数字前后有其他数字"""
        cfg = PIIConfig(enable=True)
        text = "编号2013912345678号"
        result, _ = redact_structured_pii(text, cfg)
        # 前后有数字，不应匹配
        assert cfg.phone_placeholder not in result

    def test_phone_disabled(self) -> None:
        """关闭手机号检测"""
        cfg = PIIConfig(enable=True, redact_phone=False)
        text = "电话：13812345678"
        result, records = redact_structured_pii(text, cfg)
        assert "13812345678" in result
        assert not any(r.kind == "phone" for r in records)


class TestEmailRedaction:
    """邮箱检测测试"""

    def test_standard_email(self) -> None:
        """标准邮箱"""
        cfg = PIIConfig(enable=True)
        text = "邮箱：user@example.com"
        result, records = redact_structured_pii(text, cfg)
        assert "user@example.com" not in result
        assert cfg.email_placeholder in result
        assert any(r.kind == "email" for r in records)

    def test_subdomain_email(self) -> None:
        """子域名邮箱"""
        cfg = PIIConfig(enable=True)
        text = "联系 test.user@mail.example.co.jp"
        result, _ = redact_structured_pii(text, cfg)
        assert cfg.email_placeholder in result

    def test_email_disabled(self) -> None:
        """关闭邮箱检测"""
        cfg = PIIConfig(enable=True, redact_email=False)
        text = "邮箱：user@example.com"
        result, _ = redact_structured_pii(text, cfg)
        assert "user@example.com" in result


class TestIDCardRedaction:
    """身份证号检测测试"""

    def test_valid_id_card(self) -> None:
        """合法 18 位身份证号"""
        cfg = PIIConfig(enable=True)
        text = "身份证：110101199003071234"
        result, records = redact_structured_pii(text, cfg)
        assert "110101199003071234" not in result
        assert cfg.id_card_placeholder in result
        assert any(r.kind == "id_card" for r in records)

    def test_id_card_with_x(self) -> None:
        """末位 X 的身份证号"""
        cfg = PIIConfig(enable=True)
        text = "证件号 11010119900307123X"
        result, _ = redact_structured_pii(text, cfg)
        assert cfg.id_card_placeholder in result

    def test_id_card_disabled(self) -> None:
        """关闭身份证检测"""
        cfg = PIIConfig(enable=True, redact_id_card=False)
        text = "身份证：110101199003071234"
        result, _ = redact_structured_pii(text, cfg)
        assert "110101199003071234" in result


class TestBankCardRedaction:
    """银行卡号检测测试"""

    def test_luhn_valid_card(self) -> None:
        """Luhn 校验通过的银行卡号"""
        cfg = PIIConfig(enable=True)
        # 4539 1488 0343 6467 是 Luhn 合法的测试卡号
        text = "卡号：4539148803436467"
        result, records = redact_structured_pii(text, cfg)
        assert "4539148803436467" not in result
        assert cfg.bank_card_placeholder in result
        assert any(r.kind == "bank_card" for r in records)

    def test_luhn_invalid_not_replaced(self) -> None:
        """Luhn 校验不通过的不替换"""
        cfg = PIIConfig(enable=True)
        text = "编号：1234567890123456"
        result, records = redact_structured_pii(text, cfg)
        assert "1234567890123456" in result
        assert not any(r.kind == "bank_card" for r in records)

    def test_card_with_spaces(self) -> None:
        """带空格的银行卡号"""
        cfg = PIIConfig(enable=True)
        text = "卡号 4539 1488 0343 6467"
        result, _ = redact_structured_pii(text, cfg)
        assert cfg.bank_card_placeholder in result


class TestProcessingOrder:
    """处理顺序测试"""

    def test_id_card_not_eaten_by_bank_card(self) -> None:
        """身份证号不被银行卡候选吞掉"""
        cfg = PIIConfig(enable=True)
        text = "证件 110101199003071234 卡号 4539148803436467"
        result, records = redact_structured_pii(text, cfg)
        assert cfg.id_card_placeholder in result
        assert cfg.bank_card_placeholder in result
        kinds = [r.kind for r in records]
        assert "id_card" in kinds
        assert "bank_card" in kinds


class TestCustomPlaceholder:
    """自定义占位符测试"""

    def test_custom_phone_placeholder(self) -> None:
        """自定义手机号占位符"""
        cfg = PIIConfig(
            enable=True,
            phone_placeholder="[PHONE]",
        )
        text = "电话 13812345678"
        result, records = redact_structured_pii(text, cfg)
        assert "[PHONE]" in result
        assert records[0].placeholder == "[PHONE]"


class TestCredentialRedaction:
    """凭据 / token 检测（label 锚定 + URL 内联 + 已知格式）"""

    def test_password_kv_keeps_label_redacts_value(self) -> None:
        """password=value 只替换 value、保留 label"""
        cfg = PIIConfig(enable=True)
        result, records = redact_structured_pii(
            "数据库 password=Secr3t!Pass 连接", cfg,
        )
        assert "Secr3t!Pass" not in result
        assert "password=" in result
        assert cfg.credential_placeholder in result
        assert any(r.kind == "credential" for r in records)

    def test_various_labels(self) -> None:
        """passwd/pwd/token/api_key/secret_key/用户名/账号 等 label 全命中"""
        cfg = PIIConfig(enable=True)
        for text in (
            "passwd: hunter2",
            "pwd=abcDEF",
            "token: someopaquevalue",
            "api_key=ABCdef123456",
            "secret_key = mysecretval",
            "用户名：zhangsan",
            "账号: 6217000010001",
        ):
            result, _ = redact_structured_pii(text, cfg)
            assert cfg.credential_placeholder in result, text

    def test_full_width_colon_and_quoted_value(self) -> None:
        """全角冒号分隔 + 引号包裹 value"""
        cfg = PIIConfig(enable=True)
        r1, _ = redact_structured_pii("密码：MyP@ss123", cfg)
        assert "MyP@ss123" not in r1
        r2, _ = redact_structured_pii('password = "my secret pass"', cfg)
        assert "my secret pass" not in r2

    def test_url_basic_auth(self) -> None:
        """URL 内联 user:pass@ 凭据，保留 scheme 与 host"""
        cfg = PIIConfig(enable=True)
        result, _ = redact_structured_pii(
            "克隆 https://admin:p4ssw0rd@git.example.com/repo.git", cfg,
        )
        assert "admin:p4ssw0rd" not in result
        assert "git.example.com" in result
        assert cfg.credential_placeholder in result

    def test_known_token_formats(self) -> None:
        """sk-/ghp_/AKIA/JWT 无需 label 也命中"""
        cfg = PIIConfig(enable=True)
        for text in (
            "sk-abcdefghijklmnopqrstuvwxyz0123",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
            "AKIAIOSFODNN7EXAMPLE",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVm",
        ):
            result, _ = redact_structured_pii(text, cfg)
            assert cfg.credential_placeholder in result, text

    def test_password_digit_value_not_misclassified_as_phone(self) -> None:
        """password=<11位数字> 归凭据而非手机号（凭据先行处理）"""
        cfg = PIIConfig(enable=True)
        result, records = redact_structured_pii("password=13800001111", cfg)
        assert "13800001111" not in result
        kinds = [r.kind for r in records]
        assert "credential" in kinds
        assert "phone" not in kinds

    def test_toggle_off(self) -> None:
        """redact_credential=False 时不替换"""
        cfg = PIIConfig(enable=True, redact_credential=False)
        result, records = redact_structured_pii("password=Secr3t", cfg)
        assert "Secr3t" in result
        assert not any(r.kind == "credential" for r in records)

    def test_no_false_positive_on_technical_text(self) -> None:
        """技术正文不应被误伤：裸 key/user/token 词、非凭据 KV 保持原样"""
        cfg = PIIConfig(enable=True)
        for text in (
            "key features of the system",
            "user manual and user space",
            "token bucket rate limiter",
            "CONFIG_DDR_FREQ=2133",
            "the public key is shared here",
        ):
            result, records = redact_structured_pii(text, cfg)
            assert result == text, f"误伤: {text!r} -> {result!r}"
            assert not any(r.kind == "credential" for r in records)
