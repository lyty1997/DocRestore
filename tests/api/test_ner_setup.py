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

"""本地 NER 一键安装 NERSetupManager + 端点单测（S3.4b）。

``create_subprocess_exec`` 经 monkeypatch 注入 fake 进程，**绝不真跑 pip /
spacy download**。覆盖：模型白名单校验、成功/失败状态机、单任务串行（双启 409
路径）、关停空闲 no-op、端点 200 全流程。
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable, Iterable

import pytest
from httpx import AsyncClient

from docrestore.privacy import ner_install
from docrestore.privacy.ner_install import NERSetupManager


class _FakeStdout:
    """异步行迭代器（喂 fake 子进程的 stdout）。"""

    def __init__(self, lines: Iterable[bytes]) -> None:
        """缓存预置输出行。"""
        self._lines = list(lines)

    def __aiter__(self) -> _FakeStdout:
        """自身即异步迭代器。"""
        return self

    async def __anext__(self) -> bytes:
        """逐行吐出，耗尽抛 StopAsyncIteration。"""
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    """fake 子进程：预置输出行 + 退出码；可选 release 事件挂起 wait()。"""

    def __init__(
        self,
        rc: int,
        lines: Iterable[bytes] = (),
        *,
        release: asyncio.Event | None = None,
    ) -> None:
        """记录退出码、输出行、可选挂起事件。"""
        self.returncode: int | None = None
        self.pid = 424242
        self.stdout = _FakeStdout(lines)
        self._rc = rc
        self._release = release

    async def wait(self) -> int:
        """返回退出码；配置了 release 则先挂起直到事件置位。"""
        if self._release is not None:
            await self._release.wait()
        self.returncode = self._rc
        return self._rc


def _patch_exec(
    monkeypatch: pytest.MonkeyPatch,
    proc_factory: Callable[[], _FakeProc],
) -> None:
    """monkeypatch asyncio.create_subprocess_exec → 每次调用返回新 fake 进程。"""

    async def _fake(*_args: object, **_kwargs: object) -> _FakeProc:
        return proc_factory()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


class _SignalRecordingProc:
    """fake 子进程：记录收到的信号。SIGTERM 是否生效由 obey_sigterm 决定，
    SIGKILL 一律致死——用于验证 _kill_proc 的 SIGTERM→SIGKILL 升级与回收（#63）。"""

    def __init__(self, *, obey_sigterm: bool) -> None:
        """记录是否听从 SIGTERM；初始未退出。"""
        self.returncode: int | None = None
        self.pid = 525252
        self.stdout = _FakeStdout([])
        self._obey_sigterm = obey_sigterm
        self._dead = asyncio.Event()
        self.signals: list[int] = []

    async def wait(self) -> int:
        """挂起直到收到致命信号，返回退出码（SIGKILL → -9）。"""
        await self._dead.wait()
        self.returncode = -9 if signal.SIGKILL in self.signals else 0
        return self.returncode

    def deliver(self, sig: int) -> None:
        """记录信号；致命信号置位 _dead 让 wait() 返回。"""
        self.signals.append(sig)
        if sig == signal.SIGKILL or (
            self._obey_sigterm and sig == signal.SIGTERM
        ):
            self._dead.set()


def _patch_killpg(
    monkeypatch: pytest.MonkeyPatch, proc: _SignalRecordingProc, *, pgid: int,
) -> None:
    """把 os.getpgid/os.killpg 接到 fake 进程的信号记录（不碰真实进程）。"""
    monkeypatch.setattr(os, "getpgid", lambda _pid: pgid)
    monkeypatch.setattr(
        os, "killpg",
        lambda gid, sig: proc.deliver(sig) if gid == pgid else None,
    )


@pytest.mark.asyncio
async def test_kill_proc_sigkill_fallback_when_sigterm_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """忽略 SIGTERM 的 stuck 子进程 → grace 后 SIGKILL 兜底 + wait() 回收（#63）。"""
    proc = _SignalRecordingProc(obey_sigterm=False)
    mgr = NERSetupManager()
    mgr._proc = proc  # type: ignore[assignment]
    _patch_killpg(monkeypatch, proc, pgid=777)

    await mgr._kill_proc(grace=0.05)

    assert signal.SIGTERM in proc.signals  # 先优雅
    assert signal.SIGKILL in proc.signals  # 再强杀兜底
    assert proc.returncode is not None  # 已回收（无僵尸）


@pytest.mark.asyncio
async def test_run_reaps_proc_on_generic_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_exec 泛异常 → _run 的 finally 仍杀+回收子进程（#63：原 except 路径漏杀）。"""
    proc = _SignalRecordingProc(obey_sigterm=True)
    mgr = NERSetupManager()
    _patch_killpg(monkeypatch, proc, pgid=888)

    async def _boom(_cmd: list[str]) -> bool:
        # 模拟 spawn 后、命令处理中异常
        mgr._proc = proc  # type: ignore[assignment]
        raise RuntimeError("drain 崩了")

    monkeypatch.setattr(mgr, "_exec", _boom)

    await mgr._run(("zh_core_web_md",))

    assert mgr.status()["state"] == "failed"
    assert signal.SIGTERM in proc.signals  # finally 清理了子进程
    assert proc.returncode is not None  # 已回收
    assert mgr._proc is None  # finally 置空


@pytest.mark.asyncio
async def test_shutdown_kills_running_proc_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中 shutdown：cancel + finally 杀+回收子进程，无异常逃逸（#63）。"""
    proc = _SignalRecordingProc(obey_sigterm=True)
    started = asyncio.Event()
    mgr = NERSetupManager()
    _patch_killpg(monkeypatch, proc, pgid=999)

    async def _hang(_cmd: list[str]) -> bool:
        mgr._proc = proc  # type: ignore[assignment]
        started.set()
        await asyncio.Event().wait()  # 永久挂起，直到被 cancel
        return True

    monkeypatch.setattr(mgr, "_exec", _hang)

    assert await mgr.start(["zh_core_web_md"]) is True
    await started.wait()
    await mgr.shutdown()  # cancel + await + kill

    assert signal.SIGTERM in proc.signals
    assert proc.returncode is not None
    assert mgr.status()["state"] == "failed"  # 取消置 failed


@pytest.mark.asyncio
async def test_install_success_resets_detector_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """安装成功后调用 reset_detector_cache（装后免重启生效，#61）。"""
    called = {"n": 0}
    monkeypatch.setattr(
        ner_install, "reset_detector_cache",
        lambda: called.__setitem__("n", called["n"] + 1),
    )
    _patch_exec(monkeypatch, lambda: _FakeProc(0, [b"ok\n"]))
    mgr = NERSetupManager()
    assert await mgr.start(["zh_core_web_md"]) is True
    assert mgr._task is not None
    await mgr._task

    assert mgr.status()["state"] == "done"
    assert called["n"] == 1


def test_validate_models_whitelist() -> None:
    """白名单：合法 spaCy 模型名放行，非法（任意包/路径逃逸）被拒。"""
    assert NERSetupManager.validate_models(
        ["zh_core_web_md", "en_core_web_lg", "fr_core_news_sm"],
    ) == []
    bad = NERSetupManager.validate_models(["zh_core_web_md", "evil_pkg", "../x"])
    assert "evil_pkg" in bad
    assert "../x" in bad


@pytest.mark.asyncio
async def test_install_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """全命令 rc=0 → state=done，error 空。"""
    _patch_exec(monkeypatch, lambda: _FakeProc(0, [b"Collecting spacy\n"]))
    mgr = NERSetupManager()
    assert await mgr.start(["zh_core_web_md"]) is True
    assert mgr._task is not None
    await mgr._task
    st = mgr.status()
    assert st["state"] == "done"
    assert st["error"] == ""


@pytest.mark.asyncio
async def test_install_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令 rc=1 → state=failed，error 非空。"""
    _patch_exec(monkeypatch, lambda: _FakeProc(1))
    mgr = NERSetupManager()
    await mgr.start(["zh_core_web_md"])
    assert mgr._task is not None
    await mgr._task
    st = mgr.status()
    assert st["state"] == "failed"
    assert st["error"]


@pytest.mark.asyncio
async def test_invalid_model_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法模型名 → start 抛 ValueError（不启动子进程，状态仍 idle）。"""
    _patch_exec(monkeypatch, lambda: _FakeProc(0))
    mgr = NERSetupManager()
    with pytest.raises(ValueError, match="模型名"):
        await mgr.start(["evil_pkg"])
    assert mgr.status()["state"] == "idle"


@pytest.mark.asyncio
async def test_double_start_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有安装在跑 → 第二次 start False（单任务串行，端点据此 409）。"""
    release = asyncio.Event()
    _patch_exec(monkeypatch, lambda: _FakeProc(0, release=release))
    mgr = NERSetupManager()
    assert await mgr.start(["zh_core_web_md"]) is True
    assert await mgr.start(["zh_core_web_md"]) is False
    release.set()
    assert mgr._task is not None
    await mgr._task
    assert mgr.status()["state"] == "done"


@pytest.mark.asyncio
async def test_shutdown_idle_noop() -> None:
    """空闲时 shutdown 不抛，状态仍 idle。"""
    mgr = NERSetupManager()
    await mgr.shutdown()
    assert mgr.status()["state"] == "idle"


@pytest.mark.asyncio
async def test_setup_endpoint_smoke(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /ner/setup → 200；轮询 GET /ner/setup/status 直到 done。"""
    _patch_exec(monkeypatch, lambda: _FakeProc(0, [b"ok\n"]))
    resp = await api_client.post("/api/v1/ner/setup")
    assert resp.status_code == 200
    assert resp.json()["state"] in ("running", "done")

    st = resp
    for _ in range(50):
        st = await api_client.get("/api/v1/ner/setup/status")
        assert st.status_code == 200
        if st.json()["state"] == "done":
            break
        await asyncio.sleep(0.01)
    assert st.json()["state"] == "done"
