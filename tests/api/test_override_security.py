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

"""请求级覆盖不能改基础设施字段（#32 RCE / #33 paddle SSRF）。

两道防线：
1. schema 层——``OCRConfigRequest`` 不再声明 ``paddle_python`` /
   ``paddle_server_url`` / ``paddle_server_model_name``，pydantic 默认丢弃。
2. sink 兜底——``_resolve_ocr_config`` 经 ``_OCR_INFRA_OVERRIDE_DENY`` 剔除，
   即便 ``model_dump`` 误带基础设施键也不会流进生效 OCRConfig。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from docrestore.api.routes import (
    _OCR_INFRA_OVERRIDE_DENY,
    _resolve_ocr_config,
)
from docrestore.api.schemas import CreateTaskRequest, OCRConfigRequest
from docrestore.pipeline.config import OCRConfig

#: RCE / SSRF 攻击面的基础设施字段，必须全部在 sink 兜底名单内。
_DANGEROUS_FIELDS = (
    "paddle_python",  # 解释器路径 → 任意二进制 exec（RCE）
    "paddle_server_python",
    "paddle_worker_script",  # worker 脚本 → 任意脚本执行
    "deepseek_python",
    "deepseek_worker_script",
    "paddle_server_url",  # 推理服务地址 → SSRF + 页面图外泄
    "paddle_server_model_name",
)


class TestSchemaDropsInfraFields:
    """schema 层：请求体里的基础设施键被 pydantic 直接丢弃。"""

    def test_infra_fields_not_declared(self) -> None:
        """三个高危字段不在 OCRConfigRequest 的模型字段里。"""
        fields = set(OCRConfigRequest.model_fields)
        assert "paddle_python" not in fields
        assert "paddle_server_url" not in fields
        assert "paddle_server_model_name" not in fields

    def test_infra_keys_in_body_ignored_business_kept(self) -> None:
        """请求体带基础设施键 → dump 不含；业务键正常保留。"""
        req_ocr = OCRConfigRequest.model_validate(
            {
                "paddle_python": "/usr/bin/whoami",
                "paddle_server_url": "http://169.254.169.254/",
                "paddle_server_model_name": "evil",
                "gpu_id": "1",
                "paddle_pipeline": "basic",
            },
        )
        dumped = req_ocr.model_dump(exclude_none=True)
        for field in (
            "paddle_python",
            "paddle_server_url",
            "paddle_server_model_name",
        ):
            assert field not in dumped
        assert dumped["gpu_id"] == "1"
        assert dumped["paddle_pipeline"] == "basic"


class TestResolveOcrConfigSink:
    """sink 层：合成生效 OCRConfig 时基础设施字段恒取服务端默认。"""

    @staticmethod
    def _server_default() -> OCRConfig:
        """带可辨识服务端值的默认 OCRConfig（便于断言未被覆盖）。"""
        return OCRConfig(
            paddle_python="/server/conda/bin/python",
            paddle_server_url="http://localhost:8119/v1",
            paddle_server_model_name="server-model",
        )

    def test_infra_override_ignored_business_applied(self) -> None:
        """请求注入恶意基础设施值被忽略，合法业务字段仍覆盖生效。"""
        req = CreateTaskRequest.model_validate(
            {
                "image_dir": "input-dir",
                "ocr": {
                    "paddle_python": "/usr/bin/whoami",
                    "paddle_server_url": "http://169.254.169.254/",
                    "gpu_id": "2",
                    "paddle_pipeline": "basic",
                },
            },
        )
        default = self._server_default()
        cfg = _resolve_ocr_config(req, default)
        assert cfg is not None
        # 基础设施字段恒为服务端默认（worker argv[0] 不被请求改写）
        assert cfg.paddle_python == "/server/conda/bin/python"
        assert cfg.paddle_server_url == "http://localhost:8119/v1"
        assert cfg.paddle_server_model_name == "server-model"
        # 业务字段照常生效
        assert cfg.gpu_id == "2"
        assert cfg.paddle_pipeline == "basic"

    def test_denylist_strips_even_if_dump_leaks_infra(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """兜底纵深：即便 model_dump 误带基础设施键，sink 也剔除。

        模拟 schema 回归（基础设施字段被误加回 OCRConfigRequest）：在类层
        替换 model_dump 让它返回含 paddle_python 的脏 dump，断言生效配置仍
        取服务端解释器、业务字段照常应用。
        """

        def _leaky_dump(
            self: OCRConfigRequest,  # noqa: ARG001
            **kwargs: object,  # noqa: ARG001
        ) -> dict[str, object]:
            return {"paddle_python": "/usr/bin/whoami", "gpu_id": "3"}

        monkeypatch.setattr(OCRConfigRequest, "model_dump", _leaky_dump)
        req = CreateTaskRequest.model_validate(
            {"image_dir": "input-dir", "ocr": {"gpu_id": "3"}},
        )
        default = self._server_default()
        cfg = _resolve_ocr_config(req, default)
        assert cfg is not None
        assert cfg.paddle_python == "/server/conda/bin/python"
        assert cfg.gpu_id == "3"


class TestDenylistCoverage:
    """兜底名单必须覆盖全部 RCE/SSRF 基础设施字段（防新增字段漏网）。"""

    @pytest.mark.parametrize("field", _DANGEROUS_FIELDS)
    def test_dangerous_field_in_denylist(self, field: str) -> None:
        """每个高危基础设施字段都在 _OCR_INFRA_OVERRIDE_DENY 内。"""
        assert field in _OCR_INFRA_OVERRIDE_DENY


class TestApiBaseGuardEndpoint:
    """端点级：create_task 对请求级 api_base 实施 SSRF 守卫（#33 wiring）。"""

    @pytest.mark.asyncio
    async def test_metadata_api_base_rejected(
        self, api_client: AsyncClient,
    ) -> None:
        """POST 带指向云元数据端点的 api_base → 400，且在建任务前拦截。

        守卫在 llm 配置合成阶段触发（早于 image_dir 校验），故无需真实
        image_dir / OCR 数据。状态码 400 区别于 pydantic 422；detail 含
        ``api_base`` 进一步坐实是本守卫拒绝。
        """
        resp = await api_client.post(
            "/api/v1/tasks",
            json={
                "image_dir": "input-dir",
                "llm": {"api_base": "http://169.254.169.254/v1"},
            },
        )
        assert resp.status_code == 400
        assert "api_base" in resp.json()["detail"]
