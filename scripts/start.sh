#!/usr/bin/env bash
# DocRestore 启动脚本
# 用法：
#   ./scripts/start.sh           # 同时启动后端 + 前端
#   ./scripts/start.sh backend   # 仅启动后端
#   ./scripts/start.sh frontend  # 仅启动前端

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 默认配置（可通过环境变量覆盖）
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[DocRestore]${NC} $*"; }
err() { echo -e "${RED}[错误]${NC} $*" >&2; }

_CLEANING_UP=0
cleanup() {
    # 重入保护：防止 INT/TERM/EXIT 多次触发
    if [ "$_CLEANING_UP" -eq 1 ]; then
        return
    fi
    _CLEANING_UP=1

    # 收到信号后先移除 trap，避免 kill 产生的后续信号再次进入
    trap - EXIT INT TERM

    log "正在关闭服务..."
    # 向子进程发送 TERM（不含自身）
    jobs -p | xargs -r kill 2>/dev/null || true
    wait 2>/dev/null || true
    log "已关闭"
}
trap cleanup EXIT INT TERM

start_backend() {
    log "启动后端 → http://${BACKEND_HOST}:${BACKEND_PORT}"

    cd "$PROJECT_ROOT"

    # 检查虚拟环境
    if [ -d ".venv" ]; then
        # shellcheck disable=SC1091
        source .venv/bin/activate
    fi

    # 检查依赖
    if ! python -c "import docrestore" 2>/dev/null; then
        err "docrestore 未安装，请先运行 scripts/setup.sh"
        return 1
    fi

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
    *)
        err "未知参数: $MODE"
        echo "用法: $0 [backend|frontend|all]"
        exit 1
        ;;
esac

# 等待子进程（Ctrl+C 触发 cleanup）
wait
