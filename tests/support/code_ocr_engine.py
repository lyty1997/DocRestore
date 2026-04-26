# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""代码模式专用的测试 OCR 引擎：从 spike lines.jsonl 喂 text_lines。

仅 tests/ 使用，不属于产品代码。代码模式 (`code.enable=True`) 需要
``PageOCR.text_lines`` 而非 ``raw_text``，文档模式的 FixtureOCREngine 不适用。

数据源：``output/age8-probe-basic/<stem>/lines.jsonl``（已用 PaddleOCR-VL
basic pipeline 跑过的 8 张 chromium spike）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from docrestore.models import PageOCR, TextLine


class CodeFixtureOCREngine:
    """从 spike lines.jsonl 读取 text_lines 返回 PageOCR。

    搜索路径：``<lines_root>/<image_stem>/lines.jsonl``。找不到时报错。
    image_size 从原图读取（PIL）；原图缺失时 fallback 用 bbox 最大值。
    """

    def __init__(self, lines_root: Path) -> None:
        self._lines_root = lines_root
        self._ready = False

    async def initialize(
        self,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        del on_progress
        if not self._lines_root.exists():
            msg = f"spike lines 目录不存在: {self._lines_root}"
            raise FileNotFoundError(msg)
        self._ready = True

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        del output_dir  # 代码模式不写 OCR 缓存
        stem = image_path.stem
        lines_path = self._lines_root / stem / "lines.jsonl"
        if not lines_path.exists():
            msg = f"spike lines.jsonl 不存在: {lines_path}"
            raise FileNotFoundError(msg)

        text_lines: list[TextLine] = []
        for line in lines_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            text_lines.append(TextLine(
                bbox=tuple(int(v) for v in item["bbox"][:4]),  # type: ignore[arg-type]
                text=str(item["text"]),
                score=float(item.get("score", 1.0)),
            ))

        # 用 bbox 最大值作 image_size fallback（原图存在时优先读真实尺寸）
        image_size: tuple[int, int]
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                image_size = img.size
        except (OSError, ImportError):
            image_size = (
                max((ln.bbox[2] for ln in text_lines), default=0),
                max((ln.bbox[3] for ln in text_lines), default=0),
            )

        return PageOCR(
            image_path=image_path,
            image_size=image_size,
            raw_text="",
            cleaned_text="",
            regions=[],
            output_dir=None,
            has_eos=True,
            text_lines=text_lines,
        )

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        results: list[PageOCR] = []
        total = len(image_paths)
        for i, path in enumerate(image_paths):
            page = await self.ocr(path, output_dir)
            results.append(page)
            if on_progress is not None:
                on_progress(i + 1, total)
        return results

    async def shutdown(self) -> None:
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready
