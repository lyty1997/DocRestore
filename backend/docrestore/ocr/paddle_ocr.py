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

"""PaddleOCR 引擎实现（subprocess 调用独立 conda 环境）

通过 JSON Lines 协议与 scripts/paddle_ocr_worker.py 通信，
实现环境隔离（PaddleOCR 与 DeepSeek-OCR-2 的依赖不兼容）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from docrestore.models import PageOCR, Region, TextLine
from docrestore.ocr.base import (
    OCR_DEBUG_COORDS_FILENAME,
    OCR_RESULT_FILENAME,
    WorkerBackedOCREngine,
)
from docrestore.ocr.column_filter import ColumnFilter
from docrestore.pipeline.config import OCRConfig

logger = logging.getLogger(__name__)


def _parse_image_refs(markdown: str) -> list[Region]:
    """从 markdown 中解析图片引用，构造 Region 列表。

    匹配 ![...](images/N.jpg) 和 <img src="images/..." /> 两种格式。
    """
    regions: list[Region] = []

    # markdown 格式：![alt](images/N.jpg)
    md_pattern = re.compile(r"!\[[^\]]*\]\((images/[^)]+)\)")
    for match in md_pattern.finditer(markdown):
        regions.append(Region(
            bbox=(0, 0, 0, 0),
            label="image",
            cropped_path=Path(match.group(1)),
        ))

    # HTML 格式：<img src="images/..." />
    html_pattern = re.compile(r'<img\s+[^>]*src="(images/[^"]+)"')
    for match in html_pattern.finditer(markdown):
        regions.append(Region(
            bbox=(0, 0, 0, 0),
            label="image",
            cropped_path=Path(match.group(1)),
        ))

    return regions


class PaddleOCREngine(WorkerBackedOCREngine):
    """PaddleOCR 引擎（通过 subprocess 调用独立 conda 环境）"""

    engine_name = "PaddleOCR"
    worker_script_path = "scripts/paddle_ocr_worker.py"

    def __init__(self, config: OCRConfig) -> None:
        super().__init__(config)
        self._ocr_count = 0  # 已处理图片计数（重启阈值依据）
        self._column_filter = ColumnFilter(
            min_sidebar_count=config.column_filter_min_sidebar,
            thresholds=config.column_filter_thresholds,
        ) if config.enable_column_filter else None

    # ── 基类钩子实现 ──────────────────────────────────────

    def _get_python_path(self) -> str:
        return self._config.paddle_python

    def _get_timeout(self) -> int:
        return self._config.paddle_ocr_timeout

    def _resolve_worker_script(self) -> str:
        return (
            self._config.paddle_worker_script
            or self.worker_script_path
        )

    def _build_subprocess_env(self) -> dict[str, str]:
        env = {**os.environ}

        # 确保 worker 子进程的 localhost 请求不经过代理
        no_proxy = env.get("no_proxy", "")
        for host in ("localhost", "127.0.0.1"):
            if host not in no_proxy:
                no_proxy = f"{host},{no_proxy}" if no_proxy else host
        env["no_proxy"] = no_proxy
        env["NO_PROXY"] = no_proxy

        # 与 ppocr-server 使用同一块 GPU
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        # gpu_id=None 兜底：pipeline 直接调 create_engine 时没走 engine_manager
        from docrestore.ocr.gpu_detect import pick_best_gpu
        env["CUDA_VISIBLE_DEVICES"] = (
            self._config.gpu_id or pick_best_gpu() or "0"
        )
        return env

    def _build_init_cmd(self) -> dict[str, object]:
        init_cmd: dict[str, object] = {
            "cmd": "initialize",
            "pipeline": self._config.paddle_pipeline,
        }
        # vl 模式才需要 server_url（basic 不依赖 vllm-server）
        if self._config.paddle_pipeline == "vl" and self._config.paddle_server_url:
            init_cmd["server_url"] = self._config.paddle_server_url
            init_cmd["server_model_name"] = (
                self._config.paddle_server_model_name
            )
        return init_cmd

    async def _terminate_process(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            await asyncio.wait_for(
                self._process.wait(),
                timeout=self._config.worker_terminate_timeout,
            )
        except (TimeoutError, ProcessLookupError, OSError):
            if self._process.returncode is None:
                self._process.kill()
                await self._process.wait()

    async def _restart_worker(self) -> None:
        """重启 worker 并重置 _ocr_count。"""
        await super()._restart_worker()
        self._ocr_count = 0

    # ── OCR 主流程 ────────────────────────────────────────

    async def ocr(  # noqa: C901
        self, image_path: Path, output_dir: Path
    ) -> PageOCR:
        """单张 OCR，结果写入 output_dir/{stem}_OCR/"""
        await self._resync_if_needed()

        # 增量OCR：优先检查裁剪后版本，再检查原始版本
        cropped_ocr_dir = output_dir / f"{image_path.stem}_cropped_OCR"
        cropped_mmd = cropped_ocr_dir / OCR_RESULT_FILENAME
        ocr_dir = output_dir / f"{image_path.stem}_OCR"
        result_mmd = ocr_dir / OCR_RESULT_FILENAME
        if cropped_mmd.exists():
            logger.info("跳过已有OCR结果(cropped): %s", image_path.name)
            return await self._load_existing_ocr(image_path, cropped_ocr_dir)
        if result_mmd.exists():
            logger.info("跳过已有OCR结果: %s", image_path.name)
            return await self._load_existing_ocr(image_path, ocr_dir)

        # 定期重启 worker（防止显存累积）
        restart_interval = self._config.paddle_restart_interval
        if restart_interval > 0 and self._ocr_count >= restart_interval:
            await self._restart_worker()

        # 执行 OCR，超时时重启并重试一次
        try:
            resp = await self._send_ocr_cmd(image_path, output_dir)
        except RuntimeError as e:
            if "响应超时" in str(e):
                logger.warning(
                    "OCR 超时，重启 worker 并重试: %s", image_path.name,
                )
                await self._restart_worker()
                resp = await self._send_ocr_cmd(image_path, output_dir)
            else:
                raise

        if not resp.get("ok"):
            error = resp.get("error", "未知错误")
            msg = f"PaddleOCR 处理失败: {error}"
            raise RuntimeError(msg)

        self._ocr_count += 1

        raw_text = str(resp.get("raw_text", ""))
        image_size = self._parse_image_size(resp.get("image_size", [0, 0]))
        text_lines = self._parse_text_lines(resp.get("text_lines", []))

        # 处理坐标并检测侧栏
        coordinates_raw = resp.get("coordinates", [])
        has_sidebar = False
        crop_box_norm = None
        if isinstance(coordinates_raw, list) and self._column_filter:
            normalized = self._normalize_coordinates(
                coordinates_raw, image_size
            )
            if normalized:
                # 保存归一化坐标到 debug 文件，方便排查侧栏检测
                self._dump_coordinates(
                    ocr_dir, image_path.name, normalized,
                )
                has_sidebar, crop_box_norm = self._detect_sidebar(
                    normalized, image_path.name,
                )

        # 如果检测到侧栏，裁剪重跑
        if has_sidebar and crop_box_norm:
            return await self._crop_and_reocr(
                image_path, output_dir, image_size, crop_box_norm,
            )

        # 从 markdown 解析图片引用
        regions = _parse_image_refs(raw_text)
        # 补全 cropped_path 为绝对路径
        for region in regions:
            if region.cropped_path is not None:
                region.cropped_path = ocr_dir / region.cropped_path

        return PageOCR(
            image_path=image_path,
            image_size=image_size,
            raw_text=raw_text,
            regions=regions,
            output_dir=ocr_dir,
            has_eos=True,
            text_lines=text_lines,
        )

    async def _send_ocr_cmd(
        self, image_path: Path, output_dir: Path,
    ) -> dict[str, object]:
        """发送 ocr 命令。"""
        return await self._send_command({
            "cmd": "ocr",
            "image_path": str(image_path),
            "output_dir": str(output_dir),
            "min_image_size": self._config.paddle_min_image_size,
        })

    @staticmethod
    def _parse_image_size(
        raw: object,
    ) -> tuple[int, int]:
        """解析 worker 返回的 image_size。"""
        if not isinstance(raw, list) or len(raw) < 2:
            return (0, 0)
        return (int(raw[0]), int(raw[1]))

    @staticmethod
    def _parse_text_lines(raw: object) -> list[TextLine]:
        """basic pipeline 返回的行级 [{bbox, text, score}] 反序列化为 TextLine。

        vl pipeline 返回 None/[]，输出仍是空 list。
        """
        if not isinstance(raw, list) or not raw:
            return []
        out: list[TextLine] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox")
            if not isinstance(bbox, list) or len(bbox) < 4:
                continue
            try:
                x1, y1, x2, y2 = (int(v) for v in bbox[:4])
            except (TypeError, ValueError):
                continue
            out.append(TextLine(
                bbox=(x1, y1, x2, y2),
                text=str(item.get("text", "")),
                score=float(item.get("score", 0.0) or 0.0),
            ))
        return out

    # ── 侧栏检测与裁剪重跑 ──────────────────────────────

    @staticmethod
    def _dump_coordinates(
        ocr_dir: Path,
        image_name: str,
        normalized: list[dict[str, object]],
    ) -> None:
        """将归一化坐标写入 OCR 目录的 debug coords 文件，方便排查。"""
        try:
            ocr_dir.mkdir(parents=True, exist_ok=True)
            dump_path = ocr_dir / OCR_DEBUG_COORDS_FILENAME
            with open(dump_path, "w", encoding="utf-8") as f:
                for c in normalized:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
            logger.debug(
                "坐标已保存: %s (%d 个)", dump_path, len(normalized),
            )
        except OSError:
            logger.debug("保存坐标失败: %s", image_name)

    def _normalize_coordinates(
        self,
        coordinates: list[dict[str, object]],
        image_size: tuple[int, int],
    ) -> list[dict[str, object]]:
        """将像素坐标归一化到 [0, coord_range]。"""
        if not coordinates or image_size[0] == 0 or image_size[1] == 0:
            return []

        width, height = image_size
        coord_range = self._config.column_filter_thresholds.coord_range
        normalized: list[dict[str, object]] = []

        for coord in coordinates:
            bbox = coord.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue

            x1, y1, x2, y2 = bbox
            normalized.append({
                "label": coord.get("label", "text"),
                "x1": int(x1 * coord_range / width),
                "y1": int(y1 * coord_range / height),
                "x2": int(x2 * coord_range / width),
                "y2": int(y2 * coord_range / height),
                "text": coord.get("text", ""),
            })

        return normalized

    def _detect_sidebar(
        self,
        normalized_coords: list[dict[str, object]],
        image_name: str,
    ) -> tuple[bool, tuple[int, int, int, int] | None]:
        """检测侧栏并返回裁剪框。

        Returns:
            (has_sidebar, crop_box)
            crop_box: (x1, y1, x2, y2) 归一化坐标，None 表示无需裁剪
        """
        if not self._column_filter:
            return (False, None)

        # 转换为 GroundingRegion 格式
        from docrestore.ocr.column_filter import GroundingRegion

        regions: list[GroundingRegion] = []
        for c in normalized_coords:
            x1_raw, y1_raw = c.get("x1", 0), c.get("y1", 0)
            x2_raw, y2_raw = c.get("x2", 0), c.get("y2", 0)
            if not (
                isinstance(x1_raw, int) and isinstance(y1_raw, int)
                and isinstance(x2_raw, int) and isinstance(y2_raw, int)
            ):
                continue
            x1, y1, x2, y2 = x1_raw, y1_raw, x2_raw, y2_raw

            regions.append(
                GroundingRegion(
                    label=str(c.get("label", "text")),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    text=str(c.get("text", "")),
                    raw_block="",
                )
            )

        # debug: 输出归一化坐标，方便排查侧栏检测
        logger.debug(
            "%s: %d 个区域的归一化坐标:",
            image_name, len(regions),
        )
        for r in regions:
            logger.debug(
                "  [%s] x1=%d y1=%d x2=%d y2=%d w=%d | %.20s",
                r.label, r.x1, r.y1, r.x2, r.y2,
                r.x2 - r.x1, r.text,
            )

        boundaries = self._column_filter.detect_boundaries(regions)
        coord_range = self._config.column_filter_thresholds.coord_range
        if boundaries.has_sidebar:
            # 主内容区域：左边界到右边界之间
            # left_boundary=0 → 无左侧栏；right_boundary=coord_range → 无右侧栏
            main_left = boundaries.left_boundary
            main_right = boundaries.right_boundary

            # 只有主内容区域小于全图时才裁剪
            if main_left > 0 or main_right < coord_range:
                logger.warning(
                    "%s: 检测到侧栏 (左边界=%d, 右边界=%d)，将裁剪重跑",
                    image_name, main_left, main_right,
                )
                crop_box = (main_left, 0, main_right, coord_range)
                return (True, crop_box)

        return (False, None)

    async def _crop_and_reocr(
        self,
        image_path: Path,
        output_dir: Path,
        image_size: tuple[int, int],
        crop_box_norm: tuple[int, int, int, int],
    ) -> PageOCR:
        """裁剪图片并重新 OCR。"""
        from PIL import Image

        # 归一化坐标 → 像素坐标
        width, height = image_size
        coord_range = self._config.column_filter_thresholds.coord_range
        x1 = int(crop_box_norm[0] * width / coord_range)
        y1 = int(crop_box_norm[1] * height / coord_range)
        x2 = int(crop_box_norm[2] * width / coord_range)
        y2 = int(crop_box_norm[3] * height / coord_range)

        img = await asyncio.to_thread(Image.open, image_path)
        cropped = img.crop((x1, y1, x2, y2))

        cropped_path = (
            output_dir / f"{image_path.stem}_cropped{image_path.suffix}"
        )
        await asyncio.to_thread(cropped.save, cropped_path)

        logger.info(
            "裁剪图片: %s → %s", image_path.name, cropped_path.name,
        )

        resp = await self._send_ocr_cmd(cropped_path, output_dir)

        if not resp.get("ok"):
            error = resp.get("error", "未知错误")
            msg = f"PaddleOCR 裁剪重跑失败: {error}"
            raise RuntimeError(msg)

        self._ocr_count += 1

        raw_text = str(resp.get("raw_text", ""))
        # 使用裁剪后的图片名称构造 OCR 目录
        ocr_dir = output_dir / f"{cropped_path.stem}_OCR"

        regions = _parse_image_refs(raw_text)
        for region in regions:
            if region.cropped_path is not None:
                region.cropped_path = ocr_dir / region.cropped_path

        return PageOCR(
            image_path=image_path,
            image_size=image_size,
            raw_text=raw_text,
            regions=regions,
            output_dir=ocr_dir,
            has_eos=True,
        )
