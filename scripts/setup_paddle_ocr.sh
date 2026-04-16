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

# DocRestore PaddleOCR 环境配置脚本
#
# 安装 PaddleOCR server（ppocr_vlm）和 client（ppocr_client）conda 环境。
#
# 用法：
#   ./scripts/setup_paddle_ocr.sh                # 安装 server + client
#   ./scripts/setup_paddle_ocr.sh --server-only   # 仅安装 server 环境
#   ./scripts/setup_paddle_ocr.sh --client-only   # 仅安装 client 环境
#   ./scripts/setup_paddle_ocr.sh --help
#
# 环境变量：
#   PPOCR_GPU_MEMORY_UTIL  — 显存利用率（默认 0.85）
#   CUDA_VERSION           — CUDA 工具链版本（默认 12.8）
#   PADDLE_GPU_VERSION     — paddlepaddle-gpu 版本（默认 3.3.0）
#   PADDLE_GPU_WHL_INDEX   — paddlepaddle-gpu pip index
#   FLASH_ATTN_VERSION     — flash-attn 版本（默认 2.8.2）
#   MAX_JOBS               — flash-attn 编译并行数（默认 4）

set -euo pipefail

# ──────────── 定位项目根目录 ────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ──────────── 默认值 ────────────

PPOCR_GPU_MEMORY_UTIL="${PPOCR_GPU_MEMORY_UTIL:-0.85}"
CUDA_VERSION="${CUDA_VERSION:-12.8}"
PADDLE_GPU_VERSION="${PADDLE_GPU_VERSION:-3.3.0}"
PADDLE_GPU_WHL_INDEX="${PADDLE_GPU_WHL_INDEX:-https://www.paddlepaddle.org.cn/packages/stable/cu126/}"
FLASH_ATTN_VERSION="${FLASH_ATTN_VERSION:-2.8.2}"
MAX_JOBS="${MAX_JOBS:-4}"

SERVER_ENV="ppocr_vlm"
CLIENT_ENV="ppocr_client"

INSTALL_SERVER=true
INSTALL_CLIENT=true

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

show_help() {
    echo "用法: $0 [--server-only | --client-only | --help]"
    echo ""
    echo "安装 PaddleOCR server 和 client conda 环境。"
    echo ""
    echo "选项："
    echo "  --server-only   仅安装 server 环境（ppocr_vlm）"
    echo "  --client-only   仅安装 client 环境（ppocr_client）"
    echo "  --help          显示帮助"
    echo ""
    echo "环境变量："
    echo "  PPOCR_GPU_MEMORY_UTIL   显存利用率（默认 0.85）"
    echo "  CUDA_VERSION            CUDA 工具链版本（默认 12.8）"
    echo "  PADDLE_GPU_VERSION      paddlepaddle-gpu 版本（默认 3.3.0）"
    echo "  FLASH_ATTN_VERSION      flash-attn 版本（默认 2.8.2）"
    echo "  MAX_JOBS                flash-attn 编译并行数（默认 4）"
}

for arg in "$@"; do
    case "$arg" in
        --server-only)
            INSTALL_CLIENT=false
            ;;
        --client-only)
            INSTALL_SERVER=false
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            err "未知参数: $arg"
            show_help
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

# ──────────── Server 环境安装 ────────────

install_server() {
    log ""
    log "=========================================="
    log " 安装 Server 环境: ${SERVER_ENV}"
    log "=========================================="

    ensure_env "$SERVER_ENV"

    log "安装 CUDA ${CUDA_VERSION} 工具链..."
    conda run -n "$SERVER_ENV" \
        conda install -c nvidia \
        "cuda-nvcc=${CUDA_VERSION}" "cuda-toolkit=${CUDA_VERSION}" -y

    log "安装 paddleocr[doc-parser]..."
    pip_in_env "$SERVER_ENV" "paddleocr[doc-parser]"

    # flash-attn 必须从源码编译，依赖 nvcc ${CUDA_VERSION} + torch
    # paddleocr install_genai_server_deps 内部会用带 build isolation 的 pip
    # 装 flash-attn，隔离环境里没有 torch 会失败。
    # 因此先手动装好 torch + flash-attn，再跑 install_genai_server_deps。

    log "预装 torch==2.8.0（flash-attn 源码编译依赖）..."
    pip_in_env "$SERVER_ENV" "torch==2.8.0"

    log "源码编译 flash-attn==${FLASH_ATTN_VERSION}（需要 nvcc ${CUDA_VERSION} + torch，耗时较长）..."
    conda run -n "$SERVER_ENV" \
        pip uninstall flash-attn -y 2>/dev/null || true
    if MAX_JOBS="$MAX_JOBS" conda run -n "$SERVER_ENV" \
        pip install "flash-attn==${FLASH_ATTN_VERSION}" \
        --no-build-isolation --no-cache-dir; then
        log "flash-attn 安装成功"
    else
        warn "flash-attn 编译失败，可能需要检查 CUDA 版本兼容性"
        warn "可稍后手动安装: conda run -n ${SERVER_ENV} pip install flash-attn==${FLASH_ATTN_VERSION} --no-build-isolation"
    fi

    # torch + flash-attn 已就绪，剩余依赖（vllm 等）由此命令补齐
    log "安装 vllm 及其余 genai server 依赖..."
    conda run -n "$SERVER_ENV" \
        paddleocr install_genai_server_deps vllm

    # 修改显存利用率配置
    log "修改显存利用率为 ${PPOCR_GPU_MEMORY_UTIL}..."
    local config_path
    config_path=$(conda run -n "$SERVER_ENV" python -c "
import paddlex, os
print(os.path.join(os.path.dirname(paddlex.__file__),
    'inference/genai/configs/paddleocr_vl_09b.py'))
" 2>/dev/null) || true

    if [[ -n "$config_path" && -f "$config_path" ]]; then
        sed -i "s/\"gpu-memory-utilization\": [0-9.]*/\"gpu-memory-utilization\": ${PPOCR_GPU_MEMORY_UTIL}/" \
            "$config_path"
        log "已修改: $config_path"
    else
        warn "未找到 paddlex genai 配置文件，跳过显存利用率修改"
        warn "可稍后手动修改 paddleocr_vl_09b.py 中的 gpu-memory-utilization"
    fi

    log "Server 环境安装完成 ✓"
}

# ──────────── Client 环境安装 ────────────

install_client() {
    log ""
    log "=========================================="
    log " 安装 Client 环境: ${CLIENT_ENV}"
    log "=========================================="

    ensure_env "$CLIENT_ENV"

    log "安装 PaddlePaddle GPU ${PADDLE_GPU_VERSION}..."
    pip_in_env "$CLIENT_ENV" \
        "paddlepaddle-gpu==${PADDLE_GPU_VERSION}" \
        -i "$PADDLE_GPU_WHL_INDEX"

    log "安装 paddleocr[doc-parser]..."
    pip_in_env "$CLIENT_ENV" -U "paddleocr[doc-parser]"

    log "Client 环境安装完成 ✓"
    log "（注意：ppocr_client 仅供 paddle_ocr_worker 使用，后端请安装独立的 docrestore 环境）"
}

# ──────────── 主流程 ────────────

main() {
    echo -e "${CYAN}"
    echo "=========================================="
    echo " DocRestore PaddleOCR 环境配置"
    echo "=========================================="
    echo -e "${NC}"

    detect_conda

    if $INSTALL_SERVER; then
        install_server
    fi

    if $INSTALL_CLIENT; then
        install_client
    fi

    # 获取 client python 路径
    local client_python=""
    if $INSTALL_CLIENT; then
        client_python=$(conda run -n "$CLIENT_ENV" \
            which python 2>/dev/null) || true
    fi

    echo ""
    echo -e "${CYAN}=========================================="
    echo " 安装完成！"
    echo -e "==========================================${NC}"

    if $INSTALL_SERVER; then
        local server_python=""
        server_python=$(conda run -n "$SERVER_ENV" \
            which python 2>/dev/null) || true

        echo ""
        echo "ppocr-server 由 EngineManager 自动管理（选择 PaddleOCR 时自动启动）。"
        if [[ -n "$server_python" ]]; then
            echo "Server python 路径（自动检测，也可通过 OCRConfig.paddle_server_python 配置）："
            echo "  ${server_python}"
        fi
        echo ""
        echo "如需手动启动 ppocr-server（可选）："
        echo "  bash scripts/start.sh ppocr-server"
        echo "  自定义 GPU 和端口："
        echo "  PPOCR_GPU_ID=0 PPOCR_PORT=9119 bash scripts/start.sh ppocr-server"
    fi

    if $INSTALL_CLIENT && [[ -n "$client_python" ]]; then
        echo ""
        echo "Client python 路径（自动检测，也可通过 OCRConfig.paddle_python 配置）："
        echo "  ${client_python}"
    fi

    echo ""
    echo "如尚未安装后端环境，请先运行："
    echo "  bash scripts/setup_backend.sh"
    echo ""
    echo "启动服务："
    echo "  bash scripts/start.sh all"
    echo ""
    echo "E2E 测试："
    echo "  python scripts/run_e2e.py --paddle-server-url http://localhost:8119/v1"
    echo ""
}

main
