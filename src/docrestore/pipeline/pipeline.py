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

"""Pipeline 核心编排器

串联 OCR → 清洗 → 去重 → LLM 精修 → 输出的完整流程。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

import aiofiles

from docrestore.llm.base import LLMRefiner
from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.prompts import parse_gaps
from docrestore.llm.segmenter import DocumentSegmenter
from docrestore.models import (
    Gap,
    MergedDocument,
    PipelineResult,
    RefineContext,
    RefinedResult,
    TaskProgress,
)
from docrestore.ocr.base import OCREngine
from docrestore.output.renderer import Renderer
from docrestore.pipeline.config import LLMConfig, PipelineConfig
from docrestore.processing.cleaner import OCRCleaner
from docrestore.processing.dedup import PageDeduplicator

logger = logging.getLogger(__name__)


class Pipeline:
    """核心编排器"""

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config
        self._ocr_engine: OCREngine | None = None
        self._refiner: LLMRefiner | None = None

    def set_ocr_engine(self, engine: OCREngine) -> None:
        """注入 OCR 引擎（允许外部传入 mock）"""
        self._ocr_engine = engine

    def set_refiner(self, refiner: LLMRefiner) -> None:
        """注入 LLM 精修器（允许外部传入 mock）"""
        self._refiner = refiner

    async def _save_debug(
        self,
        output_dir: Path,
        name: str,
        content: str,
    ) -> None:
        """将中间结果写入 output_dir/debug/{name}（受 debug 开关控制）"""
        if not self._config.debug:
            return
        debug_dir = output_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = debug_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(target, "w", encoding="utf-8") as f:
            await f.write(content)

    async def initialize(self) -> None:
        """创建并初始化 OCR 引擎 + LLM 精修器"""
        if self._ocr_engine is not None:
            await self._ocr_engine.initialize()

        if self._refiner is None and self._config.llm.model:
            self._refiner = CloudLLMRefiner(self._config.llm)

    async def process(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None]
        | None = None,
        llm_override: dict[str, str | int] | None = None,
    ) -> PipelineResult:
        """完整处理流程。

        llm_override: 请求级 LLM 配置覆盖
        """
        await asyncio.to_thread(
            output_dir.mkdir, parents=True, exist_ok=True
        )

        def _report(
            stage: str,
            current: int,
            total: int,
            message: str = "",
        ) -> None:
            if on_progress is not None:
                percent = (
                    (current / total * 100) if total > 0 else 0
                )
                on_progress(
                    TaskProgress(
                        stage=stage,
                        current=current,
                        total=total,
                        percent=round(percent, 1),
                        message=message,
                    )
                )

        # 阶段 1: OCR
        all_files = await asyncio.to_thread(
            lambda: list(image_dir.iterdir())
        )
        images = sorted(
            p
            for p in all_files
            if p.suffix.lower()
            in (".jpg", ".jpeg", ".png")
        )
        if not images:
            msg = f"未找到图片文件: {image_dir}"
            raise FileNotFoundError(msg)

        if self._ocr_engine is None:
            msg = "OCR 引擎未初始化"
            raise RuntimeError(msg)

        pages = await self._ocr_engine.ocr_batch(
            images,
            output_dir,
            on_progress=lambda c, t: _report(
                "ocr", c, t, f"正在 OCR 第 {c} 张照片..."
            ),
        )

        # 阶段 2: 清洗
        cleaner = OCRCleaner()
        for i, page in enumerate(pages):
            await cleaner.clean(page)
            _report(
                "clean",
                i + 1,
                len(pages),
                f"正在清洗第 {i + 1} 页...",
            )
            # debug: 每页清洗后的文本
            stem = page.image_path.stem
            await self._save_debug(
                output_dir,
                f"{stem}_cleaned.md",
                page.cleaned_text,
            )

        # 阶段 3: 去重合并
        dedup = PageDeduplicator(self._config.dedup)
        merged = dedup.merge_all_pages(
            pages,
            on_progress=lambda c, t: _report(
                "merge", c, t, f"正在合并第 {c} 页..."
            ),
        )
        # debug: 去重合并后的完整 markdown
        await self._save_debug(
            output_dir, "merged_raw.md", merged.markdown
        )

        # 阶段 4: LLM 精修
        # 请求级 LLM 配置覆盖：合并默认配置和 override
        llm_cfg = self._config.llm
        refiner = self._refiner
        if llm_override:
            from dataclasses import asdict

            cfg_dict: dict[str, object] = {
                **asdict(llm_cfg),
                **llm_override,
            }
            llm_cfg = LLMConfig(**cfg_dict)  # type: ignore[arg-type]
            refiner = CloudLLMRefiner(llm_cfg)

        segmenter = DocumentSegmenter(
            max_chars_per_segment=llm_cfg.max_chars_per_segment,
            overlap_lines=llm_cfg.segment_overlap_lines,
        )
        segments = segmenter.segment(merged.markdown)

        all_gaps: list[Gap] = []
        refined_results: list[RefinedResult] = []

        for i, seg in enumerate(segments):
            _report(
                "refine",
                i + 1,
                len(segments),
                f"正在精修第 {i + 1} 段...",
            )
            # debug: segment 输入
            await self._save_debug(
                output_dir, f"segments/{i}_input.md", seg.text
            )
            if refiner is not None:
                ctx = RefineContext(
                    segment_index=i + 1,
                    total_segments=len(segments),
                    overlap_before="",
                    overlap_after="",
                )
                try:
                    result = await refiner.refine(
                        seg.text, ctx
                    )
                except Exception:
                    logger.warning(
                        "段 %d 精修失败，回退到原文",
                        i + 1,
                        exc_info=True,
                    )
                    result = RefinedResult(markdown=seg.text)
            else:
                result = RefinedResult(markdown=seg.text)

            refined_results.append(result)
            all_gaps.extend(result.gaps)
            # debug: segment 精修输出
            await self._save_debug(
                output_dir,
                f"segments/{i}_output.md",
                result.markdown,
            )

        # 阶段 5: 重组
        reassembled = self._reassemble(
            refined_results, merged
        )
        # debug: 重组后的完整 markdown
        await self._save_debug(
            output_dir,
            "reassembled.md",
            reassembled.markdown,
        )

        # 收集 GAP（从重组后的 markdown 中再次扫描）
        _, extra_gaps = parse_gaps(reassembled.markdown)
        all_gaps.extend(extra_gaps)

        # 阶段 6: 输出
        _report("render", 0, 1, "正在渲染输出...")
        renderer = Renderer(self._config.output)
        doc_path = await renderer.render(
            reassembled, output_dir
        )
        _report("render", 1, 1, "渲染完成")

        final_md = doc_path.read_text(encoding="utf-8")
        return PipelineResult(
            output_path=doc_path,
            markdown=final_md,
            images=reassembled.images,
            gaps=all_gaps,
        )

    async def shutdown(self) -> None:
        """释放所有资源"""
        if self._ocr_engine is not None:
            await self._ocr_engine.shutdown()

    @staticmethod
    def _reassemble(
        refined_results: list[RefinedResult],
        merged_doc: MergedDocument,
    ) -> MergedDocument:
        """拼接精修后的各段。"""
        if not refined_results:
            return merged_doc

        parts = [r.markdown for r in refined_results]
        reassembled_md = "\n".join(parts)
        return MergedDocument(
            markdown=reassembled_md,
            images=merged_doc.images,
            gaps=merged_doc.gaps,
        )
