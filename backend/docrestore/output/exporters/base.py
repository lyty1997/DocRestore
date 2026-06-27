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

"""导出器协议与公共工具（Epic D）。

导出在**下载环节**按需运行：输入是已落盘的 ``document.md`` + ``images/``，
输出对应格式文件（docx/pdf/...）。不进 pipeline，不碰任务配置。
详见 ``docs/zh/export-mode.md``。

所有导出器都是阻塞的子进程 / 库调用，由下载路由用 ``asyncio.to_thread`` 包裹后调用。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

try:
    import resource as _resource
except ImportError:  # pragma: no cover - 非 POSIX 平台
    _resource = None  # type: ignore[assignment]

#: 导出产物缓存目录名（落在每个 doc_dir 下，不进 asset 白名单、不裸打进 zip）
EXPORT_CACHE_DIRNAME = ".exports"

#: 导出子进程资源限额（POSIX）：墙钟超时为主，rlimit 为辅
_EXPORT_RLIMIT_CPU_SECONDS = 60
#: 单个产物大小上限（挡失控写盘；pandoc/weasyprint 正常产物远小于此）
_EXPORT_RLIMIT_FSIZE_BYTES = 256 * 1024 * 1024
#: 导出子进程墙钟超时
_EXPORT_TIMEOUT_SECONDS = 120


class ExportError(Exception):
    """导出相关异常基类。"""


class ExportToolUnavailable(ExportError):
    """外部导出依赖（pandoc / weasyprint）缺失 —— fail-closed。"""

    def __init__(self, tool: str) -> None:
        """记录缺失的工具名，供上层映射 503 + i18n params。"""
        super().__init__(f"导出依赖不可用: {tool}")
        self.tool = tool


class ExportFailed(ExportError):
    """导出过程失败（子进程非零退出 / 渲染异常）。"""

    def __init__(self, tool: str, fmt: str, reason: str) -> None:
        """携带工具名、格式与原因，供上层映射 500 + i18n params。"""
        super().__init__(f"导出失败[{fmt}/{tool}]: {reason}")
        self.tool = tool
        self.fmt = fmt
        self.reason = reason


@runtime_checkable
class Exporter(Protocol):
    """把 ``document.md`` (+``images/``) 导出为某格式的协议。

    实现类需提供：
    - ``suffix``：输出文件扩展名（不含点），如 ``"docx"`` / ``"pdf"``。
    - ``tool``：底层工具名（错误提示 / i18n params 用）。
    - ``ensure_available()``：依赖缺失时抛 :class:`ExportToolUnavailable`。
    - ``export()``：生成产物；失败抛 :class:`ExportFailed`。
    """

    #: 输出文件扩展名（不含点）
    suffix: str
    #: 底层工具名（错误提示用）
    tool: str

    def ensure_available(self) -> None:
        """依赖缺失时抛 :class:`ExportToolUnavailable`。"""
        ...

    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None:
        """从 ``doc_md`` (+``assets_dir``) 生成 ``out_path``。

        失败抛 :class:`ExportFailed`；依赖缺失抛 :class:`ExportToolUnavailable`。
        """
        ...


def export_content_hash(doc_md: Path) -> str:
    """以 ``document.md`` 内容的 sha256 前 16 位做缓存键。

    内容不变即复用产物，避免每次下载都重跑导出子进程。
    """
    digest = hashlib.sha256(doc_md.read_bytes()).hexdigest()
    return digest[:16]


def export_cache_path(doc_dir: Path, suffix: str, content_hash: str) -> Path:
    """导出产物缓存路径：``{doc_dir}/.exports/{content_hash}.{suffix}``。"""
    return doc_dir / EXPORT_CACHE_DIRNAME / f"{content_hash}.{suffix}"


def export_to_cache(
    exporter: Exporter,
    doc_md: Path,
    assets_dir: Path,
    cache: Path,
) -> None:
    """原子地把导出产物写入 ``cache``：先写同目录临时文件，再 ``os.replace`` 落位。

    解决并发下载竞态：缓存命中走 ``cache.is_file()`` 这条「存在即用」路径，若产物
    直接写最终路径，另一个并发请求可能读到尚未写完的半成品并打进 zip。临时文件
    与 ``cache`` 同目录（同一文件系统）保证 ``os.replace`` 是原子 rename；读者只会
    看到「不存在」或「完整产物」。重复导出（两请求都 miss）仍可能各跑一遍，但
    「最后写者胜」是原子的，两份都是完整产物，无损坏。

    任一步失败都清理临时文件并向上抛原异常（:class:`ExportFailed` /
    :class:`ExportToolUnavailable`），由调用方映射 HTTP 状态。
    """
    cache.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{cache.stem}.",
        suffix=f".{exporter.suffix}.tmp",
        dir=str(cache.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        exporter.export(doc_md, assets_dir, tmp_path)
        os.replace(tmp_path, cache)
    finally:
        tmp_path.unlink(missing_ok=True)


def clear_export_caches(root: Path) -> int:
    """删除 ``root`` 子树下所有导出缓存目录（``.exports/``），返回删除的目录数。

    续跑（resume）复用同一 ``output_dir`` 时，``document.md`` 可能字节不变（LLM
    缓存命中产出相同字节）而附属输入（图片 / ``.ppt_layout.json``）已变；导出缓存键
    只哈希 ``document.md``（见 :func:`export_content_hash`），命中即返回 stale 产物。
    续跑前清空缓存即关闭该窗口（#3），下次下载按新产物重新导出。

    防御性吞 ``OSError``（目录在遍历途中消失等），逐目录 ``ignore_errors`` 删除。
    """
    removed = 0
    with contextlib.suppress(OSError):
        for cache_dir in root.rglob(EXPORT_CACHE_DIRNAME):
            if cache_dir.is_dir():
                shutil.rmtree(cache_dir, ignore_errors=True)
                removed += 1
    return removed


def resolve_tool(tool: str) -> str | None:
    """定位外部工具二进制（``shutil.which``）；不存在返回 ``None``。"""
    return shutil.which(tool)


def _export_preexec() -> None:  # pragma: no cover - 仅子进程 fork 后执行
    """导出子进程资源限额（仅 POSIX）：CPU + 产物大小，挡失控渲染。"""
    if _resource is None:
        return
    for res, limit in (
        (_resource.RLIMIT_CPU, _EXPORT_RLIMIT_CPU_SECONDS),
        (_resource.RLIMIT_FSIZE, _EXPORT_RLIMIT_FSIZE_BYTES),
    ):
        with contextlib.suppress(ValueError, OSError):
            _resource.setrlimit(res, (limit, limit))


def _kill_export_group(proc: subprocess.Popen[bytes]) -> None:
    """超时后强杀子进程组（POSIX），回退到只杀直接子进程。"""
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.kill()


def run_export_command(
    cmd: list[str],
    *,
    cwd: Path,
    tool: str,
    fmt: str,
    timeout_s: int = _EXPORT_TIMEOUT_SECONDS,
) -> None:
    """运行外部导出命令（pandoc 等），fail-closed。

    复用项目子进程纪律：``start_new_session`` 独占进程组、POSIX rlimit 限额、
    ``communicate`` 墙钟超时、超时 ``killpg`` + 二次回收避免僵尸/孤儿。
    非零退出 / 超时 → :class:`ExportFailed`（带 stderr 摘要）；
    二进制在校验后被移走的竞态 → :class:`ExportToolUnavailable`。
    """
    try:
        with subprocess.Popen(  # noqa: S603 — cmd 由本模块固定构造，无 shell
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_export_preexec if os.name == "posix" else None,  # noqa: PLW1509
        ) as proc:
            try:
                _, stderr = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                _kill_export_group(proc)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.communicate(timeout=10)
                raise ExportFailed(tool, fmt, f"超时 {timeout_s}s") from exc
            if proc.returncode != 0:
                detail = (stderr or b"").decode("utf-8", "replace").strip()
                raise ExportFailed(
                    tool, fmt, detail[:500] or f"exit {proc.returncode}",
                )
    except FileNotFoundError as exc:  # 二进制在 ensure_available 后被移走
        raise ExportToolUnavailable(tool) from exc
    except OSError as exc:
        raise ExportFailed(tool, fmt, str(exc)) from exc
