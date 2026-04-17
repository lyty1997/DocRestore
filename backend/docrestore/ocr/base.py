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
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

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
        self._desync = False

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
                # 显式关闭 stdin，避免 __del__ 时事件循环已关闭
                if self._process is not None and self._process.stdin:
                    self._process.stdin.close()
                    await self._process.stdin.wait_closed()
                self._process = None

        self._ready = False
        logger.info("%s 引擎已关闭", self.engine_name)

    async def _restart_worker(self) -> None:
        """重启 worker 进程（用于清理累积显存或恢复协议失步）。

        restart 场景下 worker 往往已经假死/卡住，graceful 命令没有意义，
        直接 force=True 终止进程避免再次在 shutdown 命令上阻塞。
        """
        logger.info("重启 %s worker...", self.engine_name)
        await self.shutdown(force=True)
        await self.initialize()
        logger.info("%s worker 重启完成", self.engine_name)

    async def _recover_desync_if_needed(self) -> None:
        """若上次命令被 cancel 导致协议失步，重启 worker 恢复同步。"""
        if self._desync:
            logger.warning(
                "检测到协议失步，重启 %s worker 恢复同步", self.engine_name,
            )
            await self._restart_worker()
            self._desync = False

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
        """从 ocr_dir/OCR_RESULT_FILENAME 加载已有 OCR 结果。"""
        import aiofiles
        from PIL import Image

        result_mmd = ocr_dir / OCR_RESULT_FILENAME
        async with aiofiles.open(result_mmd, encoding="utf-8") as f:
            raw_text = await f.read()

        img = Image.open(image_path)
        image_size = img.size
        img.close()

        return PageOCR(
            image_path=image_path,
            raw_text=raw_text,
            regions=[],
            output_dir=ocr_dir,
            image_size=image_size,
            has_eos=True,
        )

    async def _send_command(
        self, cmd: dict[str, object]
    ) -> dict[str, object]:
        """向 worker stdin 写 JSON，从 stdout 读 JSON 响应。"""
        if self._process is None:
            msg = "Worker 进程未启动"
            raise RuntimeError(msg)

        stdin = self._process.stdin
        stdout = self._process.stdout
        if stdin is None or stdout is None:
            msg = "Worker 进程 stdin/stdout 不可用"
            raise RuntimeError(msg)

        line = json.dumps(cmd, ensure_ascii=False) + "\n"
        stdin.write(line.encode("utf-8"))
        try:
            await stdin.drain()
        except asyncio.CancelledError:
            # 命令可能已部分/全部写入，worker 可能产生响应残留
            self._desync = True
            raise

        read_timeout = self._get_timeout()
        try:
            raw = await asyncio.wait_for(
                stdout.readline(), timeout=read_timeout,
            )
        except asyncio.CancelledError:
            # worker 仍在处理，响应会残留在 stdout 缓冲区
            self._desync = True
            raise
        except TimeoutError:
            msg = f"{self.engine_name} worker 响应超时({read_timeout}s)"
            raise RuntimeError(msg) from None

        if not raw:
            await self._raise_worker_exited()

        return await self._read_response(raw, stdout, read_timeout)

    async def _raise_worker_exited(self) -> None:
        """worker 进程退出时，收集 stderr 后抛出 RuntimeError。"""
        stderr_text = ""
        if self._process is not None and self._process.stderr is not None:
            stderr_bytes = await self._process.stderr.read()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        msg = f"{self.engine_name} worker 意外退出: {stderr_text[:500]}"
        raise RuntimeError(msg)
