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

"""OCR 引擎生命周期管理器

按需切换引擎（PaddleOCR ↔ DeepSeek-OCR-2），自动管理 ppocr-server 进程。
同一时刻只有一个引擎在 GPU 上，切换时释放旧引擎资源。
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from types import FrameType

from docrestore.ocr.base import OCREngine, _drain_stream_to_logger
from docrestore.ocr.router import _parse_model, create_engine
from docrestore.pipeline.config import OCRConfig

logger = logging.getLogger(__name__)

# 进度回调：message → 前端展示
ProgressFn = Callable[[str], None]


# ─────────────────────────────────────────────────────────────
# 进程级兜底清理（atexit / signal / PDEATHSIG / 启动扫描）
# ─────────────────────────────────────────────────────────────
# ppocr-server 是独立 session leader（start_new_session=True），
# vLLM EngineCore 是它的子进程，属于同一进程组。只要 killpg 到整个
# 进程组，vLLM 就会被带走。
#
# 清理路径分四层，依次覆盖不同退出场景：
#   A. atexit —— uvicorn force-quit / sys.exit 等 Python 主线程结束
#   B. PDEATHSIG(SIGKILL) —— docrestore 被 kill -9（内核自动杀 ppocr-server）
#   C. SIGHUP handler —— 终端关闭 / SSH 断开
#   D. startup scan —— 上次遗留（kill -9 之后 vLLM 孤儿）清理
# 最残存的盲区仅有：docrestore 被 SIGKILL 导致 PDEATHSIG 生效但 vLLM
# 作为 ppocr-server 子进程未继承 PDEATHSIG —— 靠 D 下次启动时兜底。

_atexit_callbacks: dict[int, Callable[[], None]] = {}
_tracked_pgids: set[int] = set()
_signal_handlers_installed = False
_original_sighup_handler: (
    Callable[[int, FrameType | None], None] | int | None
) = None


def _kill_pgid_sync(pgid: int, grace_seconds: float = 3.0) -> None:
    """同步清理进程组：SIGTERM → 最长 grace_seconds → SIGKILL。

    用于 atexit / signal handler 等同步上下文（不能 await）。
    所有 OSError 静默吞掉，因为进程退出路径上 logging 可能已失效。
    """
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)  # 探测进程组是否还存活
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.1)

    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(pgid, signal.SIGKILL)


def _prctl_set_pdeathsig() -> None:
    """preexec_fn：设置 PR_SET_PDEATHSIG=SIGKILL。

    fork 之后 exec 之前运行于子进程。Linux only；其他平台或 libc 加载
    失败都静默跳过，绝不阻止子进程启动（失败也不影响主流程，仅失去
    kill -9 兜底能力）。
    """
    if sys.platform != "linux":
        return
    try:
        import ctypes
        pr_set_pdeathsig = 1
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(pr_set_pdeathsig, signal.SIGKILL, 0, 0, 0)
    except Exception:  # noqa: BLE001, S110 — fork 后 logging 可能死锁，只能静默
        pass


def _sighup_handler(
    signum: int, frame: FrameType | None,
) -> None:
    """SIGHUP handler：同步清理 pgid 后转发 SIGTERM 让 uvicorn 正常关闭。"""
    del signum, frame
    for pgid in list(_tracked_pgids):
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pgid, signal.SIGTERM)
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(os.getpid(), signal.SIGTERM)


def _install_signal_handlers() -> None:
    """安装进程级 SIGHUP handler（幂等）。

    SIGINT/SIGTERM 由 uvicorn 自己处理（走 lifespan shutdown + atexit 兜底），
    我们只接管 uvicorn 不管的 SIGHUP（终端关闭 / SSH 断开）。
    """
    global _signal_handlers_installed, _original_sighup_handler
    if _signal_handlers_installed:
        return
    _signal_handlers_installed = True

    try:
        _original_sighup_handler = signal.signal(
            signal.SIGHUP, _sighup_handler,
        )
    except (OSError, ValueError):
        # Windows 或非主线程：signal.signal 不可用
        logger.debug("SIGHUP handler 安装失败", exc_info=True)


def _track_pgid(pgid: int) -> None:
    """注册 pgid 到 atexit + signal 双重清理（幂等）。"""
    if pgid in _atexit_callbacks:
        return
    _tracked_pgids.add(pgid)

    def _cleanup() -> None:
        _kill_pgid_sync(pgid)

    atexit.register(_cleanup)
    _atexit_callbacks[pgid] = _cleanup
    _install_signal_handlers()


def _untrack_pgid(pgid: int) -> None:
    """从进程级清理机制中移除 pgid（正常 shutdown 时调用）。"""
    _tracked_pgids.discard(pgid)
    cb = _atexit_callbacks.pop(pgid, None)
    if cb is not None:
        atexit.unregister(cb)


def cleanup_stale_ppocr_servers() -> list[int]:
    """启动时扫描并清理残留的 paddleocr genai_server 进程。

    Linux only。遍历 /proc 找到 cmdline 含 `paddleocr` + `genai_server`
    的进程，按进程组 ID 去重后批量 killpg。返回被清理的 pgid 列表。
    """
    if sys.platform != "linux":
        return []

    try:
        proc_entries = os.listdir("/proc")
    except OSError:
        return []

    stale_pgids: set[int] = set()
    for entry in proc_entries:
        if not entry.isdigit():
            continue
        pgid = _extract_paddleocr_pgid(entry)
        if pgid is not None:
            stale_pgids.add(pgid)

    cleaned: list[int] = []
    for pgid in stale_pgids:
        logger.warning(
            "发现残留的 paddleocr genai_server 进程组 pgid=%d，清理中...",
            pgid,
        )
        _kill_pgid_sync(pgid)
        cleaned.append(pgid)

    return cleaned


def _extract_paddleocr_pgid(pid_str: str) -> int | None:
    """读取 /proc/{pid}/cmdline 和 stat，若为 paddleocr genai_server 返回 pgid。"""
    try:
        with open(f"/proc/{pid_str}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace",
            )
    except OSError:
        return None

    if "paddleocr" not in cmdline or "genai_server" not in cmdline:
        return None

    try:
        with open(f"/proc/{pid_str}/stat", encoding="utf-8") as f:
            stat = f.read()
    except OSError:
        return None

    # /proc/<pid>/stat 格式：pid (comm) state ppid pgid ...
    # comm 可能含空格/括号，按最后一个 ")" 切分后再取字段
    try:
        rparen = stat.rindex(")")
    except ValueError:
        return None
    fields = stat[rparen + 2:].split()
    if len(fields) < 3:
        return None
    try:
        return int(fields[2])  # 跳过 state / ppid，取 pgid
    except ValueError:
        return None


class EngineManager:
    """OCR 引擎生命周期管理器 — 按需切换，自动管理 ppocr-server"""

    def __init__(
        self,
        default_config: OCRConfig,
        gpu_lock: asyncio.Lock,
    ) -> None:
        self._default_config = default_config
        self._gpu_lock = gpu_lock
        self._engine: OCREngine | None = None
        self._current_model: str = ""
        self._current_gpu: str = ""
        self._ppocr_server_proc: asyncio.subprocess.Process | None = None
        # ppocr-server 的 stdout/stderr drain task（防 pipe buffer 写满）
        self._ppocr_drain_tasks: list[asyncio.Task[None]] = []
        self._switch_lock = asyncio.Lock()

    @property
    def current_model(self) -> str:
        """当前活跃的引擎模型标识符。"""
        return self._current_model

    @property
    def current_gpu(self) -> str:
        """当前活跃的 GPU ID。"""
        return self._current_gpu

    @property
    def is_ready(self) -> bool:
        """当前引擎是否已初始化就绪。"""
        return self._engine is not None and self._engine.is_ready

    @property
    def is_switching(self) -> bool:
        """是否正在切换引擎（switch_lock 被持有）。"""
        return self._switch_lock.locked()

    @property
    def engine(self) -> OCREngine | None:
        """当前引擎实例（可能为 None）。"""
        return self._engine

    async def ensure(
        self,
        ocr: OCRConfig | None = None,
        on_progress: ProgressFn | None = None,
    ) -> OCREngine:
        """确保引擎匹配请求的模型，必要时切换。

        切换时序：
        1. switch_lock 防止并发切换
        2. gpu_lock 等待当前 OCR 操作完成
        3. shutdown 旧引擎 + ppocr-server
        4. 创建新配置 + 启动 ppocr-server（如需要）
        5. 创建 + initialize 新引擎
        """
        config = ocr or self._default_config
        target_model = config.model
        target_gpu = config.gpu_id
        logger.debug(
            "ensure() model=%s, gpu=%s (override=%s)",
            target_model, target_gpu, ocr is not None,
        )

        def _progress(msg: str) -> None:
            if on_progress is not None:
                on_progress(msg)

        # 快速路径：引擎已匹配
        if self._is_matched(target_model, target_gpu):
            return self._engine  # type: ignore[return-value]

        async with self._switch_lock:
            # 双重检查（另一个协程可能刚完成切换）
            if self._is_matched(target_model, target_gpu):
                return self._engine  # type: ignore[return-value]

            self._log_switch_reason(target_model, target_gpu)

            # 获取 gpu_lock，等待当前 OCR 操作完成后再切换
            async with self._gpu_lock:
                try:
                    if self._current_model:
                        _progress("正在释放旧引擎资源...")
                    await self._shutdown_current()

                    provider, _ = _parse_model(target_model)

                    # PaddleOCR 需要先启动 ppocr-server
                    if provider == "paddle-ocr":
                        _progress("正在启动 OCR 推理服务...")
                        await self._start_ppocr_server(config, _progress)

                    # 创建并初始化引擎（Protocol 统一支持 on_progress）
                    _progress("正在初始化 OCR 引擎...")
                    engine = create_engine(target_model, config)
                    self._engine = engine
                    await engine.initialize(on_progress=on_progress)
                    self._current_model = target_model
                    self._current_gpu = target_gpu
                except BaseException:
                    # 任何异常（含 CancelledError）都清理半成品状态
                    logger.info("引擎切换失败，清理资源...")
                    await self._shutdown_current()
                    raise

            _progress("OCR 引擎就绪")
            logger.info("OCR 引擎切换完成: %s", target_model)
            return self._engine

    async def shutdown(self) -> None:
        """应用关闭时调用：释放引擎 + ppocr-server。"""
        async with self._switch_lock:
            await self._shutdown_current()
        logger.info("EngineManager 已关闭")

    def _is_matched(self, target_model: str, target_gpu: str) -> bool:
        """当前引擎的 model + gpu 是否匹配目标。"""
        return (
            self._engine is not None
            and self._current_model == target_model
            and self._current_gpu == target_gpu
        )

    def _log_switch_reason(
        self, target_model: str, target_gpu: str,
    ) -> None:
        """记录引擎切换原因（模型变化 / GPU 变化）。"""
        parts: list[str] = []
        if self._current_model != target_model:
            parts.append(
                f"模型 {self._current_model or '(无)'} → {target_model}"
            )
        if self._current_gpu != target_gpu:
            parts.append(
                f"GPU {self._current_gpu or '(无)'} → {target_gpu}"
            )
        logger.info("切换 OCR 引擎: %s", ", ".join(parts))

    async def _shutdown_current(self) -> None:
        """关闭当前引擎和 ppocr-server。

        try/finally 保证 _stop_ppocr_server 无论 engine.shutdown 成功/失败/
        被 cancel 都会被调用 — ppocr-server 是独立 session leader，
        不清理会遗留孤儿进程（含 vLLM EngineCore 子进程）。
        """
        try:
            if self._engine is not None:
                try:
                    await self._engine.shutdown()
                except Exception:
                    logger.warning("引擎 shutdown 异常", exc_info=True)
                finally:
                    self._engine = None
        finally:
            try:
                await self._stop_ppocr_server()
            finally:
                self._current_model = ""
                self._current_gpu = ""

    async def _start_ppocr_server(
        self,
        config: OCRConfig,
        on_progress: ProgressFn | None = None,
    ) -> None:
        """自动启动 ppocr genai_server 子进程。"""
        python_path = config.paddle_server_python
        if not python_path:
            logger.warning(
                "未配置 paddle_server_python，跳过 ppocr-server 自动启动。"
                "PaddleOCR 将以本地模式运行（worker 内加载模型）。"
            )
            return

        python_exists = await asyncio.to_thread(
            lambda: os.path.exists(python_path)
        )
        if not python_exists:
            logger.warning(
                "ppocr_vlm python 不存在: %s，跳过 server 启动",
                python_path,
            )
            return

        port = config.paddle_server_port
        gpu_id = config.gpu_id
        model_name = config.paddle_server_model_name
        backend_config_path = config.paddle_server_backend_config

        logger.info(
            "启动 ppocr-server: port=%d, gpu=%s, model=%s%s",
            port, gpu_id, model_name,
            f", backend_config={backend_config_path}"
            if backend_config_path else "",
        )

        env = {**os.environ}
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["CUDA_VISIBLE_DEVICES"] = gpu_id

        argv: list[str] = [
            python_path, "-m", "paddleocr", "genai_server",
            "--model_name", model_name,
            "--backend", "vllm",
            "--port", str(port),
        ]
        # 可选 backend_config YAML：内容由 paddlex 解析为 vLLM CLI 参数
        if backend_config_path:
            argv.extend(["--backend_config", backend_config_path])

        self._ppocr_server_proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,  # 独立进程组，方便 killpg 清理子进程
            preexec_fn=_prctl_set_pdeathsig,  # 父进程死则子进程被内核 SIGKILL
        )
        # start_new_session=True 保证 pid == pgid，登记到进程级兜底清理。
        _track_pgid(self._ppocr_server_proc.pid)

        # 等待 server 就绪，超时/异常/取消时清理进程
        timeout = config.paddle_server_startup_timeout
        try:
            await self._wait_server_ready(
                port,
                timeout,
                self._ppocr_server_proc,
                on_progress,
                connect_timeout=config.paddle_server_connect_timeout,
                poll_interval=config.paddle_server_poll_interval,
            )
        except (TimeoutError, RuntimeError):
            await self._stop_ppocr_server()
            raise
        except asyncio.CancelledError:
            logger.info("ppocr-server 启动被取消，正在清理...")
            await self._stop_ppocr_server()
            raise

        # 自动配置 paddle_server_url（如果尚未设置）
        if not config.paddle_server_url:
            config.paddle_server_url = (
                config.build_default_paddle_server_url()
            )
            logger.info(
                "自动配置 paddle_server_url: %s",
                config.paddle_server_url,
            )

        logger.info("ppocr-server 已就绪 (port=%d)", port)

        # server ready 之后挂起 stdout/stderr drain：否则日志累积把 64KB pipe
        # buffer 写满，server 内任何 logging 阻塞在 pipe_write → HTTP 响应
        # 全部卡死 → worker OCR 300s 超时循环。_wait_server_ready 期间由
        # _collect_stderr_progress 读 stderr 提取启动进度，此时已结束，不冲突。
        proc = self._ppocr_server_proc
        if proc.stdout is not None:
            self._ppocr_drain_tasks.append(
                asyncio.create_task(
                    _drain_stream_to_logger(
                        proc.stdout, "[ppocr-server stdout]",
                    ),
                    name="ppocr-server-stdout-drain",
                ),
            )
        if proc.stderr is not None:
            self._ppocr_drain_tasks.append(
                asyncio.create_task(
                    _drain_stream_to_logger(
                        proc.stderr, "[ppocr-server stderr]",
                    ),
                    name="ppocr-server-stderr-drain",
                ),
            )

    async def _stop_ppocr_server(self) -> None:
        """关闭 ppocr-server 整个进程组（含 vLLM EngineCore 子进程）。

        vLLM 会 fork 出 EngineCore_DP0 等子进程占用 GPU 显存，
        仅 kill 主进程会留下孤儿进程导致后续 CUDA OOM。
        通过 start_new_session=True + os.killpg() 清理整个进程树。
        """
        if self._ppocr_server_proc is None:
            return

        pid = self._ppocr_server_proc.pid
        # 正常路径清理走起：先从兜底名单移除，避免 atexit 重复 kill
        _untrack_pgid(pid)
        logger.info("关闭 ppocr-server 进程组 (pid=%s)...", pid)

        # 先 cancel drain tasks：进程被 killpg 后 stdout/stderr EOF，
        # drain 会自然退出；cancel 是兜底，避免卡在 readline
        for task in self._ppocr_drain_tasks:
            task.cancel()
        if self._ppocr_drain_tasks:
            await asyncio.gather(
                *self._ppocr_drain_tasks, return_exceptions=True,
            )
            self._ppocr_drain_tasks = []
        try:
            # 向整个进程组发送 SIGTERM
            os.killpg(pid, signal.SIGTERM)
            await asyncio.wait_for(
                self._ppocr_server_proc.wait(),
                timeout=self._default_config.paddle_server_shutdown_timeout,
            )
        except TimeoutError:
            # vLLM 加载阶段可能不响应 SIGTERM，升级到 SIGKILL
            logger.info("ppocr-server 进程组未响应 SIGTERM，发送 SIGKILL")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            await self._ppocr_server_proc.wait()
        except ProcessLookupError:
            logger.debug("ppocr-server 进程组已退出")
        except OSError:
            logger.debug("关闭 ppocr-server 进程组异常", exc_info=True)
        finally:
            self._ppocr_server_proc = None

        logger.info("ppocr-server 进程组已关闭")

    @staticmethod
    def _extract_stderr_message(line: str) -> str | None:
        """从 ppocr-server stderr 行中提取用户可读的进度信息。

        返回 None 表示该行不值得展示给用户。
        """
        # 模型权重加载进度：Loading safetensors checkpoint shards:  50% ...
        m = re.search(r"Loading.*shards:\s+(\d+%)", line)
        if m:
            return f"加载模型权重... {m.group(1)}"

        # 使用缓存模型
        if "Using cached" in line or "already exist" in line:
            return "模型文件已缓存，跳过下载"

        # 网络检查
        if "Checking connectivity" in line:
            return "检查模型源连通性..."

        # vLLM 引擎初始化相关
        if "EngineCore" in line and "pid=" in line:
            return "vLLM 推理引擎初始化中..."

        # 网络不可用（模型未本地缓存时可能出现）
        if "No model hoster is available" in line:
            return "模型源不可用，请检查网络连接"

        return None

    @staticmethod
    async def _read_stderr_lines(
        proc: asyncio.subprocess.Process,
    ) -> list[str]:
        """非阻塞读取 stderr 中当前可用的所有行。"""
        lines: list[str] = []
        if proc.stderr is None:
            return lines
        while True:
            try:
                raw = await asyncio.wait_for(
                    proc.stderr.readline(), timeout=0.1,
                )
            except (TimeoutError, OSError, asyncio.IncompleteReadError):
                break
            if not raw:
                break
            lines.append(raw.decode("utf-8", errors="replace").rstrip())
        return lines

    @staticmethod
    async def _collect_stderr_progress(
        proc: asyncio.subprocess.Process,
        stderr_buf: list[str],
        on_progress: ProgressFn | None,
    ) -> None:
        """读取 stderr 新行，提取进度信息推送给前端。"""
        new_lines = await EngineManager._read_stderr_lines(proc)
        stderr_buf.extend(new_lines)
        if on_progress is None:
            return
        for line in new_lines:
            msg = EngineManager._extract_stderr_message(line)
            if msg is not None:
                on_progress(msg)

    @staticmethod
    def _check_process_alive(
        proc: asyncio.subprocess.Process,
        stderr_buf: list[str],
    ) -> None:
        """检查子进程是否仍在运行，已退出则抛出 RuntimeError。"""
        if proc.returncode is None:
            return
        msg = f"ppocr-server 进程已退出（exit code {proc.returncode}）"
        if stderr_buf:
            msg += "\nstderr:\n" + "\n".join(stderr_buf[-20:])
        logger.error(msg)
        raise RuntimeError(msg)

    @staticmethod
    async def _wait_server_ready(
        port: int,
        startup_timeout: int,
        proc: asyncio.subprocess.Process,
        on_progress: ProgressFn | None = None,
        *,
        connect_timeout: float = 2.0,
        poll_interval: float = 2.0,
    ) -> None:
        """轮询 TCP 端口直到 ppocr-server 就绪。

        同时监控子进程状态和 stderr 输出：
        - 进程退出 → 立即报错
        - stderr 中的关键信息 → 通过 on_progress 推送给前端
        """
        stderr_buf: list[str] = []
        deadline = asyncio.get_event_loop().time() + startup_timeout
        attempt = 0

        while asyncio.get_event_loop().time() < deadline:
            await EngineManager._collect_stderr_progress(
                proc, stderr_buf, on_progress,
            )
            EngineManager._check_process_alive(proc, stderr_buf)

            attempt += 1
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port),
                    timeout=connect_timeout,
                )
                writer.close()
                await writer.wait_closed()
                logger.info(
                    "ppocr-server 端口 %d 可达（第 %d 次尝试）",
                    port, attempt,
                )
                return
            except (OSError, TimeoutError):
                if attempt % 5 == 0:
                    elapsed = startup_timeout - (
                        deadline - asyncio.get_event_loop().time()
                    )
                    if on_progress is not None:
                        on_progress(
                            f"等待推理服务就绪... ({int(elapsed)}s)"
                        )
                    logger.info(
                        "等待 ppocr-server 就绪... (第 %d 次尝试)",
                        attempt,
                    )
                await asyncio.sleep(poll_interval)

        # 超时
        msg = f"ppocr-server 启动超时（{startup_timeout}s，端口 {port}）"
        if stderr_buf:
            msg += "\nstderr:\n" + "\n".join(stderr_buf[-20:])
        logger.error(msg)
        raise TimeoutError(msg)
