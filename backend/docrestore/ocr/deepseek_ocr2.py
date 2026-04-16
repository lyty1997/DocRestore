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
from pathlib import Path

from docrestore.models import PageOCR, Region
from docrestore.ocr.base import (
    OCR_RESULT_FILENAME,
    ProgressFn,
    WorkerBackedOCREngine,
)

logger = logging.getLogger(__name__)


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
        env["CUDA_VISIBLE_DEVICES"] = self._config.gpu_id
        logger.info("DeepSeek worker GPU: %s", self._config.gpu_id)
        return env

    def _start_new_session(self) -> bool:
        # 独立进程组，方便 killpg 清理 vLLM 子进程
        return True

    def _build_init_cmd(self) -> dict[str, object]:
        return {
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
        }

    async def _send_init_command(
        self,
        init_cmd: dict[str, object],
        on_progress: ProgressFn | None,
    ) -> dict[str, object]:
        """并发读 stderr 推送初始化进度（vLLM 加载耗时长）。"""
        stop_event = asyncio.Event()
        stderr_task = asyncio.create_task(
            self._stream_stderr_progress(stop_event, on_progress),
        )
        try:
            resp = await self._send_command(init_cmd)
        finally:
            stop_event.set()
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
            error = resp.get("error", "未知错误")
            msg = f"DeepSeek-OCR-2 处理失败: {error}"
            raise RuntimeError(msg)

        raw_text = str(resp.get("raw_text", ""))
        image_size_raw = resp.get("image_size", [0, 0])
        if not isinstance(image_size_raw, list) or len(image_size_raw) < 2:
            image_size_raw = [0, 0]
        image_size: tuple[int, int] = (
            int(image_size_raw[0]),
            int(image_size_raw[1]),
        )
        has_eos = bool(resp.get("has_eos", True))
        resp_ocr_dir = resp.get("ocr_dir")
        actual_ocr_dir = Path(str(resp_ocr_dir)) if resp_ocr_dir else ocr_dir

        regions = self._parse_regions(resp.get("regions", []), actual_ocr_dir)

        return PageOCR(
            image_path=image_path,
            image_size=image_size,
            raw_text=raw_text,
            regions=regions,
            output_dir=actual_ocr_dir,
            has_eos=has_eos,
        )

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
