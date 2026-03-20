#!/usr/bin/env bash

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

# DocRestore 环境配置脚本
#
# 用法：
#   ./scripts/setup.sh          # 完整安装（dev + ocr + vendor）
#   ./scripts/setup.sh --no-ocr # 仅开发依赖，跳过 OCR 和 vendor
#
# 环境变量：
#   PYTHON_BIN         — 指定 Python 解释器路径
#   CUDA_TAG           — CUDA 版本标签，默认 cu118（可选 cu121、cu124）
#   VLLM_WHL           — 本地 vllm whl 路径（跳过下载）
#
# 官方环境要求（DeepSeek-OCR-2 README）：
#   cuda11.8 + torch==2.6.0 + torchvision==0.21.0 + vllm==0.8.5

set -euo pipefail

# 定位项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 解析参数
INSTALL_OCR=1
for arg in "$@"; do
    case "$arg" in
        --no-ocr) INSTALL_OCR=0 ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

# 选择 Python 解释器
select_python() {
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        echo "$PYTHON_BIN"
        return
    fi
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" &>/dev/null; then
            echo "$candidate"
            return
        fi
    done
    echo "错误：未找到 Python 3 解释器" >&2
    exit 1
}

PYTHON="$(select_python)"
PY_VERSION="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "使用 Python: $PYTHON ($PY_VERSION)"

if [[ "$PY_VERSION" == "3.13" ]]; then
    echo "警告：Python 3.13 尚未充分测试，部分依赖可能不兼容"
fi

# 创建虚拟环境
VENV_DIR="$PROJECT_ROOT/.venv"
if [[ -d "$VENV_DIR" ]]; then
    echo "复用已有虚拟环境: $VENV_DIR"
else
    echo "创建虚拟环境: $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "升级 pip/setuptools/wheel..."
pip install --quiet --upgrade pip setuptools wheel

# ── OCR 依赖（按官方 README 顺序安装） ──
if [[ "$INSTALL_OCR" -eq 1 ]]; then
    CUDA_TAG="${CUDA_TAG:-cu118}"
    TORCH_INDEX="https://download.pytorch.org/whl/${CUDA_TAG}"

    echo "安装 torch==2.6.0 + torchvision==0.21.0 (${CUDA_TAG})..."
    pip install torch==2.6.0 torchvision==0.21.0 --index-url "$TORCH_INDEX"

    # vllm 0.8.5 cu118 whl（PyPI 上没有，需从 GitHub releases 下载）
    VLLM_WHL_NAME="vllm-0.8.5+${CUDA_TAG}-cp38-abi3-manylinux1_x86_64.whl"
    VLLM_WHL_URL="https://github.com/vllm-project/vllm/releases/download/v0.8.5/${VLLM_WHL_NAME}"

    if [[ -n "${VLLM_WHL:-}" ]]; then
        echo "使用本地 vllm whl: $VLLM_WHL"
        pip install "$VLLM_WHL"
    else
        echo "下载并安装 vllm 0.8.5+${CUDA_TAG}..."
        DOWNLOAD_DIR="$PROJECT_ROOT/.cache"
        mkdir -p "$DOWNLOAD_DIR"
        VLLM_LOCAL="$DOWNLOAD_DIR/$VLLM_WHL_NAME"
        if [[ -f "$VLLM_LOCAL" ]]; then
            echo "复用已下载的 whl: $VLLM_LOCAL"
        else
            echo "从 GitHub 下载 $VLLM_WHL_NAME ..."
            curl -L -o "$VLLM_LOCAL" "$VLLM_WHL_URL"
        fi
        pip install "$VLLM_LOCAL"
    fi

    # 安装项目（dev + ocr 的纯 Python 依赖）
    echo "安装项目依赖: [dev,ocr]"
    pip install -e ".[dev,ocr]"

    # 最后降级 transformers/tokenizers（vllm 拉了新版，DeepSeek-OCR-2 要求旧版）
    # 官方 README: "you don't need to worry about this installation error"
    echo "降级 transformers==4.46.3 + tokenizers==0.20.3（DeepSeek-OCR-2 要求）..."
    pip install transformers==4.46.3 tokenizers==0.20.3

    # clone DeepSeek-OCR-2 到 vendor/
    VENDOR_DIR="$PROJECT_ROOT/vendor/DeepSeek-OCR-2"
    if [[ -d "$VENDOR_DIR" ]]; then
        echo "vendor/DeepSeek-OCR-2 已存在，跳过 clone"
    else
        echo "克隆 DeepSeek-OCR-2..."
        mkdir -p "$PROJECT_ROOT/vendor"
        git clone https://github.com/deepseek-ai/DeepSeek-OCR-2.git "$VENDOR_DIR"
    fi

    # 下载 DeepSeek-OCR-2 模型权重（HuggingFace）
    MODEL_REPO="deepseek-ai/DeepSeek-OCR-2"
    MODEL_DIR="$PROJECT_ROOT/models/DeepSeek-OCR-2"
    if [[ -d "$MODEL_DIR" ]]; then
        echo "模型权重已存在: $MODEL_DIR，跳过下载"
    else
        echo "下载 DeepSeek-OCR-2 模型权重..."
        mkdir -p "$PROJECT_ROOT/models"
        huggingface-cli download "$MODEL_REPO" --local-dir "$MODEL_DIR"
    fi

    # flash-attn（DeepSeek-OCR-2 的 sam_vary_sdpa.py 硬依赖）
    echo "安装 flash-attn==2.7.3（需要编译，可能耗时较长）..."
    pip install flash-attn==2.7.3 --no-build-isolation
else
    echo "安装项目依赖: [dev]"
    pip install -e ".[dev]"
fi

echo ""
echo "安装完成！激活环境："
echo "  source .venv/bin/activate"