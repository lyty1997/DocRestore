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

"""基于已有 OCR 输出目录的 Fixture 引擎（测试用）

读取 {output_dir}/{stem}_OCR/ 下已有的 result.mmd 和 images/，
构造 PageOCR 返回，不做真实 OCR 推理。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from docrestore.models import PageOCR, Region


class FixtureOCREngine:
    """基于已有 OCR 输出的 Fixture 引擎"""

    def __init__(self) -> None:
        self._ready = False

    async def initialize(self) -> None:
        """无需加载模型"""
        self._ready = True

    async def ocr(
        self, image_path: Path, output_dir: Path
    ) -> PageOCR:
        """从已有 OCR 输出目录读取结果。

        在 output_dir 下查找 {stem}_OCR/，与真实引擎行为一致。
        测试时需先将样例数据拷贝到 output_dir。
        """
        stem = image_path.stem
        source_dir = output_dir / f"{stem}_OCR"

        if not source_dir.exists():
            msg = f"Fixture OCR 目录不存在: {source_dir}"
            raise FileNotFoundError(msg)

        # 读取 result.mmd
        mmd_path = source_dir / "result.mmd"
        raw_text = mmd_path.read_text(encoding="utf-8")

        # 读取原始输出（含 grounding）
        ori_path = source_dir / "result_ori.mmd"
        ori_text = (
            ori_path.read_text(encoding="utf-8")
            if ori_path.exists()
            else raw_text
        )

        # 扫描 images/ 目录构造 regions
        images_dir = source_dir / "images"
        regions: list[Region] = []
        if images_dir.exists():
            for img_file in sorted(images_dir.iterdir()):
                if img_file.suffix.lower() in (
                    ".jpg",
                    ".jpeg",
                    ".png",
                ):
                    regions.append(
                        Region(
                            bbox=(0, 0, 0, 0),
                            label=self._extract_label(
                                ori_text, img_file.stem
                            ),
                            cropped_path=img_file,
                        )
                    )

        return PageOCR(
            image_path=image_path,
            image_size=(1603, 1720),
            raw_text=raw_text,
            regions=regions,
            output_dir=source_dir,
            has_eos=True,
        )

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()。"""
        results: list[PageOCR] = []
        total = len(image_paths)
        for i, path in enumerate(image_paths):
            page = await self.ocr(path, output_dir)
            results.append(page)
            if on_progress is not None:
                on_progress(i + 1, total)
        return results

    async def shutdown(self) -> None:
        """无需释放资源"""
        self._ready = False

    @property
    def is_ready(self) -> bool:
        """引擎是否就绪"""
        return self._ready

    @staticmethod
    def _extract_label(ori_text: str, img_index: str) -> str:
        """从原始 grounding 文本中提取图片标签。"""
        # 简单匹配 <|ref|>label<|/ref|> 模式
        refs: list[str] = re.findall(
            r"<\|ref\|>(\w+)<\|/ref\|>", ori_text
        )
        idx = int(img_index) if img_index.isdigit() else 0
        if idx < len(refs):
            return refs[idx]
        return "image"
