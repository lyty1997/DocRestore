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

"""端到端测试：子目录遍历 → 逐目录 Pipeline → 输出 markdown 文档

用法：
    conda activate docrestore && source .env
    python scripts/run_e2e.py [OPTIONS]

示例：
    # 使用默认配置（PaddleOCR）
    python scripts/run_e2e.py

    # 指定输入输出路径
    python scripts/run_e2e.py -i test_images/my_docs -o output/my_docs

    # 使用 DeepSeek-OCR-2 引擎
    python scripts/run_e2e.py --ocr-model deepseek/ocr-2

    # DeepSeek-OCR-2 自定义参数
    python scripts/run_e2e.py --ocr-model deepseek/ocr-2 \
        --ocr-model-path models/DeepSeek-OCR-2 \
        --ocr-gpu-util 0.8 \
        --ocr-max-model-len 8192 \
        --ocr-max-tokens 8192

    # 自定义 LLM 配置
    python scripts/run_e2e.py --llm-model openai/gpt-4 --llm-api-base https://api.openai.com/v1

DeepSeek-OCR-2 参数（仅 --ocr-model deepseek/ocr-2 时生效）：
    --ocr-model-path       本地权重路径（默认 models/DeepSeek-OCR-2）
    --ocr-gpu-util         GPU 显存利用率 0.0~1.0（默认 0.75）
    --ocr-max-model-len    模型最大上下文长度（默认 8192）
    --ocr-max-tokens       最大生成 token 数（默认 8192）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def _detect_conda_python(env_name: str) -> str:
    """自动检测 conda 环境的 python 路径。"""
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 — conda_bin 来自 shutil.which，可信
            [conda_bin, "run", "-n", env_name, "which", "python"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _print_results(results: Sequence[object]) -> None:
    """打印处理结果摘要。"""
    print(f"  文档数: {len(results)}")

    for i, result in enumerate(results):
        label = getattr(result, "doc_title", "") or f"文档 {i + 1}"
        output_path = getattr(result, "output_path", None)
        doc_dir = getattr(result, "doc_dir", "")
        gaps = getattr(result, "gaps", [])

        print(f"\n  [{i + 1}/{len(results)}] {label}")
        print(f"    输出文件: {output_path}")
        print(f"    子目录: {doc_dir or '（根目录）'}")
        print(f"    GAP 数量: {len(gaps)}")

        if output_path and Path(str(output_path)).exists():
            text = Path(str(output_path)).read_text(encoding="utf-8")
            print(f"    文档长度: {len(text)} 字符")
            print("\n    === 文档前 500 字符 ===")
            print(text[:500])
            print("    === ... ===")


async def main() -> None:
    """运行完整 pipeline"""
    from docrestore.pipeline.config import (
        LLMConfig,
        OCRConfig,
        PipelineConfig,
    )
    from docrestore.pipeline.pipeline import Pipeline

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="端到端测试")
    parser.add_argument(
        "-i", "--input",
        default="test_images/linux_sdk/test_process_many",
        help="输入图片根目录（相对于项目根目录）",
    )
    parser.add_argument(
        "-o", "--output",
        default="output/linux_sdk/test_process_many",
        help="输出根目录（相对于项目根目录）",
    )
    parser.add_argument(
        "--ocr-model",
        default="paddle-ocr/ppocr-v4",
        help="OCR 模型标识符（默认 paddle-ocr/ppocr-v4）",
    )
    parser.add_argument(
        "--paddle-python",
        default="",
        help="PaddleOCR client conda 环境 python 路径（自动检测 ppocr_client）",
    )
    parser.add_argument(
        "--paddle-server-url",
        default="http://localhost:8119/v1",
        help="PaddleOCR genai_server URL（默认 http://localhost:8119/v1）",
    )
    parser.add_argument(
        "--paddle-server-model",
        default="PaddleOCR-VL-1.5-0.9B",
        help="PaddleOCR server 模型名称",
    )
    parser.add_argument(
        "--deepseek-python",
        default="",
        help="DeepSeek-OCR-2 conda 环境 python 路径（自动检测 deepseek_ocr）",
    )
    # DeepSeek-OCR-2 专用参数
    parser.add_argument(
        "--ocr-model-path",
        default="models/DeepSeek-OCR-2",
        help="DeepSeek-OCR-2 本地权重路径（默认 models/DeepSeek-OCR-2）",
    )
    parser.add_argument(
        "--ocr-gpu-util",
        type=float,
        default=0.9,
        help="GPU 显存利用率 0.0~1.0（默认 0.75）",
    )
    parser.add_argument(
        "--ocr-max-model-len",
        type=int,
        default=8192,
        help="模型最大上下文长度（默认 8192）",
    )
    parser.add_argument(
        "--ocr-max-tokens",
        type=int,
        default=8192,
        help="最大生成 token 数（默认 8192）",
    )
    parser.add_argument(
        "--llm-model",
        default="openai/gemini-3.1-flash-lite-preview",
        help="LLM 模型名称",
    )
    parser.add_argument(
        "--llm-api-base",
        default="https://poloai.top/v1",
        help="LLM API base URL",
    )
    parser.add_argument(
        "--llm-api-key-env",
        default="GEMINI_API_KEY",
        help="LLM API key 环境变量名",
    )
    args = parser.parse_args()

    # 输入输出路径
    image_root = PROJECT_ROOT / args.input
    output_root = PROJECT_ROOT / args.output

    print(f"输入根目录: {image_root}")
    print(f"输出根目录: {output_root}")

    # 自动检测 PaddleOCR client python 路径
    paddle_python = args.paddle_python
    if not paddle_python and args.ocr_model.startswith("paddle"):
        paddle_python = _detect_conda_python("ppocr_client")
        if not paddle_python:
            print("错误：未找到 ppocr_client conda 环境，"
                  "请运行 scripts/setup_paddle_ocr.sh 或指定 --paddle-python")
            sys.exit(1)
        print(f"自动检测 PaddleOCR python: {paddle_python}")

    # 自动检测 DeepSeek-OCR-2 python 路径
    deepseek_python = args.deepseek_python
    if not deepseek_python and args.ocr_model.startswith("deepseek"):
        deepseek_python = _detect_conda_python("deepseek_ocr")
        if not deepseek_python:
            print("错误：未找到 deepseek_ocr conda 环境，"
                  "请运行 scripts/setup_deepseek_ocr.sh 或指定 --deepseek-python")
            sys.exit(1)
        print(f"自动检测 DeepSeek python: {deepseek_python}")

    # 配置
    api_key = os.environ.get(args.llm_api_key_env, "")
    if not api_key:
        print(f"错误：环境变量 {args.llm_api_key_env} 未设置")
        sys.exit(1)

    config = PipelineConfig(
        ocr=OCRConfig(
            model=args.ocr_model,
            model_path=args.ocr_model_path,
            gpu_memory_utilization=args.ocr_gpu_util,
            max_model_len=args.ocr_max_model_len,
            max_tokens=args.ocr_max_tokens,
            paddle_python=paddle_python,
            paddle_server_url=args.paddle_server_url,
            paddle_server_model_name=args.paddle_server_model,
            deepseek_python=deepseek_python,
            enable_column_filter=True,
            column_filter_min_sidebar=5,
        ),
        llm=LLMConfig(
            model=args.llm_model,
            api_base=args.llm_api_base,
            api_key=api_key,
            max_retries=5,
            timeout=900,
        ),
    )

    # 创建 pipeline
    pipeline = Pipeline(config)

    print(f"\n初始化 pipeline（OCR 模型: {args.ocr_model}）...")
    t0 = time.time()
    await pipeline.initialize()
    print(f"初始化完成，耗时 {time.time() - t0:.1f}s")

    # 处理（自动遍历子目录 + LLM 文档聚类）
    def on_progress(p: object) -> None:
        """进度回调"""
        print(f"  [{getattr(p, 'stage', '')}] {getattr(p, 'message', '')}")

    print("\n开始处理...")
    t1 = time.time()

    results = await pipeline.process_tree(
        image_dir=image_root,
        output_dir=output_root,
        on_progress=on_progress,
    )

    elapsed = time.time() - t1
    print(f"\n全部处理完成，总耗时 {elapsed:.1f}s")
    _print_results(results)

    await pipeline.shutdown()
    print("\n完成！")


if __name__ == "__main__":
    asyncio.run(main())
