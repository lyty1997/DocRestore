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

# DocRestore DeepSeek-OCR-2 环境配置脚本
#
# 安装 DeepSeek-OCR-2 所需的 conda 环境（deepseek_ocr）。
#
# 用法：
#   ./scripts/setup_deepseek_ocr.sh                    # 完整安装（dev + ocr + vendor）
#   ./scripts/setup_deepseek_ocr.sh --no-ocr           # 仅开发依赖，跳过 OCR 和 vendor
#   ./scripts/setup_deepseek_ocr.sh --skip-model       # 跳过模型下载（需手动下载）
#   ./scripts/setup_deepseek_ocr.sh --no-ocr --skip-model
#
# 环境变量：
#   CUDA_TAG           — CUDA 版本标签，默认 cu118（可选 cu121、cu124）
#   VLLM_WHL           — 本地 vllm whl 路径（跳过下载）
#   HF_ENDPOINT        — HuggingFace 镜像地址（如 https://hf-mirror.com）
#
# 官方环境要求（DeepSeek-OCR-2 README）：
#   cuda11.8 + torch==2.6.0 + torchvision==0.21.0 + vllm==0.8.5

set -euo pipefail

# ──────────── 定位项目根目录 ────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ──────────── 默认值 ────────────

CONDA_ENV="deepseek_ocr"
INSTALL_OCR=1
SKIP_MODEL=0

# ──────────── 颜色输出 ────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

# ──────────── 参数解析 ────────────

for arg in "$@"; do
    case "$arg" in
        --no-ocr) INSTALL_OCR=0 ;;
        --skip-model) SKIP_MODEL=1 ;;
        --help|-h)
            echo "用法: $0 [选项]"
            echo ""
            echo "安装 DeepSeek-OCR-2 conda 环境（${CONDA_ENV}）。"
            echo ""
            echo "选项:"
            echo "  --no-ocr       仅开发依赖，跳过 OCR 和 vendor"
            echo "  --skip-model   跳过模型下载（需手动下载或使用镜像）"
            echo "  --help, -h     显示此帮助"
            echo ""
            echo "环境变量:"
            echo "  CUDA_TAG       CUDA 版本标签（默认 cu118）"
            echo "  VLLM_WHL       本地 vllm whl 路径"
            echo "  HF_ENDPOINT    HuggingFace 镜像地址（如 https://hf-mirror.com）"
            exit 0
            ;;
        *)
            err "未知参数: $arg"
            echo "使用 --help 查看用法"
            exit 1
            ;;
    esac
done

# ──────────── 检测 conda ────────────

detect_conda() {
    if command -v conda &>/dev/null; then
        CONDA_BIN="conda"
    elif command -v mamba &>/dev/null; then
        CONDA_BIN="mamba"
    else
        err "未找到 conda 或 mamba，请先安装 Miniconda/Anaconda："
        err "  https://docs.conda.io/en/latest/miniconda.html"
        exit 1
    fi
    log "使用 conda: $(command -v $CONDA_BIN)"

    # 初始化 conda shell hook
    CONDA_BASE=$($CONDA_BIN info --base 2>/dev/null)
    # shellcheck source=/dev/null
    source "$CONDA_BASE/etc/profile.d/conda.sh"
}

# ──────────── 创建/复用 conda 环境 ────────────

ensure_env() {
    local env_name="$1"
    if conda env list 2>/dev/null | grep -q "^${env_name} "; then
        log "conda 环境 ${env_name} 已存在，跳过创建"
    else
        log "创建 conda 环境 ${env_name} (python=3.12) ..."
        $CONDA_BIN create -n "$env_name" python=3.12 -y
    fi
}

# 在指定 conda 环境中执行 pip install
pip_in_env() {
    local env_name="$1"
    shift
    conda run -n "$env_name" pip install "$@"
}

# ──────────── 检查网络连通性（HuggingFace） ────────────

check_hf_network() {
    local hf_host="huggingface.co"
    if [[ -n "${HF_ENDPOINT:-}" ]]; then
        hf_host="${HF_ENDPOINT#*//}"
        hf_host="${hf_host%%/*}"
    fi

    log "检查 HuggingFace 网络连通性 ($hf_host)..."
    if curl --max-time 10 -sI "https://$hf_host" > /dev/null 2>&1; then
        log "网络连通: 可以访问 $hf_host"
        return 0
    else
        warn "无法连接到 $hf_host"
        return 1
    fi
}

# ──────────── 主流程 ────────────

main() {
    echo -e "${CYAN}"
    echo "=========================================="
    echo " DocRestore DeepSeek-OCR-2 环境配置"
    echo "=========================================="
    echo -e "${NC}"

    detect_conda
    ensure_env "$CONDA_ENV"

    log "升级 pip/setuptools/wheel..."
    pip_in_env "$CONDA_ENV" --quiet --upgrade pip setuptools wheel

    # ── OCR 依赖（按官方 README 顺序安装） ──
    if [[ "$INSTALL_OCR" -eq 1 ]]; then
        CUDA_TAG="${CUDA_TAG:-cu118}"
        TORCH_INDEX="https://download.pytorch.org/whl/${CUDA_TAG}"

        log "安装 torch==2.6.0 + torchvision==0.21.0 (${CUDA_TAG})..."
        pip_in_env "$CONDA_ENV" \
            torch==2.6.0 torchvision==0.21.0 \
            --extra-index-url "$TORCH_INDEX"

        # vllm 0.8.5 cu118 whl（PyPI 上没有，需从 GitHub releases 下载）
        VLLM_WHL_NAME="vllm-0.8.5+${CUDA_TAG}-cp38-abi3-manylinux1_x86_64.whl"
        VLLM_WHL_URL="https://github.com/vllm-project/vllm/releases/download/v0.8.5/${VLLM_WHL_NAME}"

        if [[ -n "${VLLM_WHL:-}" ]]; then
            log "使用本地 vllm whl: $VLLM_WHL"
            pip_in_env "$CONDA_ENV" "$VLLM_WHL"
        else
            log "下载并安装 vllm 0.8.5+${CUDA_TAG}..."
            DOWNLOAD_DIR="$PROJECT_ROOT/.cache"
            mkdir -p "$DOWNLOAD_DIR"
            VLLM_LOCAL="$DOWNLOAD_DIR/$VLLM_WHL_NAME"
            if [[ -f "$VLLM_LOCAL" ]]; then
                log "复用已下载的 whl: $VLLM_LOCAL"
            else
                log "从 GitHub 下载 $VLLM_WHL_NAME ..."
                wget -O "$VLLM_LOCAL" "$VLLM_WHL_URL"
            fi
            pip_in_env "$CONDA_ENV" "$VLLM_LOCAL"
        fi

        # 安装 OCR 的纯 Python 依赖 + Pillow（worker 内图片处理需要）
        # 后端依赖（fastapi/uvicorn 等）由独立的 docrestore 环境提供
        log "安装 OCR 依赖: [ocr] + Pillow"
        pip_in_env "$CONDA_ENV" -e ".[ocr]" Pillow

        # 最后降级 transformers/tokenizers（vllm 拉了新版，DeepSeek-OCR-2 要求旧版）
        log "降级 transformers==4.46.3 + tokenizers==0.20.3（DeepSeek-OCR-2 要求）..."
        pip_in_env "$CONDA_ENV" transformers==4.46.3 tokenizers==0.20.3

        # clone DeepSeek-OCR-2 到 vendor/
        VENDOR_DIR="$PROJECT_ROOT/vendor/DeepSeek-OCR-2"
        if [[ -d "$VENDOR_DIR" ]]; then
            log "vendor/DeepSeek-OCR-2 已存在，跳过 clone"
        else
            log "克隆 DeepSeek-OCR-2..."
            mkdir -p "$PROJECT_ROOT/vendor"
            git clone https://github.com/deepseek-ai/DeepSeek-OCR-2.git "$VENDOR_DIR"
        fi

        # 下载 DeepSeek-OCR-2 模型权重（HuggingFace）
        MODEL_REPO="deepseek-ai/DeepSeek-OCR-2"
        MODEL_DIR="$PROJECT_ROOT/models/DeepSeek-OCR-2"
        if [[ -d "$MODEL_DIR" ]]; then
            log "模型权重已存在: $MODEL_DIR，跳过下载"
        elif [[ "$SKIP_MODEL" -eq 1 ]]; then
            log "跳过模型下载（--skip-model 已指定）"
            echo "请手动下载模型："
            echo "  方法1: export HF_ENDPOINT=https://hf-mirror.com && hf download \"$MODEL_REPO\" --local-dir \"$MODEL_DIR\""
            echo "  方法2: 使用 huggingface-cli 或其他工具下载到: $MODEL_DIR"
        elif check_hf_network; then
            log "下载 DeepSeek-OCR-2 模型权重..."
            mkdir -p "$PROJECT_ROOT/models"
            if [[ -n "${HF_ENDPOINT:-}" ]]; then
                log "使用 HuggingFace 镜像: $HF_ENDPOINT"
                export HF_ENDPOINT
            fi
            if conda run -n "$CONDA_ENV" hf download "$MODEL_REPO" --local-dir "$MODEL_DIR"; then
                log "模型下载成功: $MODEL_DIR"
            else
                echo ""
                err "模型下载失败。您可以尝试以下方法："
                echo "  1. 使用镜像站: export HF_ENDPOINT=https://hf-mirror.com && $0"
                echo "  2. 跳过下载: $0 --skip-model（然后手动下载模型）"
                echo "  3. 检查网络连接或代理设置"
                exit 1
            fi
        else
            echo ""
            err "无法连接到 HuggingFace 下载模型。您可以尝试："
            echo "  1. 使用镜像站: export HF_ENDPOINT=https://hf-mirror.com && $0"
            echo "  2. 跳过下载: $0 --skip-model（然后手动下载模型）"
            echo ""
            echo "手动下载命令："
            echo "  export HF_ENDPOINT=https://hf-mirror.com"
            echo "  hf download \"$MODEL_REPO\" --local-dir \"$MODEL_DIR\""
            exit 1
        fi

        # flash-attn（DeepSeek-OCR-2 的 sam_vary_sdpa.py 硬依赖）
        log "安装 flash-attn==2.7.3（需要编译，可能耗时较长）..."
        pip_in_env "$CONDA_ENV" flash-attn==2.7.3 --no-build-isolation
    else
        log "跳过 OCR 依赖安装（--no-ocr）"
        log "如需后端环境，请运行: bash scripts/setup_backend.sh"
    fi

    # 获取 deepseek_ocr python 路径
    local ds_python=""
    ds_python=$(conda run -n "$CONDA_ENV" which python 2>/dev/null) || true

    echo ""
    echo -e "${CYAN}=========================================="
    echo " 安装完成！"
    echo -e "==========================================${NC}"
    echo ""
    if [[ -n "$ds_python" ]]; then
        echo "DeepSeek python 路径（自动检测，也可通过 OCRConfig.deepseek_python 配置）："
        echo "  ${ds_python}"
        echo ""
    fi
    echo "如尚未安装后端环境，请先运行："
    echo "  bash scripts/setup_backend.sh"
    echo ""
    echo "启动服务（后端 + 前端）："
    echo "  bash scripts/start.sh all"
    echo ""
    echo "注意：后端是轻量协调器，不直接依赖 torch/vllm。"
    echo "DeepSeek-OCR-2 以子进程 worker 运行在 ${CONDA_ENV} 环境中，"
    echo "由 EngineManager 在前端选择 DeepSeek 引擎时自动启动。"
}

main
