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

"""OCR 引擎 Protocol 定义"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from docrestore.models import PageOCR


class OCREngine(Protocol):
    """OCR 引擎接口"""

    async def initialize(self) -> None:
        """加载模型到 GPU"""
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
