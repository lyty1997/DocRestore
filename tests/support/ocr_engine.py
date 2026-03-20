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

"""测试用 OCR 引擎（读取已有 *_OCR/ 输出目录）。

注意：该模块仅用于 tests/，不属于产品代码。

目的：在没有 GPU 的环境中，也能通过“读取已生成的 OCR 结果”来复现
Pipeline 的后续流程（clean/dedup/refine/render），使端到端测试可运行。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from docrestore.models import PageOCR, Region


class FixtureOCREngine:
    """基于已有 OCR 输出的测试引擎。"""

    def __init__(self) -> None:
        self._ready = False

    async def initialize(self) -> None:
        """测试引擎无需加载模型。"""
        self._ready = True

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        """从 output_dir/{stem}_OCR/ 读取 result.mmd/images。"""
        stem = image_path.stem
        source_dir = output_dir / f"{stem}_OCR"

        if not source_dir.exists():
            msg = f"测试 OCR 目录不存在: {source_dir}"
            raise FileNotFoundError(msg)

        mmd_path = source_dir / "result.mmd"
        raw_text = mmd_path.read_text(encoding="utf-8")

        ori_path = source_dir / "result_ori.mmd"
        if ori_path.exists():
            ori_text = ori_path.read_text(encoding="utf-8")
        else:
            ori_text = raw_text

        images_dir = source_dir / "images"
        regions: list[Region] = []
        if images_dir.exists():
            for img_file in sorted(images_dir.iterdir()):
                if img_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    regions.append(
                        Region(
                            bbox=(0, 0, 0, 0),
                            label=self._extract_label(ori_text, img_file.stem),
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
        """测试引擎无需释放资源。"""
        self._ready = False

    @property
    def is_ready(self) -> bool:
        """引擎是否就绪。"""
        return self._ready

    @staticmethod
    def _extract_label(ori_text: str, img_index: str) -> str:
        """从原始 grounding 文本中提取图片标签。"""
        refs: list[str] = re.findall(r"<\|ref\|>(\w+)<\|/ref\|>", ori_text)
        idx = int(img_index) if img_index.isdigit() else 0
        if idx < len(refs):
            return refs[idx]
        return "image"
