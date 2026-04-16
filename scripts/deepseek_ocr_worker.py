#!/usr/bin/env python3
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

"""DeepSeek-OCR-2 worker — 在 deepseek_ocr conda 环境中运行

通信协议（JSON Lines over stdin/stdout）：
  请求: {"cmd": "initialize", "model_path": "...", ...}
      | {"cmd": "ocr", "image_path": "...", "output_dir": "...", ...}
      | {"cmd": "reocr_page", "image_path": "..."}
      | {"cmd": "shutdown"}
  响应: {"ok": true, ...}
      | {"ok": false, "error": "..."}

注意：所有日志输出到 stderr，stdout 专用于 JSON 协议通信。
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# 日志输出到 stderr
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[deepseek-worker] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _send(data: dict[str, object]) -> None:
    """向 stdout 写一行 JSON。"""
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _recv() -> dict[str, object] | None:
    """从 stdin 读一行 JSON，EOF 时返回 None。"""
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)  # type: ignore[no-any-return]


def _find_project_root() -> Path:
    """从脚本位置向上查找 pyproject.toml 定位项目根目录。"""
    env_root = os.environ.get("DOCRESTORE_PROJECT_ROOT")
    if env_root:
        return Path(env_root)

    current = Path(__file__).resolve().parent
    for parent in (current, *current.parents):
        if (parent / "pyproject.toml").exists():
            return parent

    msg = "无法定位项目根目录（未找到 pyproject.toml）"
    raise RuntimeError(msg)


def _inject_vendor_path() -> None:
    """将 DeepSeek-OCR-2 vendor 代码注入 sys.path。"""
    root = _find_project_root()
    vendor_code_dir = (
        root
        / "vendor"
        / "DeepSeek-OCR-2"
        / "DeepSeek-OCR2-master"
        / "DeepSeek-OCR2-vllm"
    )
    if not vendor_code_dir.exists():
        msg = (
            f"DeepSeek-OCR-2 vendor 代码不存在: {vendor_code_dir}\n"
            "请运行 ./scripts/setup_deepseek_ocr.sh 完成安装。"
        )
        raise RuntimeError(msg)

    path_str = str(vendor_code_dir)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        logger.info("已注入 vendor 路径: %s", path_str)


class Worker:
    """DeepSeek-OCR-2 worker 主类。"""

    def __init__(self) -> None:
        self._engine: Any = None
        self._sampling_params: Any = None
        self._preprocessor: Any = None
        self._config: dict[str, Any] = {}

    async def handle_initialize(
        self, config: dict[str, Any]
    ) -> dict[str, object]:
        """初始化 vLLM 引擎 + 预处理器。"""
        try:
            os.environ["VLLM_USE_V1"] = "0"
            _inject_vendor_path()

            from vllm import AsyncLLMEngine, SamplingParams
            from vllm.engine.arg_utils import AsyncEngineArgs
            from vllm.model_executor.models.registry import ModelRegistry

            from deepseek_ocr2 import DeepseekOCR2ForCausalLM

            ModelRegistry.register_model(
                "DeepseekOCR2ForCausalLM",
                DeepseekOCR2ForCausalLM,
            )

            # 添加项目 backend 到 sys.path（以便 import docrestore 模块）
            root = _find_project_root()
            backend_path = str(root / "backend")
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)

            from docrestore.ocr.ngram_filter import NoRepeatNGramLogitsProcessor
            from docrestore.ocr.preprocessor import ImagePreprocessor

            self._config = config
            model_path = config.get("model_path", "models/DeepSeek-OCR-2")

            # 基础 vLLM engine 参数
            engine_kwargs: dict[str, Any] = {
                "model": model_path,
                "hf_overrides": {
                    "architectures": ["DeepseekOCR2ForCausalLM"]
                },
                "dtype": "bfloat16",
                "max_model_len": int(config.get("max_model_len", 8192)),
                "trust_remote_code": True,
                "gpu_memory_utilization": float(
                    config.get("gpu_memory_utilization", 0.75)
                ),
            }

            # 两引擎共有的 vLLM 优化参数（None/False 表示沿用 vLLM 默认值）
            enforce_eager = config.get("vllm_enforce_eager")
            if enforce_eager is not None:
                engine_kwargs["enforce_eager"] = bool(enforce_eager)

            block_size = config.get("vllm_block_size")
            if block_size is not None:
                engine_kwargs["block_size"] = int(block_size)

            swap_space = config.get("vllm_swap_space_gb")
            if swap_space is not None:
                engine_kwargs["swap_space"] = float(swap_space)

            if bool(config.get("vllm_disable_mm_preprocessor_cache", False)):
                engine_kwargs["disable_mm_preprocessor_cache"] = True

            if bool(config.get("vllm_disable_log_stats", False)):
                engine_kwargs["disable_log_stats"] = True

            engine_args = AsyncEngineArgs(**engine_kwargs)
            self._engine = AsyncLLMEngine.from_engine_args(engine_args)

            ngram_size = int(config.get("ngram_size", 20))
            ngram_window = int(config.get("ngram_window_size", 90))
            whitelist_raw = config.get(
                "ngram_whitelist_token_ids", [128821, 128822]
            )
            whitelist_ids = {
                int(t) for t in whitelist_raw
            } if isinstance(whitelist_raw, list) else {128821, 128822}
            logits_processors = [
                NoRepeatNGramLogitsProcessor(
                    ngram_size=ngram_size,
                    window_size=ngram_window,
                    whitelist_token_ids=whitelist_ids,
                )
            ]
            self._sampling_params = SamplingParams(
                temperature=0.0,
                max_tokens=int(config.get("max_tokens", 8192)),
                logits_processors=logits_processors,
                skip_special_tokens=False,
            )

            def _to_triplet(
                raw: object,
            ) -> tuple[float, float, float]:
                if isinstance(raw, list) and len(raw) == 3:
                    return (float(raw[0]), float(raw[1]), float(raw[2]))
                return (0.5, 0.5, 0.5)

            normalize_mean = _to_triplet(config.get("normalize_mean"))
            normalize_std = _to_triplet(config.get("normalize_std"))

            self._preprocessor = ImagePreprocessor(
                model_path=model_path,
                base_size=int(config.get("base_size", 1024)),
                crop_size=int(config.get("crop_size", 768)),
                min_crops=int(config.get("min_crops", 2)),
                max_crops=int(config.get("max_crops", 6)),
                normalize_mean=normalize_mean,
                normalize_std=normalize_std,
            )

            logger.info("DeepSeek-OCR-2 引擎初始化完成")
            return {"ok": True}
        except Exception as exc:
            logger.exception("初始化失败")
            return {"ok": False, "error": str(exc)}

    async def handle_ocr(
        self,
        image_path: str,
        output_dir: str,
        enable_column_filter: bool = False,
        column_filter_min_sidebar: int = 5,
    ) -> dict[str, object]:
        """对单张图片执行 OCR。"""
        if self._engine is None or self._preprocessor is None:
            return {"ok": False, "error": "引擎未初始化"}

        try:
            img_path = Path(image_path)
            out_dir = Path(output_dir)
            stem = img_path.stem

            ocr_dir = out_dir / f"{stem}_OCR"
            ocr_dir.mkdir(parents=True, exist_ok=True)
            images_dir = ocr_dir / "images"
            images_dir.mkdir(exist_ok=True)

            # 加载图片 + 预处理
            image = self._preprocessor.load_image(img_path)
            image_size = image.size
            prompt = str(self._config.get(
                "prompt",
                "<image>\nFree OCR.\n<|grounding|>Convert the document to markdown.",
            ))
            image_features = self._preprocessor.preprocess(image, prompt)

            # 推理
            request: dict[str, Any] = {
                "prompt": prompt,
                "multi_modal_data": {"image": image_features},
            }
            request_id = f"req-{stem}-{int(time.time())}"

            full_text = ""
            async for output in self._engine.generate(
                request, self._sampling_params, request_id
            ):
                if output.outputs:
                    full_text = output.outputs[0].text

            has_eos = full_text.endswith("</s>") or (
                not full_text.endswith("...")
            )

            # 保存原始输出
            ori_path = ocr_dir / "result_ori.mmd"
            ori_path.write_text(full_text, encoding="utf-8")

            # 侧栏检测与过滤
            if enable_column_filter:
                full_text, image, image_size = await self._apply_column_filter(
                    full_text, image, stem, ocr_dir,
                    column_filter_min_sidebar,
                )

            # grounding 解析 + 图片裁剪
            regions = self._parse_grounding(full_text, image, images_dir)

            # 生成 result.mmd
            cleaned_text = self._replace_grounding_tags(full_text)
            result_path = ocr_dir / "result.mmd"
            result_path.write_text(cleaned_text, encoding="utf-8")

            # 保存可视化
            self._save_visualization(image, full_text, ocr_dir)

            # 序列化 regions
            regions_data: list[dict[str, object]] = []
            for r in regions:
                rd: dict[str, object] = {
                    "bbox": list(r["bbox"]),
                    "label": r["label"],
                }
                if r.get("cropped_path") is not None:
                    # 返回相对于 ocr_dir 的路径
                    rd["cropped_path"] = str(
                        Path(r["cropped_path"]).relative_to(ocr_dir)
                    )
                regions_data.append(rd)

            return {
                "ok": True,
                "raw_text": cleaned_text,
                "image_size": list(image_size),
                "regions": regions_data,
                "has_eos": has_eos,
                "ocr_dir": str(ocr_dir),
            }
        except Exception as exc:
            logger.exception("OCR 失败: %s", image_path)
            return {"ok": False, "error": str(exc)}

    async def handle_reocr_page(
        self, image_path: str
    ) -> dict[str, object]:
        """对整页图片重新 OCR，返回清洗后的 markdown（gap fill 用）。"""
        if self._engine is None or self._preprocessor is None:
            return {"ok": False, "error": "引擎未初始化"}

        try:
            img_path = Path(image_path)
            image = self._preprocessor.load_image(img_path)
            raw_text = await self._reocr(image, img_path.stem)
            cleaned = self._replace_grounding_tags(raw_text)
            return {"ok": True, "raw_text": cleaned}
        except Exception as exc:
            logger.exception("reocr_page 失败: %s", image_path)
            return {"ok": False, "error": str(exc)}

    def handle_shutdown(self) -> dict[str, object]:
        """释放引擎资源。"""
        self._engine = None
        self._sampling_params = None
        self._preprocessor = None
        logger.info("DeepSeek-OCR-2 worker 已关闭")
        return {"ok": True}

    # --- 内部方法 ---

    async def _reocr(self, image: Any, stem: str) -> str:
        """对图片重新跑 OCR 推理，返回原始文本。"""
        prompt = str(self._config.get(
            "prompt",
            "<image>\nFree OCR.\n<|grounding|>Convert the document to markdown.",
        ))
        image_features = self._preprocessor.preprocess(image, prompt)

        request: dict[str, Any] = {
            "prompt": prompt,
            "multi_modal_data": {"image": image_features},
        }
        request_id = f"req-{stem}-reocr-{int(time.time())}"

        full_text = ""
        async for output in self._engine.generate(
            request, self._sampling_params, request_id
        ):
            if output.outputs:
                full_text = output.outputs[0].text

        return full_text

    async def _apply_column_filter(
        self,
        full_text: str,
        image: Any,
        stem: str,
        ocr_dir: Path,
        min_sidebar_count: int,
    ) -> tuple[str, Any, tuple[int, int]]:
        """侧栏检测与过滤。"""
        from docrestore.ocr.column_filter import ColumnFilter

        col_filter = ColumnFilter(min_sidebar_count=min_sidebar_count)
        grounding_regions = col_filter.parse_grounding_regions(full_text)
        boundaries = col_filter.detect_boundaries(grounding_regions)

        if not boundaries.has_sidebar:
            return full_text, image, image.size

        content_regions = col_filter.filter_regions(
            grounding_regions, boundaries
        )

        logger.info(
            "%s: 侧栏检测 — 总区域 %d, 正文 %d, 左边界 %d, 右边界 %d",
            stem, len(grounding_regions), len(content_regions),
            boundaries.left_boundary, boundaries.right_boundary,
        )

        if col_filter.needs_reocr(
            len(grounding_regions), len(content_regions)
        ):
            crop_box = col_filter.compute_crop_box(
                boundaries, *image.size
            )
            cropped = image.crop(crop_box)
            logger.info(
                "%s: 正文占比异常，裁剪重跑 OCR, crop_box=%s",
                stem, crop_box,
            )
            new_text = await self._reocr(cropped, stem)
            reocr_path = ocr_dir / "result_ori_reocr.mmd"
            reocr_path.write_text(new_text, encoding="utf-8")
            return new_text, cropped, cropped.size

        rebuilt = col_filter.rebuild_text(content_regions)
        filtered_path = ocr_dir / "result_ori_filtered.mmd"
        filtered_path.write_text(rebuilt, encoding="utf-8")
        return rebuilt, image, image.size

    @staticmethod
    def _parse_grounding(
        text: str,
        image: Any,
        images_dir: Path,
    ) -> list[dict[str, Any]]:
        """解析 grounding 标签，裁剪 image 区域。

        返回 dict 列表（非 Region dataclass，便于 JSON 序列化）。
        """
        pattern = re.compile(
            r"<\|ref\|>(.*?)<\|/ref\|>"
            r"<\|det\|>(.*?)<\|/det\|>",
            re.DOTALL,
        )
        matches = pattern.findall(text)
        w, h = image.size

        regions: list[dict[str, Any]] = []
        img_idx = 0

        for label, coords_str in matches:
            try:
                coords_list = ast.literal_eval(coords_str)
            except (ValueError, SyntaxError):
                logger.warning("grounding 坐标解析失败: %s", coords_str[:50])
                continue

            for coords in coords_list:
                if len(coords) != 4:
                    continue
                x1 = int(coords[0] / 999 * w)
                y1 = int(coords[1] / 999 * h)
                x2 = int(coords[2] / 999 * w)
                y2 = int(coords[3] / 999 * h)
                bbox = (x1, y1, x2, y2)

                cropped_path: str | None = None
                if label == "image":
                    try:
                        cropped = image.crop(bbox)
                        crop_file = images_dir / f"{img_idx}.jpg"
                        cropped.save(str(crop_file))
                        cropped_path = str(crop_file)
                        img_idx += 1
                    except Exception:
                        logger.warning(
                            "图片裁剪失败: bbox=%s", bbox, exc_info=True,
                        )

                regions.append({
                    "bbox": bbox,
                    "label": label,
                    "cropped_path": cropped_path,
                })

        return regions

    @staticmethod
    def _replace_grounding_tags(text: str) -> str:
        """替换 grounding 标签为 markdown 图片引用或删除。"""
        img_idx = 0

        def _replace_image(m: re.Match[str]) -> str:
            nonlocal img_idx
            label = m.group(1)
            if label == "image":
                result = f"![](images/{img_idx}.jpg)\n"
                img_idx += 1
                return result
            return ""

        pattern = re.compile(
            r"<\|ref\|>(.*?)<\|/ref\|>"
            r"<\|det\|>.*?<\|/det\|>",
            re.DOTALL,
        )
        result = pattern.sub(_replace_image, text)
        return (
            result.replace("\\coloneqq", ":=")
            .replace("\\eqqcolon", "=:")
        )

    @staticmethod
    def _save_visualization(
        image: Any,
        text: str,
        ocr_dir: Path,
    ) -> None:
        """保存带 bounding box 的可视化图片。"""
        from PIL import ImageDraw, ImageFont

        pattern = re.compile(
            r"<\|ref\|>(.*?)<\|/ref\|>"
            r"<\|det\|>(.*?)<\|/det\|>",
            re.DOTALL,
        )
        matches = pattern.findall(text)
        if not matches:
            return

        w, h = image.size
        img_draw = image.copy()
        draw = ImageDraw.Draw(img_draw)
        font = ImageFont.load_default()

        for label, coords_str in matches:
            try:
                coords_list = ast.literal_eval(coords_str)
            except (ValueError, SyntaxError):
                continue

            for coords in coords_list:
                if len(coords) != 4:
                    continue
                x1 = int(coords[0] / 999 * w)
                y1 = int(coords[1] / 999 * h)
                x2 = int(coords[2] / 999 * w)
                y2 = int(coords[3] / 999 * h)

                x1, x2 = min(x1, x2), max(x1, x2)
                y1, y2 = min(y1, y2), max(y1, y2)

                width = 4 if label == "title" else 2
                draw.rectangle(
                    [x1, y1, x2, y2], outline="red", width=width,
                )
                text_y = max(0, y1 - 15)
                draw.text(
                    (x1, text_y), label, font=font, fill="red",
                )

        viz_path = ocr_dir / "result_with_boxes.jpg"
        img_draw.save(str(viz_path))


def main() -> None:
    """Worker 主循环。"""
    worker = Worker()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        while True:
            request = _recv()
            if request is None:
                break

            cmd = request.get("cmd", "")

            if cmd == "initialize":
                config = {
                    k: v for k, v in request.items() if k != "cmd"
                }
                _send(loop.run_until_complete(
                    worker.handle_initialize(config)
                ))
            elif cmd == "ocr":
                _send(loop.run_until_complete(
                    worker.handle_ocr(
                        image_path=str(request.get("image_path", "")),
                        output_dir=str(request.get("output_dir", "")),
                        enable_column_filter=bool(
                            request.get("enable_column_filter", False)
                        ),
                        column_filter_min_sidebar=int(
                            str(request.get("column_filter_min_sidebar", 5))
                        ),
                    )
                ))
            elif cmd == "reocr_page":
                _send(loop.run_until_complete(
                    worker.handle_reocr_page(
                        image_path=str(request.get("image_path", "")),
                    )
                ))
            elif cmd == "shutdown":
                _send(worker.handle_shutdown())
                break
            else:
                _send({"ok": False, "error": f"未知命令: {cmd}"})
    finally:
        loop.close()


if __name__ == "__main__":
    main()
