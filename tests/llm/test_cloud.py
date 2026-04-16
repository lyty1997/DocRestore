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

"""CloudLLMRefiner 真实调用测试（GLM API）

说明：该用例验证“能调用 + 结构正确 + 不丢失关键内容”。
关键内容来自项目 README 片段，避免与具体测试图片数据集绑定。
"""

from __future__ import annotations

import os
from pathlib import Path

import aiofiles
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

        # 从 README 里截取一段稳定文本作为输入，避免绑死到特定测试图片数据集。
        readme_path = Path(__file__).parents[2] / "README.md"
        async with aiofiles.open(
            readme_path, encoding="utf-8"
        ) as f:
            readme = await f.read()

        lines = [
            ln.strip() for ln in readme.splitlines() if ln.strip()
        ]
        # 取前 30 行里的一段作为输入（包含项目介绍/流程描述）
        snippet = "\n".join(lines[:30])

        raw_md = "<!-- page: page1.jpg -->\n" + snippet

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
        # 输出应包含原文关键内容（不绑定特定测试数据集）
        keywords = ["处理流程", "Markdown", "OCR"]
        assert any(k in result.markdown for k in keywords)

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
