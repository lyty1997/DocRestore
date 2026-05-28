#!/usr/bin/env bash
# DocRestore 启动脚本
# 用法：./scripts/start.sh [backend|frontend|all|ppocr-server|-h|--help]
# 详细说明请运行 `./scripts/start.sh --help`

set -euo pipefail

usage() {
    cat <<'EOF'
DocRestore 启动脚本

用法:
  ./scripts/start.sh [MODE]

模式 (MODE):
  all            (默认) 同时启动后端 + 前端，启动后会等后端就绪再拉前端
  backend        仅启动后端 (uvicorn + docrestore.api.app)
  frontend       仅启动前端 (Vite dev server)
  ppocr-server   仅启动 PaddleOCR genai_server (vLLM 后端)
  -h, --help     显示本帮助

环境变量 (可在命令前导出覆盖默认值):
  后端:
    BACKEND_HOST   后端监听地址 (默认 0.0.0.0)
    BACKEND_PORT   后端监听端口 (默认 8000)
  前端:
    FRONTEND_PORT  Vite dev server 端口 (默认 5173)
  PaddleOCR server:
    PPOCR_GPU_ID   绑定 GPU 编号；留空则不导出 CUDA_VISIBLE_DEVICES，
                   由 vLLM 自动枚举 + docrestore 内部按显存挑卡 (默认 留空)
    PPOCR_PORT     genai_server 端口 (默认 8119)
    PPOCR_MODEL    模型名 (默认 PaddleOCR-VL-1.5-0.9B)

示例:
  ./scripts/start.sh                                 # 后端 + 前端
  BACKEND_PORT=8080 ./scripts/start.sh backend       # 后端改 8080
  PPOCR_GPU_ID=1 ./scripts/start.sh ppocr-server     # 绑 GPU 1 启 OCR server
  FRONTEND_PORT=3000 ./scripts/start.sh frontend     # 前端改 3000

退出:
  Ctrl+C 触发优雅关闭 (SIGTERM → 20s 等待 → SIGKILL 兜底)
  连按两次 Ctrl+C 立即强杀所有子进程
EOF
}

# help 分支必须在 trap cleanup 之前处理，否则退出时会触发 cleanup 打印
# "正在关闭服务..." 干扰输出
case "${1:-}" in
    -h|--help|help)
        usage
        exit 0
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 默认配置（可通过环境变量覆盖）
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# PaddleOCR server 配置
# PPOCR_GPU_ID 未设置 → 不导出 CUDA_VISIBLE_DEVICES，vLLM 自动枚举系统 GPU；
# 后端启动路径下由 docrestore.ocr.gpu_detect.pick_best_gpu 统一挑显存最大的一张。
PPOCR_GPU_ID="${PPOCR_GPU_ID:-}"
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
        # 子进程都用 setsid 启动（各自 session/pgid leader），用 kill -PGID
        # 把整个组一起 TERM。否则 `kill $pid` 只到 npm exec 那层，它派生的
        # `sh -c vite → node vite` 孙子会变孤儿继续占端口。
        local pid
        for pid in $pids; do
            kill -TERM "-${pid}" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        done
        # 最多等 20s（uvicorn lifespan 里 ppocr-server + vLLM 清理较慢，
        # OCR engine_manager.shutdown + pipeline.shutdown 串行要 10~15s）
        local waited=0
        while [ "$waited" -lt 40 ]; do
            # shellcheck disable=SC2086
            if ! kill -0 $pids 2>/dev/null; then
                break
            fi
            sleep 0.5
            waited=$((waited + 1))
        done
        # SIGKILL 兜底：防止 lifespan 卡在某个 await 不退，对整组发 -9
        # shellcheck disable=SC2086
        if kill -0 $pids 2>/dev/null; then
            log "子进程未在 20s 内退出，升级到 SIGKILL"
            for pid in $pids; do
                kill -KILL "-${pid}" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
            done
        fi
    fi
    wait 2>/dev/null || true
    log "已关闭"
}
trap cleanup EXIT INT TERM HUP

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

    # setsid 让 uvicorn 独立 session：SSH 断线 / VS Code 终端关闭时内核
    # 对 session leader 广播 SIGHUP 不再波及 uvicorn。start.sh 自己已 trap
    # HUP 走 cleanup 优雅关闭链（SIGTERM → 10s → SIGKILL 兜底），避免
    # uvicorn 被硬杀导致 ppocr-server / vLLM 孤儿进程残留。
    setsid python -m uvicorn \
        docrestore.api.app:create_app \
        --factory \
        --host "$BACKEND_HOST" \
        --port "$BACKEND_PORT" \
        --log-level info </dev/null &
}

wait_for_backend() {
    # 等后端 bind 8000 + lifespan 跑完（engine_manager/DB/ppocr 预热调度
    # 等等）。没这个守护，vite proxy 会在前端首屏命中 ECONNREFUSED
    # 窗口（uvicorn 启动耗时 2~4s，Vite 200ms 就 ready）。
    local url="http://127.0.0.1:${BACKEND_PORT}/api/v1/ocr/status"
    local timeout_s=30
    local waited=0
    log "等待后端就绪 (最多 ${timeout_s}s)..."
    while [ "$waited" -lt $((timeout_s * 2)) ]; do
        if curl -sf --max-time 1 --noproxy 127.0.0.1 "$url" \
            >/dev/null 2>&1; then
            log "后端已就绪"
            return 0
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    err "后端 ${timeout_s}s 内未响应，继续启动前端（可能有短暂 proxy error）"
    return 0  # 不阻断，让用户仍能看到前端页面
}

start_frontend() {
    log "启动前端 → http://localhost:${FRONTEND_PORT}"

    cd "$PROJECT_ROOT/frontend"

    # 检查 node_modules
    if [ ! -d "node_modules" ]; then
        log "安装前端依赖..."
        npm install
    fi

    # 同理用 setsid 隔离 session，防 SSH 断开时 vite 被 SIGHUP 硬杀
    setsid npx vite --port "$FRONTEND_PORT" </dev/null &
}

start_ppocr_server() {
    local gpu_display="${PPOCR_GPU_ID:-auto}"
    log "启动 PaddleOCR server → GPU ${gpu_display}, 端口 ${PPOCR_PORT}"

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

    # 未显式指定 PPOCR_GPU_ID 时不设 CUDA_VISIBLE_DEVICES，vLLM 自行探测所有 GPU；
    # 避免"被 hard code 指向不存在的设备导致 NVMLError_InvalidArgument"。
    # setsid 隔离 session（同 start_backend / start_frontend 注释）。
    if [ -n "$PPOCR_GPU_ID" ]; then
        CUDA_DEVICE_ORDER=PCI_BUS_ID \
        CUDA_VISIBLE_DEVICES="$PPOCR_GPU_ID" \
        setsid paddleocr genai_server \
            --model_name "$PPOCR_MODEL" \
            --backend vllm \
            --port "$PPOCR_PORT" </dev/null &
    else
        setsid paddleocr genai_server \
            --model_name "$PPOCR_MODEL" \
            --backend vllm \
            --port "$PPOCR_PORT" </dev/null &
    fi
}

# 解析参数（-h/--help 已在 trap 安装前处理）
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
        wait_for_backend
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
        log "  GPU:  ${PPOCR_GPU_ID:-auto（vLLM 自选）}"
        log "  模型: ${PPOCR_MODEL}"
        log "  按 Ctrl+C 停止"
        ;;
    *)
        err "未知参数: $MODE"
        echo ""
        usage >&2
        exit 1
        ;;
esac

# 等待子进程（Ctrl+C 触发 cleanup）
wait
