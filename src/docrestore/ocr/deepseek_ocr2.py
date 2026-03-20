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

"""DeepSeek-OCR-2 引擎实现

封装 vLLM AsyncLLMEngine，完成：
图片预处理 → 模型推理 → grounding 解析 → 图片裁剪 → 输出目录结构
"""

from __future__ import annotations

import ast
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from docrestore.models import PageOCR, Region
from docrestore.pipeline.config import OCRConfig

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """从 __file__ 向上查找 pyproject.toml 定位项目根目录。

    支持 DOCRESTORE_PROJECT_ROOT 环境变量覆盖。
    """
    env_root = os.environ.get("DOCRESTORE_PROJECT_ROOT")
    if env_root:
        return Path(env_root)

    current = Path(__file__).resolve().parent
    for parent in (current, *current.parents):
        if (parent / "pyproject.toml").exists():
            return parent

    msg = (
        "无法定位项目根目录（未找到 pyproject.toml）。"
        "请设置 DOCRESTORE_PROJECT_ROOT 环境变量。"
    )
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
            "请运行 ./scripts/setup.sh 完成安装。"
        )
        raise RuntimeError(msg)

    path_str = str(vendor_code_dir)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
        logger.info("已注入 vendor 路径: %s", path_str)


class DeepSeekOCR2Engine:
    """DeepSeek-OCR-2 OCR 引擎（vLLM AsyncLLMEngine）"""

    def __init__(self, config: OCRConfig) -> None:
        self._config = config
        self._engine: Any = None
        self._sampling_params: Any = None
        self._preprocessor: Any = None
        self._ready = False

    async def initialize(self) -> None:
        """加载模型到 GPU。

        延迟 import vLLM（必须在 VLLM_USE_V1=0 之后）。
        """
        os.environ["VLLM_USE_V1"] = "0"

        # 注入 vendor 路径（必须在 import deepseek_ocr2 之前）
        _inject_vendor_path()

        # 延迟 import，避免在无 GPU 环境报错
        from vllm import AsyncLLMEngine, SamplingParams
        from vllm.engine.arg_utils import AsyncEngineArgs
        from vllm.model_executor.models.registry import (
            ModelRegistry,
        )

        # 注册自定义模型（需要 DeepSeek-OCR-2 代码在 Python path 中）
        from deepseek_ocr2 import (
            DeepseekOCR2ForCausalLM,
        )

        ModelRegistry.register_model(
            "DeepseekOCR2ForCausalLM",
            DeepseekOCR2ForCausalLM,
        )

        from docrestore.ocr.ngram_filter import (
            NoRepeatNGramLogitsProcessor,
        )
        from docrestore.ocr.preprocessor import (
            ImagePreprocessor,
        )

        # 创建引擎
        engine_args = AsyncEngineArgs(
            model=self._config.model_path,
            hf_overrides={
                "architectures": [
                    "DeepseekOCR2ForCausalLM"
                ]
            },
            dtype="bfloat16",
            max_model_len=self._config.max_model_len,
            trust_remote_code=True,
            gpu_memory_utilization=self._config.gpu_memory_utilization,
        )
        self._engine = (
            AsyncLLMEngine.from_engine_args(engine_args)
        )

        # 创建 sampling params
        logits_processors = [
            NoRepeatNGramLogitsProcessor(
                ngram_size=self._config.ngram_size,
                window_size=self._config.ngram_window_size,
                whitelist_token_ids=self._config.ngram_whitelist_token_ids,
            )
        ]
        self._sampling_params = SamplingParams(
            temperature=0.0,
            max_tokens=self._config.max_tokens,
            logits_processors=logits_processors,
            skip_special_tokens=False,
        )

        # 创建预处理器
        self._preprocessor = ImagePreprocessor(
            model_path=self._config.model_path,
            base_size=self._config.base_size,
            crop_size=self._config.crop_size,
            min_crops=self._config.min_crops,
            max_crops=self._config.max_crops,
        )

        self._ready = True
        logger.info("DeepSeek-OCR-2 引擎初始化完成")

    async def ocr(
        self, image_path: Path, output_dir: Path
    ) -> PageOCR:
        """单张 OCR，结果写入 output_dir/{stem}_OCR/"""
        if self._engine is None or self._preprocessor is None:
            msg = "引擎未初始化，请先调用 initialize()"
            raise RuntimeError(msg)

        stem = image_path.stem
        ocr_dir = output_dir / f"{stem}_OCR"
        ocr_dir.mkdir(parents=True, exist_ok=True)
        images_dir = ocr_dir / "images"
        images_dir.mkdir(exist_ok=True)

        # 加载图片
        image = self._preprocessor.load_image(
            image_path
        )
        image_size = image.size

        # 预处理
        prompt = self._config.prompt
        image_features = self._preprocessor.preprocess(
            image, prompt
        )

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

        # 检查 eos
        has_eos = full_text.endswith("</s>") or (
            not full_text.endswith("...")
        )

        # 保存原始输出
        ori_path = ocr_dir / "result_ori.mmd"
        ori_path.write_text(full_text, encoding="utf-8")

        # grounding 解析 + 图片裁剪
        regions = self._parse_grounding(
            full_text, image, images_dir
        )

        # 生成 result.mmd（替换 grounding 标签）
        cleaned_text = self._replace_grounding_tags(
            full_text
        )
        result_path = ocr_dir / "result.mmd"
        result_path.write_text(
            cleaned_text, encoding="utf-8"
        )

        # 保存可视化
        self._save_visualization(
            image, full_text, ocr_dir
        )

        return PageOCR(
            image_path=image_path,
            image_size=image_size,
            raw_text=cleaned_text,
            regions=regions,
            output_dir=ocr_dir,
            has_eos=has_eos,
        )

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None]
        | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()"""
        results: list[PageOCR] = []
        total = len(image_paths)
        for i, path in enumerate(image_paths):
            page = await self.ocr(path, output_dir)
            results.append(page)
            if on_progress is not None:
                on_progress(i + 1, total)
        return results

    async def shutdown(self) -> None:
        """释放 GPU 资源"""
        self._engine = None
        self._sampling_params = None
        self._preprocessor = None
        self._ready = False
        logger.info("DeepSeek-OCR-2 引擎已关闭")

    @property
    def is_ready(self) -> bool:
        """引擎是否就绪"""
        return self._ready

    # --- 内部方法 ---

    @staticmethod
    def _parse_grounding(
        text: str,
        image: Image.Image,
        images_dir: Path,
    ) -> list[Region]:
        """解析 grounding 标签，裁剪 image 区域"""
        pattern = re.compile(
            r"<\|ref\|>(.*?)<\|/ref\|>"
            r"<\|det\|>(.*?)<\|/det\|>",
            re.DOTALL,
        )
        matches = pattern.findall(text)
        w, h = image.size

        regions: list[Region] = []
        img_idx = 0

        for label, coords_str in matches:
            try:
                coords_list = ast.literal_eval(coords_str)
            except (ValueError, SyntaxError):
                logger.warning(
                    "grounding 坐标解析失败: %s",
                    coords_str[:50],
                )
                continue

            for coords in coords_list:
                if len(coords) != 4:
                    continue
                x1 = int(coords[0] / 999 * w)
                y1 = int(coords[1] / 999 * h)
                x2 = int(coords[2] / 999 * w)
                y2 = int(coords[3] / 999 * h)
                bbox = (x1, y1, x2, y2)

                cropped_path: Path | None = None
                if label == "image":
                    try:
                        cropped = image.crop(bbox)
                        crop_file = (
                            images_dir / f"{img_idx}.jpg"
                        )
                        cropped.save(str(crop_file))
                        cropped_path = crop_file
                        img_idx += 1
                    except Exception:
                        logger.warning(
                            "图片裁剪失败: bbox=%s",
                            bbox,
                            exc_info=True,
                        )

                regions.append(
                    Region(
                        bbox=bbox,
                        label=label,
                        cropped_path=cropped_path,
                    )
                )

        return regions

    @staticmethod
    def _replace_grounding_tags(text: str) -> str:
        """替换 grounding 标签为 markdown 图片引用或删除"""
        # 先替换 image 类型的 grounding 为 ![](images/N.jpg)
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
        # 清理 coloneqq 等 LaTeX 替换
        return (
            result.replace("\\coloneqq", ":=")
            .replace("\\eqqcolon", "=:")
        )

    @staticmethod
    def _save_visualization(
        image: Image.Image,
        text: str,
        ocr_dir: Path,
    ) -> None:
        """保存带 bounding box 的可视化图片"""
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

                width = 4 if label == "title" else 2
                draw.rectangle(
                    [x1, y1, x2, y2],
                    outline="red",
                    width=width,
                )
                text_y = max(0, y1 - 15)
                draw.text(
                    (x1, text_y),
                    label,
                    font=font,
                    fill="red",
                )

        viz_path = ocr_dir / "result_with_boxes.jpg"
        img_draw.save(str(viz_path))
