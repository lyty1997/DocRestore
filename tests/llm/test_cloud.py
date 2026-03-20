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

"""CloudLLMRefiner 真实调用测试（GLM API）"""

from __future__ import annotations

import os

import pytest

from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.models import RefineContext
from docrestore.pipeline.config import LLMConfig

_API_KEY = os.environ.get("GLM_API_KEY", "")


@pytest.mark.skipif(
    not _API_KEY,
    reason="GLM_API_KEY 未设置，跳过云端测试",
)
class TestCloudLLMRefiner:
    """CloudLLMRefiner 真实 API 调用测试"""

    @pytest.mark.asyncio
    async def test_refine_basic(self) -> None:
        """基本精修：发送 OCR markdown，验证返回结构"""
        config = LLMConfig(
            model="openai/glm-5",
            api_base="https://poloai.top/v1",
            api_key=_API_KEY,
        )
        refiner = CloudLLMRefiner(config)

        raw_md = (
            "<!-- page: DSC04654.jpg -->\n"
            "# 概述\n\n"
            "开发指南概述\n\n"
            "TH1520 Linux SDK采用Yocto作为系统构建方式，"
            "为方便快速上手，本文从不同的开发者视角阐述了"
            "如何在Yocto体系下进行开发：\n\n"
            "·镜像编译：提供如何搭建编译环境，编译镜像\n\n"
            "·应用开发：用户应用程序开发与部署等\n\n"
            "·系统开发：外设驱动、新增开发板等"
        )
        context = RefineContext(
            segment_index=1,
            total_segments=1,
            overlap_before="",
            overlap_after="",
        )

        result = await refiner.refine(raw_md, context)

        # 验证返回结构
        assert result.markdown != ""
        assert isinstance(result.gaps, list)
        # 输出应包含原文关键内容
        assert "TH1520" in result.markdown or "SDK" in result.markdown

        # 打印实际输出供人工检查
        print("\n=== GLM API 真实输出 ===")
        print(result.markdown)
        print(f"\n=== 检测到 {len(result.gaps)} 个 GAP ===")
        for gap in result.gaps:
            print(
                f"  after_image={gap.after_image}, "
                f"before={gap.context_before!r}, "
                f"after={gap.context_after!r}"
            )
        print("=== 输出结束 ===")
