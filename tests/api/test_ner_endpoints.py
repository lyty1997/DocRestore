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

"""本地 NER 端点 + fail-fast 守卫单测（S3.4a）。

两层：
1. ``routes._guard_ner_backend`` 单元——仅 enable + 人名/机构名 + ner_backend=spacy +
   不可用时抛 ``ApiBusinessError(NER_BACKEND_UNAVAILABLE, 400)``，其余放行。
2. 端点——``GET /ner/status`` 探测结果透出；``POST /tasks`` 开 PII 但 NER 不可用 400。

探测函数 ``probe_availability`` 经 monkeypatch 注入，不依赖 spaCy 是否真安装。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from docrestore.api import routes
from docrestore.api.errors import APIErrorCode, ApiBusinessError
from docrestore.pipeline.config import PIIConfig


def _probe_unavailable(models: list[str]) -> tuple[bool, list[str], list[str]]:
    """探测桩：spaCy 未装（全部模型计 missing）。"""
    return False, [], list(models)


def _probe_available(models: list[str]) -> tuple[bool, list[str], list[str]]:
    """探测桩：全部配置模型就绪。"""
    return True, list(models), []


class TestGuardNerBackend:
    """_guard_ner_backend：仅"开实体脱敏 + spacy + 不可用"才 400，其余放行。"""

    def test_disabled_pii_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PII 未启用 → 放行（即便 NER 不可用）。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_unavailable)
        routes._guard_ner_backend(PIIConfig(enable=False))

    def test_name_redaction_off_passes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """人名/机构名脱敏都关 → 放行（无需 NER）。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_unavailable)
        routes._guard_ner_backend(
            PIIConfig(
                enable=True, redact_person_name=False, redact_org_name=False,
            ),
        )

    def test_backend_none_passes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ner_backend="none"（知情放弃）→ 放行，不阻断建任务。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_unavailable)
        routes._guard_ner_backend(
            PIIConfig(
                enable=True, redact_person_name=True, ner_backend="none",
            ),
        )

    def test_available_passes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NER 可用 → 放行。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_available)
        routes._guard_ner_backend(
            PIIConfig(enable=True, redact_person_name=True),
        )

    def test_unavailable_raises_400(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """开人名脱敏 + spacy 不可用 → 400 NER_BACKEND_UNAVAILABLE + remediable。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_unavailable)
        with pytest.raises(ApiBusinessError) as ei:
            routes._guard_ner_backend(
                PIIConfig(enable=True, redact_person_name=True),
            )
        assert ei.value.status_code == 400
        assert ei.value.code is APIErrorCode.NER_BACKEND_UNAVAILABLE
        assert ei.value.params["remediable"] is True
        assert ei.value.params["missing_models"]  # 非空（缺的模型透出供前端提示）


class TestNerStatusEndpoint:
    """GET /ner/status：透出探测结果（不加载模型）。"""

    @pytest.mark.asyncio
    async def test_status_reports_unavailable(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """spaCy 未装 → available=False，missing_models 非空。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_unavailable)
        resp = await api_client.get("/api/v1/ner/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["spacy_installed"] is False
        assert body["missing_models"]

    @pytest.mark.asyncio
    async def test_status_reports_available(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全部模型就绪 → available=True，missing_models 空。"""
        monkeypatch.setattr(routes, "probe_availability", _probe_available)
        resp = await api_client.get("/api/v1/ner/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["missing_models"] == []
        assert body["installed_models"]  # 与 configured 一致


class TestCreateEndpointNerGuard:
    """POST /tasks：开 PII 但本地 NER 不可用 → 建任务前 400 拦截。"""

    @pytest.mark.asyncio
    async def test_pii_enabled_but_ner_unavailable_rejected(
        self, api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """请求开 PII（默认开人名/机构名）+ NER 不可用 → 400，detail 含 NER。

        守卫在合成阶段触发、早于真正建任务，故 image_dir 用占位串即可。机器可读
        code/params 已在 TestGuardNerBackend 单元层断言，此处只验状态码 + detail。
        """
        monkeypatch.setattr(routes, "probe_availability", _probe_unavailable)
        resp = await api_client.post(
            "/api/v1/tasks",
            json={"image_dir": "input-dir", "pii": {"enable": True}},
        )
        assert resp.status_code == 400
        assert "NER" in resp.json()["detail"]
