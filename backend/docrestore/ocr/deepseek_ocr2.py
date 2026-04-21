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

"""DeepSeek-OCR-2 引擎实现（subprocess 调用独立 conda 环境）

通过 JSON Lines 协议与 scripts/deepseek_ocr_worker.py 通信，
实现环境隔离（DeepSeek-OCR-2 与 PaddleOCR 的依赖不兼容）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
from collections.abc import Callable
from pathlib import Path

from docrestore.models import PageOCR, Region
from docrestore.ocr.base import (
    OCR_RESULT_FILENAME,
    ProgressFn,
    WorkerBackedOCREngine,
)

logger = logging.getLogger(__name__)


class _OOMError(RuntimeError):
    """Worker 侧显存不足 —— 触发 batch_size 降级重试。"""


def _extract_stderr_message(line: str) -> str | None:
    """从 worker stderr 行中提取用户可读的进度信息。

    返回 None 表示该行不值得展示给用户。
    模式与 EngineManager._extract_stderr_message 对齐（vLLM 通用）。
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

    # vLLM 引擎初始化
    if "EngineCore" in line and "pid=" in line:
        return "vLLM 推理引擎初始化中..."

    # vLLM GPU 显存分配
    if "gpu_memory_utilization" in line or "GPU blocks" in line:
        return "GPU 显存分配中..."

    # 模型下载进度
    m = re.search(r"Downloading.*?(\d+%)", line)
    if m:
        return f"下载模型文件... {m.group(1)}"

    # worker 自身日志：初始化阶段
    if "创建 vLLM 引擎" in line or "Creating engine" in line:
        return "创建 vLLM 推理引擎..."

    if "初始化完成" in line or "initialized" in line.lower():
        return "模型加载完成"

    return None


class DeepSeekOCR2Engine(WorkerBackedOCREngine):
    """DeepSeek-OCR-2 引擎（通过 subprocess 调用独立 conda 环境）"""

    engine_name = "DeepSeek-OCR-2"
    worker_script_path = "scripts/deepseek_ocr_worker.py"

    # ── 基类钩子实现 ──────────────────────────────────────

    def _get_python_path(self) -> str:
        return self._config.deepseek_python

    def _get_timeout(self) -> int:
        return self._config.deepseek_ocr_timeout

    def _resolve_worker_script(self) -> str:
        return (
            self._config.deepseek_worker_script
            or self.worker_script_path
        )

    def _build_subprocess_env(self) -> dict[str, str]:
        env = {**os.environ}
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        # gpu_id 通常由 engine_manager.ensure() 落地；pipeline 直接调 create_engine
        # 时可能仍是 None，此处兜底调 pick_best_gpu。
        from docrestore.ocr.gpu_detect import pick_best_gpu
        gpu_id = self._config.gpu_id or pick_best_gpu() or "0"
        env["CUDA_VISIBLE_DEVICES"] = gpu_id
        logger.info("DeepSeek worker GPU: %s", gpu_id)
        return env

    def _start_new_session(self) -> bool:
        # 独立进程组，方便 killpg 清理 vLLM 子进程
        return True

    def _build_init_cmd(self) -> dict[str, object]:
        cmd: dict[str, object] = {
            "cmd": "initialize",
            "model_path": self._config.model_path,
            "gpu_memory_utilization": self._config.gpu_memory_utilization,
            "max_model_len": self._config.max_model_len,
            "max_tokens": self._config.max_tokens,
            "base_size": self._config.base_size,
            "crop_size": self._config.crop_size,
            "min_crops": self._config.min_crops,
            "max_crops": self._config.max_crops,
            "ngram_size": self._config.ngram_size,
            "ngram_window_size": self._config.ngram_window_size,
            "ngram_whitelist_token_ids": sorted(
                self._config.ngram_whitelist_token_ids,
            ),
            "normalize_mean": list(self._config.normalize_mean),
            "normalize_std": list(self._config.normalize_std),
            "prompt": self._config.prompt,
            # 两引擎共有的 vLLM 优化参数透传（None 时 worker 不覆盖默认值）
            "vllm_enforce_eager": self._config.vllm_enforce_eager,
            "vllm_block_size": self._config.vllm_block_size,
            "vllm_swap_space_gb": self._config.vllm_swap_space_gb,
            "vllm_disable_mm_preprocessor_cache": (
                self._config.vllm_disable_mm_preprocessor_cache
            ),
            "vllm_disable_log_stats": self._config.vllm_disable_log_stats,
            # GPU 监控配置（worker 内后台 task）
            "gpu_monitor_enable": self._config.gpu_monitor_enable,
            "gpu_monitor_interval_s": self._config.gpu_monitor_interval_s,
            "gpu_memory_safety_margin_mib": (
                self._config.gpu_memory_safety_margin_mib
            ),
        }
        return cmd

    async def _send_init_command(
        self,
        init_cmd: dict[str, object],
        on_progress: ProgressFn | None,
    ) -> dict[str, object]:
        """并发读 stderr 推送初始化进度（vLLM 加载耗时长）。"""
        stop_event = asyncio.Event()
        stderr_task = asyncio.create_task(
            self._stream_stderr_progress(stop_event, on_progress),
            name="deepseek-stderr-progress",
        )
        try:
            resp = await self._send_command(init_cmd)
        finally:
            # 先 set 让 drain 自然退出；再 cancel 兜底（drain 可能卡在
            # wait_for readline 的 0.5s 间隙内，cancel 立即打断）；最后
            # gather 等它完全退出，避免 initialize 被 cancel 时 stderr
            # 任务残留，worker pipe buffer 继续堆积。
            stop_event.set()
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stderr_task
        return resp

    async def _terminate_process(self) -> None:
        """向整个进程组发送信号，清理 vLLM 子进程。"""
        if self._process is None:
            return
        pid = self._process.pid
        try:
            os.killpg(pid, signal.SIGTERM)
            await asyncio.wait_for(
                self._process.wait(),
                timeout=self._config.worker_terminate_timeout,
            )
        except ProcessLookupError:
            pass
        except (TimeoutError, Exception):
            if self._process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGKILL)
                await self._process.wait()

    async def _read_response(
        self,
        raw: bytes,
        stdout: asyncio.StreamReader,
        read_timeout: int,
    ) -> dict[str, object]:
        """跳过 worker stdout 中混入的 vLLM/transformers 日志行。"""
        buffered = raw
        while True:
            text = buffered.decode("utf-8").strip()
            if text:
                try:
                    result: dict[str, object] = json.loads(text)
                    return result
                except json.JSONDecodeError:
                    logger.debug(
                        "跳过 worker stdout 非 JSON 行: %s", text[:200],
                    )
            try:
                buffered = await asyncio.wait_for(
                    stdout.readline(), timeout=read_timeout,
                )
            except TimeoutError:
                msg = f"{self.engine_name} worker 响应超时({read_timeout}s)"
                raise RuntimeError(msg) from None
            if not buffered:
                msg = (
                    f"{self.engine_name} worker 在等待 JSON 响应时退出"
                )
                raise RuntimeError(msg)

    # ── OCR 主流程 ────────────────────────────────────────

    async def ocr(
        self, image_path: Path, output_dir: Path
    ) -> PageOCR:
        """单张 OCR，结果写入 output_dir/{stem}_OCR/"""
        await self._recover_desync_if_needed()

        # 增量OCR：检查已有结果
        ocr_dir = output_dir / f"{image_path.stem}_OCR"
        result_mmd = ocr_dir / OCR_RESULT_FILENAME
        if result_mmd.exists():
            logger.info("跳过已有OCR结果: %s", image_path.name)
            return await self._load_existing_ocr(image_path, ocr_dir)

        resp = await self._send_command({
            "cmd": "ocr",
            "image_path": str(image_path),
            "output_dir": str(output_dir),
            "enable_column_filter": self._config.enable_column_filter,
            "column_filter_min_sidebar": self._config.column_filter_min_sidebar,
        })

        if not resp.get("ok"):
            error = str(resp.get("error", "未知错误"))
            if self._is_oom_error(error):
                raise _OOMError(error)
            msg = f"DeepSeek-OCR-2 处理失败: {error}"
            raise RuntimeError(msg)

        return self._parse_single_result(resp, image_path, ocr_dir)

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """批量 OCR：一次向 worker 提交 N 张图，worker 内并发处理。

        - batch_size < 2 时回退基类逐张实现（保留对照路径）。
        - 已有 result.mmd 的图跳过 worker，直接从磁盘加载。
        - OOM 时按 batch_size 对半降级重试，单图仍 OOM 才抛 RuntimeError。
        """
        await self._recover_desync_if_needed()

        batch_size = max(1, self._config.ocr_batch_size)
        if batch_size < 2:
            return await super().ocr_batch(
                image_paths, output_dir, on_progress,
            )

        total = len(image_paths)
        results: dict[Path, PageOCR] = {}
        pending: list[Path] = []

        completed = 0
        for path in image_paths:
            ocr_dir = output_dir / f"{path.stem}_OCR"
            if (ocr_dir / OCR_RESULT_FILENAME).exists():
                logger.info("跳过已有OCR结果: %s", path.name)
                results[path] = await self._load_existing_ocr(path, ocr_dir)
                completed += 1
                if on_progress is not None:
                    on_progress(completed, total)
            else:
                pending.append(path)

        if pending:
            processed = await self._send_ocr_batch_with_oom_retry(
                pending, output_dir, batch_size,
                on_progress, completed, total,
            )
            results.update(processed)

        return [results[p] for p in image_paths if p in results]

    async def _send_ocr_batch_with_oom_retry(
        self,
        pending: list[Path],
        output_dir: Path,
        batch_size: int,
        on_progress: Callable[[int, int], None] | None,
        already_done: int,
        total: int,
    ) -> dict[Path, PageOCR]:
        """按 batch_size 分块发送；遇 OOM 对半降级，不再回升避免震荡。"""
        results: dict[Path, PageOCR] = {}
        i = 0
        current_size = max(1, batch_size)
        completed = already_done
        while i < len(pending):
            chunk = pending[i : i + current_size]
            try:
                chunk_results = await self._send_ocr_batch_all(
                    chunk, output_dir,
                )
            except _OOMError as exc:
                if current_size == 1:
                    msg = f"DeepSeek-OCR-2 单图 OCR 显存不足: {exc}"
                    raise RuntimeError(msg) from exc
                new_size = max(1, current_size // 2)
                logger.warning(
                    "DeepSeek-OCR-2 batch OOM，降级 %d → %d",
                    current_size, new_size,
                )
                current_size = new_size
                continue

            results.update(chunk_results)
            i += len(chunk)
            completed += len(chunk)
            if on_progress is not None:
                on_progress(completed, total)

        return results

    async def _send_ocr_batch_all(
        self,
        chunk: list[Path],
        output_dir: Path,
    ) -> dict[Path, PageOCR]:
        """发送单个 chunk 并解析 worker 的 batch 响应。"""
        resp = await self._send_command({
            "cmd": "ocr_batch",
            "image_paths": [str(p) for p in chunk],
            "output_dir": str(output_dir),
            "enable_column_filter": self._config.enable_column_filter,
            "column_filter_min_sidebar": self._config.column_filter_min_sidebar,
        })

        if not resp.get("ok"):
            error = str(resp.get("error", "未知错误"))
            if self._is_oom_error(error):
                raise _OOMError(error)
            msg = f"DeepSeek-OCR-2 batch 处理失败: {error}"
            raise RuntimeError(msg)

        items_raw = resp.get("results", [])
        if not isinstance(items_raw, list):
            msg = (
                "DeepSeek-OCR-2 batch 返回 results 非列表: "
                f"{type(items_raw).__name__}"
            )
            raise RuntimeError(msg)

        results: dict[Path, PageOCR] = {}
        for idx, item in enumerate(items_raw):
            if not isinstance(item, dict) or idx >= len(chunk):
                continue
            image_path = chunk[idx]

            if not item.get("ok"):
                error = str(item.get("error", "未知错误"))
                if self._is_oom_error(error):
                    raise _OOMError(error)
                msg = (
                    f"DeepSeek-OCR-2 处理 {image_path.name} 失败: {error}"
                )
                raise RuntimeError(msg)

            fallback_ocr_dir = output_dir / f"{image_path.stem}_OCR"
            results[image_path] = self._parse_single_result(
                item, image_path, fallback_ocr_dir,
            )

        return results

    async def reocr_page(self, image_path: Path) -> str:
        """对整页图片重新 OCR，返回清洗后的 markdown（gap fill 用）。"""
        await self._recover_desync_if_needed()

        resp = await self._send_command({
            "cmd": "reocr_page",
            "image_path": str(image_path),
        })

        if not resp.get("ok"):
            error = resp.get("error", "未知错误")
            msg = f"DeepSeek-OCR-2 reocr_page 失败: {error}"
            raise RuntimeError(msg)

        return str(resp.get("raw_text", ""))

    def _parse_single_result(
        self,
        item: dict[str, object],
        image_path: Path,
        fallback_ocr_dir: Path,
    ) -> PageOCR:
        """从 worker 单张/批响应项构造 PageOCR（ocr/ocr_batch 共用）。"""
        raw_text = str(item.get("raw_text", ""))
        image_size_raw = item.get("image_size", [0, 0])
        if not isinstance(image_size_raw, list) or len(image_size_raw) < 2:
            image_size_raw = [0, 0]
        image_size: tuple[int, int] = (
            int(image_size_raw[0]),
            int(image_size_raw[1]),
        )
        has_eos = bool(item.get("has_eos", True))
        resp_ocr_dir = item.get("ocr_dir")
        actual_ocr_dir = (
            Path(str(resp_ocr_dir)) if resp_ocr_dir else fallback_ocr_dir
        )
        regions = self._parse_regions(item.get("regions", []), actual_ocr_dir)

        return PageOCR(
            image_path=image_path,
            image_size=image_size,
            raw_text=raw_text,
            regions=regions,
            output_dir=actual_ocr_dir,
            has_eos=has_eos,
        )

    @staticmethod
    def _is_oom_error(err: str) -> bool:
        """判定错误字符串是否为 CUDA OOM（触发 batch 降级重试）。"""
        low = err.lower()
        return (
            "out of memory" in low
            or "cuda out of memory" in low
            or "oom" in low
        )

    @staticmethod
    def _parse_regions(
        regions_data: object,
        ocr_dir: Path,
    ) -> list[Region]:
        """将 worker 返回的 regions JSON 转换为 Region 列表。"""
        if not isinstance(regions_data, list):
            return []

        regions: list[Region] = []
        for item in regions_data:
            if not isinstance(item, dict):
                continue
            bbox_raw = item.get("bbox", [0, 0, 0, 0])
            if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
                continue
            bbox = (
                int(bbox_raw[0]), int(bbox_raw[1]),
                int(bbox_raw[2]), int(bbox_raw[3]),
            )
            label = str(item.get("label", ""))
            cropped_path_str = item.get("cropped_path")
            cropped_path: Path | None = None
            if cropped_path_str is not None:
                cropped_path = ocr_dir / str(cropped_path_str)

            regions.append(Region(
                bbox=bbox,
                label=label,
                cropped_path=cropped_path,
            ))

        return regions

    async def _stream_stderr_progress(
        self,
        stop: asyncio.Event,
        on_progress: ProgressFn | None,
    ) -> None:
        """后台持续读取 worker stderr，提取进度推送给前端。"""
        if self._process is None or self._process.stderr is None:
            return
        stderr = self._process.stderr
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(
                    stderr.readline(), timeout=0.5,
                )
            except TimeoutError:
                continue
            except (OSError, asyncio.IncompleteReadError, RuntimeError):
                break
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            logger.debug("[deepseek-stderr] %s", line)
            if on_progress is not None:
                msg = _extract_stderr_message(line)
                if msg is not None:
                    on_progress(msg)
