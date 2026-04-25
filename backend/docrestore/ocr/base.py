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

"""OCR 引擎 Protocol 与 subprocess 基类。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, Protocol

from docrestore.models import PageOCR
from docrestore.pipeline.config import OCRConfig

logger = logging.getLogger(__name__)

# 模型加载/初始化进度回调
ProgressFn = Callable[[str], None]

# OCR 引擎与 Pipeline 共享的契约文件名
# 说明：worker 脚本运行在独立 conda 环境里无法 import 本模块，因此
# scripts/*_ocr_worker.py 中同名字面量需要手工同步。
OCR_RESULT_FILENAME = "result.mmd"
OCR_RAW_RESULT_FILENAME = "result_ori.mmd"
OCR_DEBUG_COORDS_FILENAME = "debug_coords.jsonl"


async def _drain_stream_to_logger(
    stream: asyncio.StreamReader,
    log_prefix: str,
    *,
    tail: deque[str] | None = None,
) -> None:
    """持续消费 stream，避免 64KB pipe buffer 写满后子进程阻塞在 write()。

    - 按行读取 → debug 级别转发到 logger
    - 可选写入 tail（deque maxlen 自动丢头），供进程退出后取最后 N 行诊断
    - EOF（readline 返回空 bytes）正常退出；CancelledError 透传
    """
    try:
        while True:
            raw = await stream.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip()
            if tail is not None:
                tail.append(line)
            logger.debug("%s %s", log_prefix, line)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("%s drain 异常", log_prefix, exc_info=True)


class OCREngine(Protocol):
    """OCR 引擎接口。"""

    async def initialize(
        self, on_progress: ProgressFn | None = None,
    ) -> None:
        """加载模型到 GPU。on_progress 用于推送长耗时初始化的分阶段进度。"""
        ...

    async def ocr(
        self, image_path: Path, output_dir: Path
    ) -> PageOCR:
        """单张 OCR，结果写入 output_dir/{image_stem}_OCR/"""
        ...

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()，每完成一张回调 on_progress(current, total)"""
        ...

    async def shutdown(self) -> None:
        """释放 GPU 资源"""
        ...

    @property
    def is_ready(self) -> bool:
        """引擎是否就绪"""
        ...


class WorkerBackedOCREngine(ABC):
    """通过 subprocess 调用独立 conda 环境的 OCR 引擎基类。

    吸收两类 worker 引擎的共用流程：
    - worker 脚本定位、subprocess 启动（16MB stdio 缓冲）
    - JSON Lines 命令往返（stdin 写命令，stdout 读响应）
    - 协议失步标志（被 cancel 时残留响应）与重启恢复
    - 批量 ocr、已有结果加载、shutdown 骨架

    子类必须实现：
    - 类属性 engine_name / worker_script_path
    - _get_python_path / _get_timeout
    - _build_subprocess_env / _build_init_cmd
    - _terminate_process（terminate / killpg）
    - ocr（单张处理，逻辑各不相同）

    子类可选覆盖：
    - _subprocess_extra_kwargs（start_new_session 等）
    - _send_init_command（初始化期间推送进度）
    - _read_response（跳过非 JSON 行等自定义解析）
    - _restart_worker（重置计数器等）
    """

    engine_name: str = ""
    worker_script_path: str = ""

    def __init__(self, config: OCRConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._ready = False
        # JSON Lines 命令序列号。每次 `_send_command` 前自增；worker 在响应里
        # 回显该 seq 以便协议失步时定位、丢弃旧响应。
        self._seq: int = 0
        # 前一条命令被 cancel 等中断，响应还在 worker stdout 缓冲区里未消费。
        # 下次 `_send_command` 前先走 `_resync_if_needed` 按 `_pending_seq`
        # 把残留响应 drain 掉；drain 超时才回退 `_restart_worker`。
        self._pending_resync: bool = False
        self._pending_seq: int | None = None
        # worker stderr drain 任务 + 最近 N 行缓冲（供 _raise_worker_exited 取）
        self._stderr_drain_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=200)

    @property
    def is_ready(self) -> bool:
        """引擎是否就绪。"""
        return self._ready

    # ── 子类必须实现 ──────────────────────────────────────

    @abstractmethod
    def _get_python_path(self) -> str:
        """返回 worker 子进程使用的 python 路径（来自 OCRConfig）。"""

    @abstractmethod
    def _get_timeout(self) -> int:
        """返回 _send_command 读取响应的超时秒数（来自 OCRConfig）。"""

    @abstractmethod
    def _build_subprocess_env(self) -> dict[str, str]:
        """构造 worker subprocess 的环境变量（含 CUDA 相关）。"""

    @abstractmethod
    def _build_init_cmd(self) -> dict[str, object]:
        """构造发送给 worker 的 initialize 命令负载。"""

    @abstractmethod
    async def _terminate_process(self) -> None:
        """终止 worker 进程（terminate / killpg 由子类选择）。

        调用时 self._process 已非 None。
        """

    @abstractmethod
    async def ocr(
        self, image_path: Path, output_dir: Path
    ) -> PageOCR:
        """单张 OCR（子类实现响应解析与侧栏/裁剪等特有逻辑）。"""

    # ── 子类可选覆盖 ──────────────────────────────────────

    def _start_new_session(self) -> bool:
        """是否让 worker 运行在独立进程组（便于 killpg 清理子进程树）。"""
        return False

    async def _send_init_command(
        self,
        init_cmd: dict[str, object],
        on_progress: ProgressFn | None,
    ) -> dict[str, object]:
        """发送初始化命令。默认直接走 _send_command；子类可覆盖以推送进度。"""
        del on_progress  # 默认实现忽略
        return await self._send_command(init_cmd)

    async def _read_response(
        self,
        raw: bytes,
        stdout: asyncio.StreamReader,
        read_timeout: int,
    ) -> dict[str, object]:
        """从 worker stdout 解析 JSON 响应。默认直接 json.loads。"""
        del stdout, read_timeout  # 默认实现不读后续行
        result: dict[str, object] = json.loads(raw.decode("utf-8"))
        return result

    # ── 通用骨架 ──────────────────────────────────────────

    def _resolve_worker_script(self) -> str:
        """返回 worker 脚本路径（子类可覆盖以读取 OCRConfig 字段）。"""
        return self.worker_script_path

    def _find_worker_script(self) -> Path:
        """定位 worker 脚本。

        - 子类通过 _resolve_worker_script 返回路径；
        - 绝对路径直接校验存在即返回；
        - 相对路径沿 backend 模块所在目录向上查找。
        """
        configured = self._resolve_worker_script()
        if not configured:
            msg = f"未配置 {self.engine_name} worker 脚本路径"
            raise ValueError(msg)

        path = Path(configured)
        if path.is_absolute():
            if path.exists():
                return path
            msg = (
                f"{self.engine_name} worker 脚本不存在: {path}"
            )
            raise FileNotFoundError(msg)

        current = Path(__file__).resolve().parent
        for parent in (current, *current.parents):
            candidate = parent / path
            if candidate.exists():
                return candidate

        msg = (
            f"找不到 {self.engine_name} worker 脚本: "
            f"{configured}\n"
            "请确认项目结构完整，或通过 OCRConfig 显式配置绝对路径。"
        )
        raise FileNotFoundError(msg)

    async def _start_worker_process(self) -> None:
        """启动 worker subprocess（通用逻辑）。"""
        python_path = self._get_python_path()
        if not python_path:
            msg = f"未配置 {self.engine_name} python 路径"
            raise ValueError(msg)

        python_exists = await asyncio.to_thread(
            Path(python_path).exists
        )
        if not python_exists:
            msg = f"{self.engine_name} python 不存在: {python_path}"
            raise FileNotFoundError(msg)

        worker_script = self._find_worker_script()
        env = self._build_subprocess_env()

        # limit: worker 单行 JSON 可能很大（含坐标/图片数据），
        # 默认 64KB 不够，提升到 OCRConfig.worker_stdio_buffer_bytes
        self._process = await asyncio.create_subprocess_exec(
            python_path,
            str(worker_script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=self._config.worker_stdio_buffer_bytes,
            start_new_session=self._start_new_session(),
        )
        # 启动 stderr drain task：worker 日志都写 stderr（stdout 是 JSON Lines
        # 协议通道，绝对不能被 drain）。不做这一步的话，worker 跑久了 stderr
        # pipe buffer（默认 64KB）写满，worker 内任何 print/logging 都会阻塞
        # 在 write()，下一条 OCR 命令的响应永远发不出来 → 主进程 300s 超时。
        self._stderr_tail.clear()
        if self._process.stderr is not None:
            self._stderr_drain_task = asyncio.create_task(
                _drain_stream_to_logger(
                    self._process.stderr,
                    f"[{self.engine_name} stderr]",
                    tail=self._stderr_tail,
                ),
                name=f"{self.engine_name}-stderr-drain",
            )

    async def initialize(
        self, on_progress: ProgressFn | None = None,
    ) -> None:
        """启动 worker 子进程并发送 initialize 命令。"""
        await self._start_worker_process()

        init_cmd = self._build_init_cmd()
        resp = await self._send_init_command(init_cmd, on_progress)
        if not resp.get("ok"):
            error = resp.get("error", "未知错误")
            msg = f"{self.engine_name} 初始化失败: {error}"
            raise RuntimeError(msg)

        self._ready = True
        logger.info("%s 引擎初始化完成", self.engine_name)

    # 优雅 shutdown 命令等 worker 响应的最长时间（秒）。
    # 远短于 _get_timeout() 的 OCR 超时（300s+），避免 worker 假死时 shutdown 雪崩。
    SHUTDOWN_COMMAND_TIMEOUT_SECONDS = 3.0

    async def shutdown(self, *, force: bool = False) -> None:
        """通用 shutdown：可选 graceful 命令 → 终止进程 → 关闭 stdin。

        - force=False（默认）：先发 shutdown 命令（短超时 3s），
          让 worker 自行清理；超时/异常立刻跳到 terminate。
        - force=True：跳过 graceful 命令，直接 terminate。
          供 _restart_worker 等"worker 假死"场景使用。
        """
        if self._process is not None:
            if not force:
                try:
                    await asyncio.wait_for(
                        self._send_command({"cmd": "shutdown"}),
                        timeout=self.SHUTDOWN_COMMAND_TIMEOUT_SECONDS,
                    )
                except (Exception, TimeoutError):
                    logger.debug(
                        "发送 shutdown 命令失败/超时（worker 可能假死）",
                        exc_info=True,
                    )
            try:
                await self._terminate_process()
            finally:
                # 进程已退出 → stderr 被关闭，drain task 会自然读到 EOF 退出；
                # 这里主动 cancel 是兜底（若 _terminate 异常或 EOF 延迟）
                if self._stderr_drain_task is not None:
                    self._stderr_drain_task.cancel()
                    with contextlib.suppress(
                        asyncio.CancelledError, Exception,
                    ):
                        await self._stderr_drain_task
                    self._stderr_drain_task = None
                # 显式关闭 stdin，避免 __del__ 时事件循环已关闭
                if self._process is not None and self._process.stdin:
                    self._process.stdin.close()
                    await self._process.stdin.wait_closed()
                self._process = None

        self._ready = False
        logger.info("%s 引擎已关闭", self.engine_name)

    async def _restart_worker(self) -> None:
        """重启 worker 进程（用于清理累积显存或最终兜底 resync）。

        restart 场景下 worker 往往已经假死/卡住，graceful 命令没有意义，
        直接 force=True 终止进程避免再次在 shutdown 命令上阻塞。重启后
        seq 归零、pending_resync 清空——worker 是新进程。
        """
        logger.info("重启 %s worker...", self.engine_name)
        await self.shutdown(force=True)
        self._seq = 0
        self._pending_resync = False
        self._pending_seq = None
        await self.initialize()
        logger.info("%s worker 重启完成", self.engine_name)

    async def _resync_if_needed(self) -> None:
        """若上次命令被 cancel 导致响应残留，drain 残留直到 seq 对齐。

        流程：
        1. `_pending_seq` 不为空时，循环从 stdout 读下一行 JSON 响应；
        2. `seq == pending_seq` → 残留已消费完毕，协议同步；
        3. `seq < pending_seq` → 更早的残留（理论不应出现），继续丢弃；
        4. `seq > pending_seq` / 缺失 seq / 解析失败 / 超时 → 无法安全恢复，
           fallback `_restart_worker()`。
        """
        if not self._pending_resync:
            return
        expected = self._pending_seq
        if expected is None or self._process is None:
            # 异常状态：置位但无信息 → 只能重启兜底
            await self._restart_worker()
            return
        stdout = self._process.stdout
        if stdout is None:
            await self._restart_worker()
            return

        read_timeout = self._get_timeout()
        logger.info(
            "检测到协议残留，开始 drain %s worker stdout 至 seq=%d",
            self.engine_name, expected,
        )
        try:
            while True:
                resp = await self._read_next_response(stdout, read_timeout)
                resp_seq = resp.get("seq")
                if isinstance(resp_seq, int):
                    if resp_seq == expected:
                        logger.info(
                            "%s resync 成功：已消费残留响应 seq=%d",
                            self.engine_name, expected,
                        )
                        self._pending_resync = False
                        self._pending_seq = None
                        return
                    if resp_seq < expected:
                        logger.debug(
                            "resync 丢弃更早残留响应 seq=%d（期望 %d）",
                            resp_seq, expected,
                        )
                        continue
                # seq > expected / seq 缺失：协议错乱，走 restart 兜底
                logger.warning(
                    "%s resync 读到异常响应 seq=%r（期望 %d），重启 worker",
                    self.engine_name, resp_seq, expected,
                )
                break
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            logger.warning(
                "%s resync 失败 (%s)，重启 worker 兜底",
                self.engine_name, exc,
            )
        await self._restart_worker()

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()。"""
        results: list[PageOCR] = []
        total = len(image_paths)
        for i, path in enumerate(image_paths):
            page = await self.ocr(path, output_dir)
            results.append(page)
            if on_progress is not None:
                on_progress(i + 1, total)
        return results

    async def _load_existing_ocr(
        self, image_path: Path, ocr_dir: Path
    ) -> PageOCR:
        """从 ocr_dir/OCR_RESULT_FILENAME 加载已有 OCR 结果。

        若同目录有 ``text_lines.jsonl``（PaddleOCR basic pipeline 产出），
        重建 ``PageOCR.text_lines`` 让缓存命中也能跑 IDE 布局识别（AGE-8）。
        """
        import json

        import aiofiles
        from PIL import Image

        from docrestore.models import TextLine

        result_mmd = ocr_dir / OCR_RESULT_FILENAME
        async with aiofiles.open(result_mmd, encoding="utf-8") as f:
            raw_text = await f.read()

        img = Image.open(image_path)
        image_size = img.size
        img.close()

        # 重建 text_lines（仅 basic pipeline 写过该文件）
        text_lines: list[TextLine] = []
        lines_path = ocr_dir / "text_lines.jsonl"
        if lines_path.exists():
            async with aiofiles.open(lines_path, encoding="utf-8") as f:
                content = await f.read()
            for line in content.splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    bbox = item.get("bbox")
                    if isinstance(bbox, list) and len(bbox) >= 4:
                        x1, y1, x2, y2 = (int(v) for v in bbox[:4])
                        text_lines.append(TextLine(
                            bbox=(x1, y1, x2, y2),
                            text=str(item.get("text", "")),
                            score=float(item.get("score", 0.0) or 0.0),
                        ))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

        return PageOCR(
            image_path=image_path,
            raw_text=raw_text,
            regions=[],
            output_dir=ocr_dir,
            image_size=image_size,
            has_eos=True,
            text_lines=text_lines,
        )

    async def _send_command(
        self, cmd: dict[str, object]
    ) -> dict[str, object]:
        """向 worker stdin 写 JSON，从 stdout 读 seq 匹配的 JSON 响应。

        协议：
        - 请求注入自增 `seq`；worker 回显 `seq`
        - cancel 打断时只记下 `_pending_seq`，下次 `_resync_if_needed` 负责
          drain 残留，避免整个 worker 重启
        - 读响应时 `seq < expected` 的旧帧直接丢弃（通常是 resync 未清完）
        """
        if self._process is None:
            msg = "Worker 进程未启动"
            raise RuntimeError(msg)

        stdin = self._process.stdin
        stdout = self._process.stdout
        if stdin is None or stdout is None:
            msg = "Worker 进程 stdin/stdout 不可用"
            raise RuntimeError(msg)

        self._seq += 1
        seq = self._seq
        payload = {**cmd, "seq": seq}

        line = json.dumps(payload, ensure_ascii=False) + "\n"
        stdin.write(line.encode("utf-8"))
        try:
            await stdin.drain()
        except asyncio.CancelledError:
            # 命令可能已部分/全部写入，worker 可能产生响应残留
            self._pending_resync = True
            self._pending_seq = seq
            raise

        read_timeout = self._get_timeout()
        while True:
            try:
                resp = await self._read_next_response(stdout, read_timeout)
            except asyncio.CancelledError:
                # worker 仍在处理，响应会残留在 stdout 缓冲区
                self._pending_resync = True
                self._pending_seq = seq
                raise
            except TimeoutError:
                msg = f"{self.engine_name} worker 响应超时({read_timeout}s)"
                raise RuntimeError(msg) from None

            resp_seq = resp.get("seq")
            if isinstance(resp_seq, int):
                if resp_seq == seq:
                    return resp
                if resp_seq < seq:
                    logger.debug(
                        "丢弃滞留响应 seq=%d（期望 %d）", resp_seq, seq,
                    )
                    continue
                # seq 超前：worker 协议错乱
                msg = (
                    f"{self.engine_name} 响应 seq={resp_seq} 超前于"
                    f"期望 seq={seq}，协议错乱"
                )
                raise RuntimeError(msg)
            # seq 缺失：老 worker / 协议不兼容 → 按当前响应返回（向后兼容）
            logger.debug(
                "响应未携带 seq（期望 %d），按当前响应返回", seq,
            )
            return resp

    async def _read_next_response(
        self,
        stdout: asyncio.StreamReader,
        read_timeout: float,
    ) -> dict[str, object]:
        """从 stdout 读下一行并解析为响应 dict。

        保留子类覆盖 `_read_response` 以跳过非 JSON 行（如 vLLM 日志混入）的
        能力；在此做一层 readline + worker-exit 兜底。
        """
        raw = await asyncio.wait_for(
            stdout.readline(), timeout=read_timeout,
        )
        if not raw:
            # 永远 raise，返回路径不可达
            await self._raise_worker_exited()
        return await self._read_response(raw, stdout, int(read_timeout))

    async def _raise_worker_exited(self) -> NoReturn:
        """worker 进程退出时，从 stderr tail 取最后 N 行抛出 RuntimeError。

        stderr 已由 drain task 持续消费写入 self._stderr_tail；这里等 drain
        task 把 EOF 前的残留读完（给它 1s 窗口），再把最近行拼起来当错误信息。
        """
        if self._stderr_drain_task is not None:
            with contextlib.suppress(
                asyncio.CancelledError, TimeoutError, Exception,
            ):
                await asyncio.wait_for(
                    self._stderr_drain_task, timeout=1.0,
                )
        stderr_text = "\n".join(self._stderr_tail)
        msg = (
            f"{self.engine_name} worker 意外退出: "
            f"{stderr_text[-500:]}"
        )
        raise RuntimeError(msg)
