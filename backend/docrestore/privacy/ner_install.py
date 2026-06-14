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

"""本地 NER 环境一键安装（spaCy + 模型）的子进程管理。

驱动 ``POST /ner/setup`` / ``GET /ner/setup/status``：单任务串行（第二次启动 409），
用 ``sys.executable -m pip/spacy`` 装进**当前 venv**（detector 惰性 import，装完免
重启即生效，见 pii-local-ner.md §6.3）。子进程遵守 concurrency-resource-safety：
``start_new_session=True`` + PIPE drain + 关停 cancel+await + killpg 兜底。

安全（§6.4）：模型名进 ``spacy download`` 前过白名单正则，命令固定为 spaCy + 校验过
的模型，list-form 无 shell，绝不接受任意包名。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import sys
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

#: 安装任务状态。idle=未启动；running=安装中；done=成功；failed=失败/取消。
SetupState = Literal["idle", "running", "done", "failed"]

#: spaCy 模型名白名单：双/三字母语言码 + _core_(web|news)_(sm|md|lg)。
_VALID_MODEL_RE = re.compile(r"^[a-z]{2,3}_core_(web|news)_(sm|md|lg)$")

#: spaCy 版本约束（与 pyproject [project.optional-dependencies].ner 对齐）。
_SPACY_SPEC = "spacy>=3.8,<4"


class NERSetupManager:
    """本地 NER 环境一键安装的子进程管理器（单任务串行 + 进度可轮询）。"""

    def __init__(self, *, log_cap: int = 200) -> None:
        """初始化空闲状态。``log_cap`` 限制保留的日志尾行数（防无界增长）。"""
        self._state: SetupState = "idle"
        self._log: list[str] = []
        self._error = ""
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._start_lock = asyncio.Lock()
        self._log_cap = log_cap

    @staticmethod
    def validate_models(model_names: Sequence[str]) -> list[str]:
        """返回不合法的模型名（空列表=全部合法）。供启动前白名单校验。"""
        return [m for m in model_names if not _VALID_MODEL_RE.match(m)]

    def status(self) -> dict[str, object]:
        """当前安装状态快照：``{state, log(尾部), error}``。"""
        return {
            "state": self._state,
            "log": list(self._log),
            "error": self._error,
        }

    async def start(self, model_names: Sequence[str]) -> bool:
        """启动安装；已有任务在跑 → False（调用方 409）。非法模型名抛 ValueError。"""
        invalid = self.validate_models(model_names)
        if invalid:
            msg = f"非法 spaCy 模型名（白名单外）：{invalid}"
            raise ValueError(msg)
        async with self._start_lock:
            if self._state == "running":
                return False
            self._state = "running"
            self._log = []
            self._error = ""
            self._task = asyncio.create_task(
                self._run(tuple(model_names)), name="ner-setup",
            )
            return True

    async def _run(self, model_names: tuple[str, ...]) -> None:
        """安装流程：pip install spaCy → 逐个 spacy download（任一失败即停）。"""
        try:
            ok = await self._exec(
                [sys.executable, "-m", "pip", "install", _SPACY_SPEC],
            )
            for name in model_names:
                if not ok:
                    break
                ok = await self._exec(
                    [sys.executable, "-m", "spacy", "download", name],
                )
            self._state = "done" if ok else "failed"
        except asyncio.CancelledError:
            self._append("安装被取消")
            self._state = "failed"
            await self._kill_proc()
            raise
        except Exception as exc:  # 安装异常 → failed，错误透出（不外抛崩 loop）
            logger.warning("NER 环境安装异常", exc_info=True)
            self._error = str(exc)
            self._state = "failed"
        finally:
            self._proc = None

    async def _exec(self, cmd: list[str]) -> bool:
        """跑一条命令，drain stdout 到日志，返回 returncode==0。

        ``start_new_session=True`` 使 pid==pgid，便于 killpg 整组清理；stdout+stderr
        合流到 PIPE 并持续 drain（否则 64KB pipe 写满 → 子进程阻塞）。命令固定为
        ``sys.executable -m pip/spacy`` + 已白名单校验的模型名（§6.4），无 shell。
        """
        self._append(f"$ {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(  # noqa: S603 — 固定命令+模型白名单
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self._proc = proc
        if proc.stdout is not None:
            async for raw in proc.stdout:
                self._append(raw.decode("utf-8", "replace").rstrip())
        rc = await proc.wait()
        self._proc = None
        if rc != 0:
            self._error = f"命令失败（退出码 {rc}）：{' '.join(cmd)}"
        return rc == 0

    async def _kill_proc(self) -> None:
        """SIGTERM 整个进程组（start_new_session 保证 pgid=pid），容忍已退出。"""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

    async def shutdown(self) -> None:
        """关停：cancel 安装任务 + await（吞 CancelledError）+ killpg 兜底。"""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._kill_proc()
        self._task = None
        self._proc = None

    def _append(self, line: str) -> None:
        """追加一行日志，超过 log_cap 截断保留尾部。"""
        self._log.append(line)
        if len(self._log) > self._log_cap:
            self._log = self._log[-self._log_cap:]
