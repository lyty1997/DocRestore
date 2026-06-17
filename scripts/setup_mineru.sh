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

# DocRestore MinerU 环境配置脚本
#
# 创建 MinerU 专属、独立、不与其他环境混用的 conda 环境（mineru_ocr），
# 安装 pipeline 后端依赖（torch+transformers+onnxruntime+UniMERNet 公式等）。
# 模型权重为用户级共享缓存（~/mineru.json + modelscope/HF cache），与 env 无关，
# 已存在则跳过下载。
#
# 用法：
#   ./scripts/setup_mineru.sh                 # 建环境 + 装 mineru[pipeline] + 按需下载权重
#   ./scripts/setup_mineru.sh --with-vlm      # 额外装 vlm 后端依赖（vllm 等，重）
#   ./scripts/setup_mineru.sh --skip-model    # 跳过模型下载
#
# 环境变量：
#   MINERU_MODEL_SOURCE  模型源 huggingface|modelscope（默认 modelscope）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CONDA_ENV="mineru_ocr"
PY_VERSION="3.12"
SKIP_MODEL=0
WITH_VLM=0
MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

for arg in "$@"; do
    case "$arg" in
        --with-vlm)   WITH_VLM=1 ;;
        --skip-model) SKIP_MODEL=1 ;;
        --help|-h)
            echo "用法: $0 [--with-vlm] [--skip-model]"
            echo "  创建独立 conda 环境 ${CONDA_ENV} 并安装 MinerU pipeline 后端。"
            echo "  --with-vlm    额外安装 vlm 后端依赖（vllm 等，重）"
            echo "  --skip-model  跳过模型权重下载"
            exit 0
            ;;
        *)
            err "未知参数: $arg（用 --help 查看用法）"
            exit 1
            ;;
    esac
done

detect_conda() {
    if command -v conda &>/dev/null; then
        CONDA_BIN="conda"
    elif command -v mamba &>/dev/null; then
        CONDA_BIN="mamba"
    else
        err "未找到 conda 或 mamba，请先安装 Miniconda/Anaconda"
        exit 1
    fi
    log "使用 conda: $(command -v $CONDA_BIN)"
    CONDA_BASE=$($CONDA_BIN info --base 2>/dev/null)
    # shellcheck source=/dev/null
    source "$CONDA_BASE/etc/profile.d/conda.sh"
}

ensure_env() {
    if conda env list 2>/dev/null | grep -q "^${CONDA_ENV} "; then
        log "conda 环境 ${CONDA_ENV} 已存在，跳过创建"
    else
        log "创建独立 conda 环境 ${CONDA_ENV} (python=${PY_VERSION}) ..."
        $CONDA_BIN create -n "$CONDA_ENV" "python=${PY_VERSION}" -y
    fi
}

main() {
    echo -e "${CYAN}"
    echo "=========================================="
    echo " DocRestore MinerU 环境配置 (${CONDA_ENV})"
    echo "=========================================="
    echo -e "${NC}"

    detect_conda
    ensure_env

    log "升级 pip/setuptools/wheel ..."
    conda run -n "$CONDA_ENV" pip install --quiet --upgrade pip setuptools wheel

    local extras="pipeline"
    if [[ "$WITH_VLM" -eq 1 ]]; then
        extras="pipeline,vlm"
        warn "包含 vlm 依赖（vllm 等），安装体积较大"
    fi
    log "安装 mineru[${extras}] ..."
    conda run -n "$CONDA_ENV" pip install -U "mineru[${extras}]"
    # six 是 pipeline OCR(pytorchocr) 的隐式依赖，mineru[pipeline] 未声明，补装
    log "补装缺失的传递依赖 six ..."
    conda run -n "$CONDA_ENV" pip install --quiet six

    log "校验安装 ..."
    conda run -n "$CONDA_ENV" python -c \
        "import mineru, importlib.metadata as m; print('mineru', m.version('mineru'))"
    conda run -n "$CONDA_ENV" python -c \
        "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

    if [[ "$SKIP_MODEL" -eq 1 ]]; then
        warn "--skip-model：跳过模型下载"
    elif [[ -f "$HOME/mineru.json" ]]; then
        log "检测到 ~/mineru.json，模型权重为用户级共享缓存，跳过下载"
    else
        local mtype="pipeline"
        [[ "$WITH_VLM" -eq 1 ]] && mtype="all"
        log "下载 MinerU ${mtype} 模型（源: ${MODEL_SOURCE}）..."
        conda run -n "$CONDA_ENV" \
            mineru-models-download -m "$mtype" -s "$MODEL_SOURCE"
    fi

    echo -e "${GREEN}"
    echo "✔ MinerU 环境就绪：conda run -n ${CONDA_ENV} mineru --help"
    echo -e "${NC}"
}

main
