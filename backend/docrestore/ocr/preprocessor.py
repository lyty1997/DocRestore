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

"""图片预处理（动态分辨率 + tile 切分）

从 DeepSeek-OCR-2 提取核心逻辑，去除全局变量依赖，参数化配置。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, cast

import torch
import torchvision.transforms as T
from PIL import Image, ImageOps
from transformers import AutoTokenizer, PreTrainedTokenizerBase


def _find_closest_aspect_ratio(
    aspect_ratio: float,
    target_ratios: list[tuple[int, int]],
    width: int,
    height: int,
    image_size: int,
) -> tuple[int, int]:
    """找到最接近原图宽高比的 tile 网格方案"""
    best_ratio_diff = float("inf")
    best_ratio = (1, 1)
    area = width * height
    for ratio in target_ratios:
        target_ar = ratio[0] / ratio[1]
        ratio_diff = abs(aspect_ratio - target_ar)
        if ratio_diff < best_ratio_diff:
            best_ratio_diff = ratio_diff
            best_ratio = ratio
        elif ratio_diff == best_ratio_diff:
            if area > 0.5 * image_size * image_size * ratio[0] * ratio[1]:
                best_ratio = ratio
    return best_ratio


def _count_tiles(
    orig_width: int,
    orig_height: int,
    min_crops: int,
    max_crops: int,
    image_size: int,
) -> tuple[int, int]:
    """计算最佳 tile 网格"""
    aspect_ratio = orig_width / orig_height
    target_ratios = sorted(
        {
            (i, j)
            for n in range(min_crops, max_crops + 1)
            for i in range(1, n + 1)
            for j in range(1, n + 1)
            if min_crops <= i * j <= max_crops
        },
        key=lambda x: x[0] * x[1],
    )
    return _find_closest_aspect_ratio(
        aspect_ratio,
        target_ratios,
        orig_width,
        orig_height,
        image_size,
    )


def _dynamic_preprocess(
    image: Image.Image,
    min_crops: int,
    max_crops: int,
    image_size: int,
) -> tuple[list[Image.Image], tuple[int, int]]:
    """动态裁切：resize 到最佳网格尺寸，按 tile 切分"""
    orig_width, orig_height = image.size
    ratio = _count_tiles(
        orig_width, orig_height, min_crops, max_crops, image_size
    )
    target_width = image_size * ratio[0]
    target_height = image_size * ratio[1]
    blocks = ratio[0] * ratio[1]

    resized = image.resize((target_width, target_height))
    tiles: list[Image.Image] = []
    cols = target_width // image_size
    for i in range(blocks):
        x0 = (i % cols) * image_size
        y0 = (i // cols) * image_size
        tiles.append(
            resized.crop(
                (x0, y0, x0 + image_size, y0 + image_size)
            )
        )
    return tiles, ratio


class _ImageTransform:
    """ToTensor + Normalize"""

    def __init__(
        self,
        mean: tuple[float, float, float] = (0.5, 0.5, 0.5),
        std: tuple[float, float, float] = (0.5, 0.5, 0.5),
    ) -> None:
        self.mean = mean
        self.std = std
        self.transform = T.Compose(
            [T.ToTensor(), T.Normalize(mean, std)]
        )

    def __call__(
        self, pil_img: Image.Image
    ) -> torch.Tensor:
        """PIL Image → normalized tensor"""
        result: torch.Tensor = self.transform(pil_img)
        return result


class ImagePreprocessor:
    """图片预处理器：global view + local tiles + token 序列构造

    从 DeepSeek-OCR-2 的 DeepseekOCR2Processor 提取，
    去除全局变量依赖，prompt 参数化。
    """

    def __init__(
        self,
        model_path: str,
        base_size: int = 1024,
        crop_size: int = 768,
        min_crops: int = 2,
        max_crops: int = 6,
        patch_size: int = 16,
        downsample_ratio: int = 4,
        revision: str | None = None,
        normalize_mean: tuple[float, float, float] = (0.5, 0.5, 0.5),
        normalize_std: tuple[float, float, float] = (0.5, 0.5, 0.5),
    ) -> None:
        self.base_size = base_size
        self.crop_size = crop_size
        self.min_crops = min_crops
        self.max_crops = max_crops
        self.patch_size = patch_size
        self.downsample_ratio = downsample_ratio

        self._transform = _ImageTransform(
            mean=normalize_mean, std=normalize_std,
        )

        # 加载 tokenizer（复用模型自带的）
        # 本地路径时 revision 无效，从 HuggingFace 下载时固定版本
        tokenizer = cast(
            PreTrainedTokenizerBase,
            AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True,
                revision=revision,
            ),
        )
        self._tokenizer = tokenizer
        self._tokenizer.padding_side = "left"

        # image token id
        self._image_token = "<image>"  # noqa: S105
        vocab = self._tokenizer.get_vocab()
        self._image_token_id = int(vocab[self._image_token])
        self._pad_token = "<｜▁pad▁｜>"  # noqa: S105
        if self._tokenizer.pad_token is None:
            self._tokenizer.add_special_tokens(
                {"pad_token": self._pad_token}
            )

    @property
    def bos_id(self) -> int:
        """BOS token id"""
        token_id = self._tokenizer.bos_token_id
        if not isinstance(token_id, int):
            msg = "tokenizer bos_token_id 不可用"
            raise RuntimeError(msg)
        return token_id

    @property
    def eos_id(self) -> int:
        """EOS token id"""
        token_id = self._tokenizer.eos_token_id
        if not isinstance(token_id, int):
            msg = "tokenizer eos_token_id 不可用"
            raise RuntimeError(msg)
        return token_id

    @property
    def pad_id(self) -> int:
        """PAD token id"""
        token_id = self._tokenizer.pad_token_id
        if not isinstance(token_id, int):
            msg = "tokenizer pad_token_id 不可用"
            raise RuntimeError(msg)
        return token_id

    def load_image(self, image_path: str | Path) -> Image.Image:
        """加载图片 + EXIF 方向校正"""
        img = Image.open(image_path).convert("RGB")
        result = ImageOps.exif_transpose(img)
        if result is None:
            return img
        return result

    def preprocess(
        self, image: Image.Image, prompt: str
    ) -> list[list[Any]]:
        """构造 vLLM multi_modal_data。

        返回格式与 DeepSeek-OCR-2 的 tokenize_with_images 一致：
        [[input_ids, pixel_values, images_crop,
          images_seq_mask, images_spatial_crop,
          num_image_tokens, image_shapes]]
        """
        text_splits = prompt.split(self._image_token)
        images_list: list[torch.Tensor] = []
        images_crop_list: list[torch.Tensor] = []
        images_seq_mask: list[bool] = []
        images_spatial_crop: list[list[int]] = []
        image_shapes: list[tuple[int, int]] = []
        num_image_tokens: list[int] = []
        tokenized_str: list[int] = []

        # 处理 <image> 前的文本 + 图片
        for text_sep in text_splits[:-1]:
            # encode 文本
            sep_ids: list[int] = self._tokenizer.encode(
                text_sep, add_special_tokens=False
            )
            tokenized_str += sep_ids
            images_seq_mask += [False] * len(sep_ids)

            image_shapes.append(image.size)

            # 计算 tile 网格
            w, h = image.size
            if w <= self.crop_size and h <= self.crop_size:
                crop_ratio = (1, 1)
                tiles_raw: list[Image.Image] = []
            else:
                tiles_raw, crop_ratio = _dynamic_preprocess(
                    image,
                    self.min_crops,
                    self.max_crops,
                    self.crop_size,
                )

            # global view: pad 到 base_size x base_size
            global_view = ImageOps.pad(
                image,
                (self.base_size, self.base_size),
                color=tuple(
                    int(x * 255)
                    for x in self._transform.mean
                ),
            )
            images_list.append(
                self._transform(global_view)
            )

            num_w, num_h = crop_ratio
            images_spatial_crop.append([num_w, num_h])

            # local tiles
            if num_w > 1 or num_h > 1:
                for tile in tiles_raw:
                    images_crop_list.append(
                        self._transform(tile)
                    )

            # image token 序列
            nq_base = math.ceil(
                (self.base_size // self.patch_size)
                / self.downsample_ratio
            )
            nq_crop = math.ceil(
                (self.crop_size // self.patch_size)
                / self.downsample_ratio
            )

            img_tokens = (
                [self._image_token_id] * nq_base
            ) * nq_base
            img_tokens += [self._image_token_id]
            if num_w > 1 or num_h > 1:
                img_tokens += (
                    [self._image_token_id]
                    * (nq_crop * num_w)
                ) * (nq_crop * num_h)

            tokenized_str += img_tokens
            images_seq_mask += [True] * len(img_tokens)
            num_image_tokens.append(len(img_tokens))

        # 最后一段文本
        last_ids: list[int] = self._tokenizer.encode(
            text_splits[-1], add_special_tokens=False
        )
        tokenized_str += last_ids
        images_seq_mask += [False] * len(last_ids)

        # BOS + EOS
        tokenized_str = [self.bos_id] + tokenized_str
        images_seq_mask = [False, *images_seq_mask]
        # 添加 EOS 后立即移除（inference mode）
        # 与原始代码行为一致

        # 构造 tensor
        input_ids = torch.LongTensor(tokenized_str)
        seq_mask = torch.tensor(
            images_seq_mask, dtype=torch.bool
        )

        # pad negative ids
        input_ids[input_ids < 0] = self.pad_id

        if not images_list:
            pixel_values = torch.zeros(
                (1, 3, self.base_size, self.base_size)
            )
            spatial_crop = torch.zeros(
                (1, 1), dtype=torch.long
            )
            crops = torch.zeros(
                (1, 3, self.crop_size, self.crop_size)
            ).unsqueeze(0)
        else:
            pixel_values = torch.stack(images_list, dim=0)
            spatial_crop = torch.tensor(
                images_spatial_crop, dtype=torch.long
            )
            if images_crop_list:
                crops = torch.stack(
                    images_crop_list, dim=0
                ).unsqueeze(0)
            else:
                crops = torch.zeros(
                    (1, 3, self.crop_size, self.crop_size)
                ).unsqueeze(0)

        input_ids = input_ids.unsqueeze(0)

        return [
            [
                input_ids,
                pixel_values,
                crops,
                seq_mask,
                spatial_crop,
                num_image_tokens,
                image_shapes,
            ]
        ]
