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

"""端到端测试：26 张图片 → 完整 Pipeline → 输出 markdown 文档

用法：
    source .venv/bin/activate && source .env
    python scripts/run_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


async def main() -> None:
    """运行完整 pipeline"""
    from docrestore.ocr.deepseek_ocr2 import (
        DeepSeekOCR2Engine,
    )
    from docrestore.pipeline.config import (
        LLMConfig,
        PipelineConfig,
    )
    from docrestore.pipeline.pipeline import Pipeline

    # 输入输出路径
    image_dir = PROJECT_ROOT / "test_images" / "development_guide"
    output_dir = PROJECT_ROOT / "output" / "development_guide"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 列出输入图片
    images = sorted(image_dir.glob("*.JPG"))
    print(f"输入目录: {image_dir}")
    print(f"输出目录: {output_dir}")
    print(f"图片数量: {len(images)}")

    # 配置
    api_key = os.environ.get("GLM_API_KEY", "")
    if not api_key:
        print("错误：GLM_API_KEY 未设置")
        sys.exit(1)

    config = PipelineConfig(
        llm=LLMConfig(
            model="openai/glm-5",
            api_base="https://poloai.top/v1",
            api_key=api_key
        ),
    )

    # 创建 pipeline + 注入 OCR 引擎
    pipeline = Pipeline(config)
    pipeline.set_ocr_engine(DeepSeekOCR2Engine(config.ocr))

    print("\n初始化 pipeline（加载 OCR 模型到 GPU）...")
    t0 = time.time()
    await pipeline.initialize()
    print(f"初始化完成，耗时 {time.time() - t0:.1f}s")

    # 运行
    print("\n开始处理...")
    t1 = time.time()

    def on_progress(p: object) -> None:
        """进度回调"""
        print(f"  [{p.stage}] {p.message}")  # type: ignore[attr-defined]

    result = await pipeline.process(
        image_dir=image_dir,
        output_dir=output_dir,
        on_progress=on_progress,
    )

    elapsed = time.time() - t1
    print(f"\n处理完成，耗时 {elapsed:.1f}s")

    # 输出结果摘要
    print("\n=== 结果摘要 ===")
    print(f"输出文件: {result.output_path}")
    print(f"GAP 数量: {len(result.gaps)}")
    if result.gaps:
        for gap in result.gaps:
            print(f"  GAP: after_image={gap.after_image}")

    # 读取输出文件前几行
    if result.output_path and result.output_path.exists():
        text = result.output_path.read_text(encoding="utf-8")
        print(f"文档长度: {len(text)} 字符")
        print("\n=== 文档前 500 字符 ===")
        print(text[:500])
        print("=== ... ===")

    await pipeline.shutdown()
    print("\n完成！")


if __name__ == "__main__":
    asyncio.run(main())
