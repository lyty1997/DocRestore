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

# DocRestore 后端环境配置脚本
#
# 安装轻量级后端 conda 环境（docrestore），不含任何 GPU/OCR/LLM 模型依赖。
# 后端作为协调器，通过子进程调用 OCR worker（各自独立 conda 环境）。
#
# 用法：
#   ./scripts/setup_backend.sh          # 安装后端环境
#   ./scripts/setup_backend.sh --help
#
# 安装完成后，还需安装至少一个 OCR 引擎环境：
#   ./scripts/setup_paddle_ocr.sh       # PaddleOCR（推荐）
#   ./scripts/setup_deepseek_ocr.sh     # DeepSeek-OCR-2（备用）

set -euo pipefail

# ──────────── 定位项目根目录 ────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ──────────── 默认值 ────────────

CONDA_ENV="docrestore"

# ──────────── 颜色输出 ────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[setup]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

# ──────────── 参数解析 ────────────

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            echo "用法: $0 [--help]"
            echo ""
            echo "安装 DocRestore 后端 conda 环境（${CONDA_ENV}）。"
            echo "轻量级环境，仅含 FastAPI/uvicorn/litellm 等后端依赖，"
            echo "不含 torch/vllm/paddlepaddle 等 GPU 依赖。"
            echo ""
            echo "安装完成后还需安装 OCR 引擎环境："
            echo "  ./scripts/setup_paddle_ocr.sh       PaddleOCR"
            echo "  ./scripts/setup_deepseek_ocr.sh     DeepSeek-OCR-2"
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

    CONDA_BASE=$($CONDA_BIN info --base 2>/dev/null)
    # shellcheck source=/dev/null
    source "$CONDA_BASE/etc/profile.d/conda.sh"
}

# ──────────── 主流程 ────────────

main() {
    echo -e "${CYAN}"
    echo "=========================================="
    echo " DocRestore 后端环境配置"
    echo "=========================================="
    echo -e "${NC}"

    detect_conda

    # 创建或复用 conda 环境
    if conda env list 2>/dev/null | grep -q "^${CONDA_ENV} "; then
        log "conda 环境 ${CONDA_ENV} 已存在，跳过创建"
    else
        log "创建 conda 环境 ${CONDA_ENV} (python=3.12) ..."
        $CONDA_BIN create -n "$CONDA_ENV" python=3.12 -y
    fi

    log "升级 pip/setuptools/wheel..."
    conda run -n "$CONDA_ENV" pip install --quiet --upgrade pip setuptools wheel

    log "安装项目依赖: [dev]"
    conda run -n "$CONDA_ENV" pip install -e ".[dev]"

    echo ""
    echo -e "${CYAN}=========================================="
    echo " 安装完成！"
    echo -e "==========================================${NC}"
    echo ""
    echo "后端环境: ${CONDA_ENV}（轻量级，无 GPU 依赖）"
    echo ""
    echo "启动服务："
    echo "  bash scripts/start.sh all"
    echo ""
    echo "还需安装至少一个 OCR 引擎环境："
    echo "  bash scripts/setup_paddle_ocr.sh       # PaddleOCR（推荐）"
    echo "  bash scripts/setup_deepseek_ocr.sh     # DeepSeek-OCR-2（备用）"
    echo ""
}

main
