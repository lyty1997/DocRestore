#!/usr/bin/env bash
# DocRestore 启动脚本
# 用法：
#   ./scripts/start.sh                # 同时启动后端 + 前端
#   ./scripts/start.sh backend        # 仅启动后端
#   ./scripts/start.sh frontend       # 仅启动前端
#   ./scripts/start.sh ppocr-server   # 启动 PaddleOCR genai_server

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 默认配置（可通过环境变量覆盖）
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# PaddleOCR server 配置
PPOCR_GPU_ID="${PPOCR_GPU_ID:-1}"
PPOCR_PORT="${PPOCR_PORT:-8119}"
PPOCR_MODEL="${PPOCR_MODEL:-PaddleOCR-VL-1.5-0.9B}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[DocRestore]${NC} $*"; }
err() { echo -e "${RED}[错误]${NC} $*" >&2; }

_CLEANING_UP=0
# 第二次 Ctrl+C 直接升级到 SIGKILL（用户意图是马上退，别再等）
_force_kill() {
    log "收到第二次中断，强杀所有子进程"
    # shellcheck disable=SC2046
    kill -9 $(jobs -p) 2>/dev/null || true
    exit 130
}

cleanup() {
    # 重入保护：防止 INT/TERM/EXIT 多次触发
    if [ "$_CLEANING_UP" -eq 1 ]; then
        return
    fi
    _CLEANING_UP=1

    # 收到信号后把 INT 改接到强杀分支，EXIT/TERM 摘掉避免重入
    trap - EXIT TERM
    trap _force_kill INT

    log "正在关闭服务... (再按一次 Ctrl+C 强制退出)"
    local pids
    pids="$(jobs -p)"
    if [ -n "$pids" ]; then
        # 先给 SIGTERM，优雅退出
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        # 最多等 10s（uvicorn lifespan 里 ppocr-server + vLLM 清理需要时间）
        local waited=0
        while [ "$waited" -lt 20 ]; do
            # shellcheck disable=SC2086
            if ! kill -0 $pids 2>/dev/null; then
                break
            fi
            sleep 0.5
            waited=$((waited + 1))
        done
        # SIGKILL 兜底：防止 lifespan 卡在某个 await 不退
        # shellcheck disable=SC2086
        if kill -0 $pids 2>/dev/null; then
            log "子进程未在 10s 内退出，升级到 SIGKILL"
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
        fi
    fi
    wait 2>/dev/null || true
    log "已关闭"
}
trap cleanup EXIT INT TERM

start_backend() {
    log "启动后端 → http://${BACKEND_HOST}:${BACKEND_PORT}"

    cd "$PROJECT_ROOT"

    # 激活 conda 环境（后端是轻量协调器，不依赖 torch/vllm/paddle）
    # 优先使用专用 docrestore 环境，回退到 OCR 环境（兼容旧部署）
    if ! command -v conda &>/dev/null; then
        err "未找到 conda，请先安装 Miniconda/Anaconda"
        return 1
    fi
    local conda_base
    conda_base=$(conda info --base 2>/dev/null)
    # shellcheck source=/dev/null
    source "$conda_base/etc/profile.d/conda.sh"

    local backend_env=""
    for candidate in docrestore ppocr_client deepseek_ocr; do
        if conda env list 2>/dev/null | grep -q "^${candidate} "; then
            if conda run -n "$candidate" python -c "import docrestore" 2>/dev/null; then
                backend_env="$candidate"
                break
            fi
        fi
    done

    if [[ -z "$backend_env" ]]; then
        err "未找到安装了 docrestore 的 conda 环境"
        err "请先运行 scripts/setup_backend.sh"
        return 1
    fi

    log "使用 conda 环境: ${backend_env}"
    conda activate "$backend_env"

    python -m uvicorn \
        docrestore.api.app:create_app \
        --factory \
        --host "$BACKEND_HOST" \
        --port "$BACKEND_PORT" \
        --log-level info &
}

start_frontend() {
    log "启动前端 → http://localhost:${FRONTEND_PORT}"

    cd "$PROJECT_ROOT/frontend"

    # 检查 node_modules
    if [ ! -d "node_modules" ]; then
        log "安装前端依赖..."
        npm install
    fi

    npx vite --port "$FRONTEND_PORT" &
}

start_ppocr_server() {
    log "启动 PaddleOCR server → GPU ${PPOCR_GPU_ID}, 端口 ${PPOCR_PORT}"

    # 检查 conda
    if ! command -v conda &>/dev/null; then
        err "未找到 conda，请先安装 Miniconda/Anaconda"
        return 1
    fi

    # 检查 ppocr_vlm 环境
    if ! conda env list 2>/dev/null | grep -q "^ppocr_vlm "; then
        err "ppocr_vlm conda 环境不存在，请先运行: bash scripts/setup_paddle_ocr.sh"
        return 1
    fi

    # 初始化 conda shell hook
    local conda_base
    conda_base=$(conda info --base 2>/dev/null)
    # shellcheck source=/dev/null
    source "$conda_base/etc/profile.d/conda.sh"

    # cuda-nvcc 激活脚本引用未绑定变量，临时关闭 set -u
    set +u
    conda activate ppocr_vlm
    set -u

    CUDA_DEVICE_ORDER=PCI_BUS_ID \
    CUDA_VISIBLE_DEVICES="$PPOCR_GPU_ID" \
    paddleocr genai_server \
        --model_name "$PPOCR_MODEL" \
        --backend vllm \
        --port "$PPOCR_PORT" &
}

# 解析参数
MODE="${1:-all}"

case "$MODE" in
    backend)
        start_backend
        ;;
    frontend)
        start_frontend
        ;;
    all)
        start_backend
        start_frontend
        echo ""
        log "${GREEN}服务已启动${NC}"
        log "  后端 API:  http://${BACKEND_HOST}:${BACKEND_PORT}/api/v1"
        log "  前端页面:  http://localhost:${FRONTEND_PORT}"
        log "  按 Ctrl+C 停止所有服务"
        ;;
    ppocr-server)
        start_ppocr_server
        echo ""
        log "${GREEN}PaddleOCR server 已启动${NC}"
        log "  端口: ${PPOCR_PORT}"
        log "  GPU:  ${PPOCR_GPU_ID}"
        log "  模型: ${PPOCR_MODEL}"
        log "  按 Ctrl+C 停止"
        ;;
    *)
        err "未知参数: $MODE"
        echo "用法: $0 [backend|frontend|all|ppocr-server]"
        exit 1
        ;;
esac

# 等待子进程（Ctrl+C 触发 cleanup）
wait
