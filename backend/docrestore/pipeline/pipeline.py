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

OCR → 清洗 → 去重合并 → LLM 精修 → 缺口补充 → 输出。
支持单目录（LLM 文档聚类）和多子目录（物理分目录）两种输入结构。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import aiofiles

from docrestore.llm.base import BaseLLMRefiner, LLMRefiner
from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.prompts import (
    extract_first_heading,
    parse_doc_boundaries,
    parse_gaps,
)
from docrestore.processing.segmenter import DocumentSegmenter
from docrestore.models import (
    DocBoundary,
    Gap,
    MergedDocument,
    PageOCR,
    PipelineResult,
    RedactionRecord,
    RefineContext,
    RefinedResult,
    Region,
    TaskProgress,
)
from docrestore.ocr.base import OCREngine, WorkerBackedOCREngine
from docrestore.ocr.engine_manager import EngineManager
from docrestore.output.renderer import Renderer
from docrestore.pipeline.config import (
    LLMConfig,
    OCRConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.profiler import (
    MemoryProfiler,
    NullProfiler,
    Profiler,
    create_profiler,
    current_profiler,
    reset_current_profiler,
    set_current_profiler,
)
from docrestore.privacy.redactor import EntityLexicon, PIIRedactor
from docrestore.processing.cleaner import OCRCleaner
from docrestore.processing.dedup import PageDeduplicator, strip_repeated_lines
from docrestore.utils.paths import sanitize_dirname

# 进度回调类型
ReportFn = Callable[[str, int, int, str], None]

# page marker 正则
_PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*(.+?)\s*-->")

logger = logging.getLogger(__name__)


_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def scan_images(image_dir: Path) -> list[Path]:
    """扫描目录下所有支持的图片文件，排序返回。"""
    return sorted(
        p
        for p in image_dir.iterdir()
        if p.suffix.lower() in _IMAGE_EXTS
    )


def find_image_dirs(root: Path) -> list[Path]:
    """递归扫描 root 下所有包含图片的叶子目录。

    - 如果某目录直接包含图片文件，收集该目录（不再递归其子目录）
    - 否则递归其子目录继续寻找
    """

    def _has_images(d: Path) -> bool:
        """检查目录是否直接包含图片文件。"""
        return any(
            p.suffix.lower() in _IMAGE_EXTS
            for p in d.iterdir() if p.is_file()
        )

    def _collect(d: Path) -> list[Path]:
        """递归收集包含图片的目录。"""
        if _has_images(d):
            return [d]
        results: list[Path] = []
        for child in sorted(d.iterdir()):
            if child.is_dir():
                results.extend(_collect(child))
        return results

    return _collect(root)


class Pipeline:
    """核心编排器"""

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config
        self._ocr_engine: OCREngine | None = None
        self._engine_manager: EngineManager | None = None
        self._refiner: LLMRefiner | None = None
        self._llm_semaphore: asyncio.Semaphore | None = None

    @property
    def config(self) -> PipelineConfig:
        """默认 PipelineConfig，供上游合成请求级 Config 时读取。"""
        return self._config

    def set_ocr_engine(self, engine: OCREngine) -> None:
        """注入 OCR 引擎（允许外部传入 mock，测试用）"""
        self._ocr_engine = engine

    @property
    def engine_manager(self) -> EngineManager | None:
        """引擎管理器实例（只读，供路由层查询状态 / 触发预热）。"""
        return self._engine_manager

    def set_engine_manager(self, manager: EngineManager) -> None:
        """注入引擎管理器（生产环境使用，支持按需切换）"""
        self._engine_manager = manager

    def set_refiner(self, refiner: LLMRefiner) -> None:
        """注入 LLM 精修器（允许外部传入 mock）"""
        self._refiner = refiner

    def set_llm_semaphore(self, semaphore: asyncio.Semaphore) -> None:
        """注入全局 LLM 并发信号量（由 app.py 从 PipelineScheduler 传入）。

        必须在 initialize() 之前调用，否则默认 refiner 不受信号量保护。
        """
        self._llm_semaphore = semaphore

    @contextlib.asynccontextmanager
    async def _task_profiler(
        self, output_dir: Path,
    ) -> AsyncIterator[tuple[Profiler, bool]]:
        """进入根任务时创建 Profiler，嵌套调用时复用上层 Profiler。

        - 若当前 context 已有非 Null profiler（嵌套调用）→ 直接复用
        - 否则（根调用）→ 创建 + 绑定 contextvar + 退出时导出 profile.json

        返回 `(profiler, is_root)`，is_root 给调用方判断是否需要做只在根
        执行的动作（目前只有 profile.json 导出，由本方法自己处理）。
        """
        existing = current_profiler()
        if not isinstance(existing, NullProfiler):
            yield existing, False
            return

        profiler = create_profiler(enable=self._config.profiling_enable)
        token = set_current_profiler(profiler)
        try:
            yield profiler, True
        finally:
            reset_current_profiler(token)
            if isinstance(profiler, MemoryProfiler):
                await self._export_profile(profiler, output_dir)

    async def _export_profile(
        self,
        profiler: MemoryProfiler,
        output_dir: Path,
    ) -> None:
        """落盘 profile.json + 打印扁平化汇总表到日志。"""
        configured = self._config.profiling_output_path
        out_path = (
            Path(configured) if configured
            else output_dir / "profile.json"
        )
        try:
            await asyncio.to_thread(profiler.export_json, out_path)
            table = profiler.export_summary_table()
            if table:
                logger.info(
                    "Pipeline profile → %s\n%s", out_path, table,
                )
        except Exception:
            logger.warning(
                "导出 profile.json 失败: %s", out_path, exc_info=True,
            )

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

    def _create_refiner(self, llm_cfg: LLMConfig) -> BaseLLMRefiner:
        """根据 provider 创建对应的 LLM 精修器，并注入全局限流 semaphore。"""
        if llm_cfg.provider == "local":
            from docrestore.llm.local import LocalLLMRefiner

            return LocalLLMRefiner(llm_cfg, semaphore=self._llm_semaphore)
        return CloudLLMRefiner(llm_cfg, semaphore=self._llm_semaphore)

    async def initialize(self) -> None:
        """创建并初始化 OCR 引擎 + LLM 精修器

        当 EngineManager 已注入时，OCR 引擎延迟到首次任务时按需创建。
        """
        if self._engine_manager is None:
            # 无 EngineManager → 传统模式（测试或直接注入）
            if self._ocr_engine is None:
                from docrestore.ocr.router import create_engine
                self._ocr_engine = create_engine(
                    model=self._config.ocr.model,
                    config=self._config.ocr,
                )
            await self._ocr_engine.initialize()

        if self._refiner is None and self._config.llm.model:
            self._refiner = self._create_refiner(self._config.llm)

    async def process_tree(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None]
        | None = None,
        llm: LLMConfig | None = None,
        gpu_lock: asyncio.Lock | None = None,
        pii: PIIConfig | None = None,
        ocr: OCRConfig | None = None,
    ) -> list[PipelineResult]:
        """统一入口：自动处理子目录结构和 LLM 文档聚类。

        - 输入目录本身含图片（叶子目录）→ process_many()（LLM 聚类）
        - 输入目录含多个子目录 → 逐子目录 process_many()，聚合结果
        - 每个子目录内部仍可通过 LLM 聚类进一步拆分

        llm/ocr/pii 为 None 时使用 `self.config` 内的默认配置；非空时
        视为本次请求的完整配置快照（由上游合成，pipeline 内部不再合并）。
        """
        async with self._task_profiler(output_dir) as (profiler, _is_root):
            with profiler.stage(
                "pipeline.total",
                image_dir=str(image_dir),
                mode="tree",
            ):
                leaf_dirs = await asyncio.to_thread(
                    find_image_dirs, image_dir,
                )
                if not leaf_dirs:
                    msg = f"未找到图片文件: {image_dir}"
                    raise FileNotFoundError(msg)

                # 单目录：直接委托 process_many（复用当前 profiler）
                if (
                    len(leaf_dirs) == 1 and leaf_dirs[0] == image_dir
                ):
                    return await self.process_many(
                        image_dir, output_dir, on_progress,
                        llm, gpu_lock, pii, ocr,
                    )

                # 多子目录：逐目录处理，聚合结果
                all_results: list[PipelineResult] = []
                for i, leaf in enumerate(leaf_dirs):
                    rel = leaf.relative_to(image_dir)
                    sub_output = output_dir / rel

                    logger.info(
                        "process_tree: [%d/%d] %s",
                        i + 1, len(leaf_dirs), rel,
                    )

                    # 包装进度回调，添加子目录标识
                    wrapped_progress = self._wrap_progress(
                        on_progress, str(rel),
                        i, len(leaf_dirs),
                    )

                    with profiler.stage(
                        "pipeline.subdir",
                        subdir=str(rel),
                        index=i + 1,
                        total=len(leaf_dirs),
                    ):
                        sub_results = await self.process_many(
                            leaf, sub_output, wrapped_progress,
                            llm, gpu_lock, pii, ocr,
                        )

                    # 补全 doc_dir：加上子目录相对路径前缀
                    for result in sub_results:
                        if result.doc_dir:
                            result.doc_dir = str(rel / result.doc_dir)
                        else:
                            result.doc_dir = str(rel)

                    all_results.extend(sub_results)

                return all_results

    @staticmethod
    def _wrap_progress(
        on_progress: Callable[[TaskProgress], None] | None,
        dir_label: str,
        dir_index: int,
        dir_total: int,
    ) -> Callable[[TaskProgress], None] | None:
        """包装进度回调，在 message 中附加子目录信息。"""
        if on_progress is None:
            return None

        def wrapped(p: TaskProgress) -> None:
            p.message = (
                f"[{dir_index + 1}/{dir_total} {dir_label}] "
                f"{p.message}"
            )
            on_progress(p)

        return wrapped

    async def process_many(  # noqa: C901
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None]
        | None = None,
        llm: LLMConfig | None = None,
        gpu_lock: asyncio.Lock | None = None,
        pii: PIIConfig | None = None,
        ocr: OCRConfig | None = None,
    ) -> list[PipelineResult]:
        """完整处理流程，支持多文档拆分。

        OCR → clean → dedup → PII → refine → reassemble
        → 拆分子文档 → 每篇独立 gap fill / final refine / render。
        返回 list[PipelineResult]（单文档退化为长度 1）。
        """
        async with self._task_profiler(output_dir) as (profiler, is_root):
            root_stage = profiler.stage(
                "pipeline.total",
                image_dir=str(image_dir),
                mode="many",
            ) if is_root else contextlib.nullcontext()
            with root_stage:
                return await self._process_many_body(
                    image_dir, output_dir, on_progress,
                    llm, gpu_lock, pii, ocr,
                )

    async def _process_many_body(  # noqa: C901
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        pii: PIIConfig | None,
        ocr: OCRConfig | None,
    ) -> list[PipelineResult]:
        """process_many 的实际实现（profiler 已由外层 _task_profiler 绑定）。"""
        profiler = current_profiler()
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

        # 扫描图片
        images = await asyncio.to_thread(scan_images, image_dir)
        if not images:
            msg = f"未找到图片文件: {image_dir}"
            raise FileNotFoundError(msg)

        if self._engine_manager is None and self._ocr_engine is None:
            msg = "OCR 引擎未初始化"
            raise RuntimeError(msg)

        # OCR + 清洗
        with profiler.stage("ocr.phase", num_images=len(images)):
            pages = await self._ocr_and_clean(
                images, output_dir, gpu_lock, _report, ocr,
            )

        # 跨页频率过滤：移除侧栏等高频重复行
        with profiler.stage("dedup.strip_repeated", num_pages=len(pages)):
            strip_repeated_lines(pages, self._config.dedup)

        # 去重合并
        with profiler.stage("dedup.merge", num_pages=len(pages)):
            dedup = PageDeduplicator(self._config.dedup)
            merged = dedup.merge_all_pages(pages)
        await self._save_debug(
            output_dir, "merged_raw.md", merged.markdown
        )

        # PII 脱敏（全局启用 或 有自定义敏感词时自动启用）
        redaction_records: list[RedactionRecord] = []
        entity_lexicon: EntityLexicon | None = None
        cloud_blocked = False
        pii_cfg = pii or self._config.pii
        if pii_cfg.enable or pii_cfg.custom_sensitive_words:
            with profiler.stage("pii.phase"):
                (
                    merged, redaction_records,
                    entity_lexicon, cloud_blocked,
                ) = await self._redact_pii(
                    merged, llm, pii_cfg,
                    output_dir, _report,
                )

        # 文档边界检测（在分段精修前）
        doc_boundaries: list[DocBoundary] = []
        if not cloud_blocked:
            with profiler.stage("llm.doc_boundary"):
                doc_boundaries = await self._detect_doc_boundaries(
                    merged, llm, _report,
                )
                if doc_boundaries:
                    merged = self._insert_doc_boundaries(
                        merged, doc_boundaries,
                    )

        # LLM 精修
        if cloud_blocked:
            refined_results: list[RefinedResult] = []
            all_gaps: list[Gap] = []
        else:
            with profiler.stage("llm.refine_phase"):
                refined_results, all_gaps = (
                    await self._refine_segments(
                        merged, output_dir, llm, _report,
                    )
                )

        # 重组
        with profiler.stage("reassemble"):
            reassembled = self._reassemble(refined_results, merged)
        await self._save_debug(
            output_dir, "reassembled.md", reassembled.markdown,
        )

        # 按文档边界拆分
        with profiler.stage("doc_split"):
            sub_docs = self._split_by_doc_boundaries(
                reassembled, pages,
            )
        _report(
            "doc_split", 0, 1,
            f"检测到 {len(sub_docs)} 篇文档",
        )

        # 每个子文档独立后处理
        results: list[PipelineResult] = []
        for di, (title, page_names, sub_doc) in enumerate(sub_docs):
            with profiler.stage(
                "doc.postprocess",
                doc_index=di + 1,
                doc_total=len(sub_docs),
                title=title,
            ):
                sub_output = self._resolve_sub_output_dir(
                    output_dir, title, di, len(sub_docs),
                )
                await asyncio.to_thread(
                    sub_output.mkdir, parents=True, exist_ok=True,
                )

                # 过滤该子文档的 pages 和 gaps
                page_name_set = set(page_names)
                sub_pages = [
                    p for p in pages
                    if p.image_path.name in page_name_set
                ]
                sub_gaps = [
                    g for g in all_gaps
                    if g.after_image in page_name_set
                ]

                sub_truncated = False
                if not cloud_blocked:
                    with profiler.stage(
                        "llm.gap_fill_phase",
                        num_gaps=len(sub_gaps),
                    ):
                        sub_doc = await self._maybe_fill_gaps(
                            sub_doc, sub_gaps, sub_pages,
                            sub_output, llm, gpu_lock,
                            _report, entity_lexicon,
                        )
                    with profiler.stage("llm.final_refine"):
                        sub_doc, sub_truncated = (
                            await self._do_final_refine(
                                sub_doc, sub_output, llm, _report,
                            )
                        )

                _, extra_gaps = parse_gaps(sub_doc.markdown)
                sub_gaps.extend(extra_gaps)

                # 输出
                label = (
                    f" ({di + 1}/{len(sub_docs)})"
                    if len(sub_docs) > 1 else ""
                )
                _report(
                    "render", di + 1, len(sub_docs),
                    f"渲染输出{label}...",
                )
                with profiler.stage("render.write"):
                    renderer = Renderer(self._config.output)
                    doc_path = await renderer.render(
                        sub_doc, sub_output, ocr_root_dir=output_dir,
                    )

                # 聚合警告
                warnings = self._collect_warnings(
                    refined_results, sub_gaps, sub_truncated,
                )
                if cloud_blocked:
                    warnings.append(
                        "PII 实体检测失败，已阻断云端 LLM 调用",
                    )

                doc_dir = (
                    "" if sub_output == output_dir
                    else sub_output.name
                )
                final_md = doc_path.read_text(encoding="utf-8")
                results.append(PipelineResult(
                    output_path=doc_path,
                    markdown=final_md,
                    images=sub_doc.images,
                    gaps=sub_gaps,
                    warnings=warnings,
                    redaction_records=redaction_records,
                    doc_title=title,
                    doc_dir=doc_dir,
                ))

        return results

    async def _ocr_and_clean(
        self,
        images: list[Path],
        output_dir: Path,
        gpu_lock: asyncio.Lock | None,
        report_fn: ReportFn,
        ocr: OCRConfig | None = None,
    ) -> list[PageOCR]:
        """OCR（支持 batch 并发）→ 清洗，返回 PageOCR 列表。

        - ocr_batch_size >= 2 且引擎支持 `ocr_batch` → 一次性提交所有图，
          引擎内部按 batch_size 分块并发，vLLM 做 continuous batching。
        - 否则回退到逐张 ocr()（保留旧路径）。
        gpu_lock 覆盖整个 ocr_batch 调用或每次单图调用。
        """
        # 通过 EngineManager 获取正确引擎（按需切换）
        if self._engine_manager is not None:
            # 引擎初始化进度 → 通过 report_fn 推送到前端（stage="init"）
            def _init_progress(msg: str) -> None:
                report_fn("init", 0, 0, msg)

            engine = await self._engine_manager.ensure(
                ocr, on_progress=_init_progress,
            )
        elif self._ocr_engine is not None:
            engine = self._ocr_engine  # 兼容测试注入
        else:
            msg = "OCR 引擎未初始化"
            raise RuntimeError(msg)

        ocr_cfg = ocr or self._config.ocr
        batch_size = max(1, ocr_cfg.ocr_batch_size)
        pages = await self._run_ocr(
            engine, images, output_dir,
            gpu_lock, report_fn, batch_size,
        )

        # 清洗 + 落盘 debug（纯 CPU/IO，与 GPU 无关，顺序处理即可）
        profiler = current_profiler()
        cleaner = OCRCleaner()
        for page in pages:
            with profiler.stage(
                "cleaner.page", stem=page.image_path.stem,
            ):
                await cleaner.clean(page)
            await self._save_debug(
                output_dir,
                f"{page.image_path.stem}_cleaned.md",
                page.cleaned_text,
            )

        return pages

    async def _run_ocr(
        self,
        engine: OCREngine,
        images: list[Path],
        output_dir: Path,
        gpu_lock: asyncio.Lock | None,
        report_fn: ReportFn,
        batch_size: int,
    ) -> list[PageOCR]:
        """OCR 调度：batch_size>=2 走 ocr_batch，否则逐张 ocr。"""
        profiler = current_profiler()
        total = len(images)

        def _on_batch_progress(done: int, tot: int) -> None:
            report_fn(
                "ocr", done, tot, f"OCR {done}/{tot}...",
            )

        # 只有 WorkerBackedOCREngine 子类才有真正的 ocr_batch 实现
        # （避免 AsyncMock 等测试替身让 hasattr/iscoroutinefunction 误判）
        if batch_size >= 2 and isinstance(engine, WorkerBackedOCREngine):
            with profiler.stage(
                "ocr.batch",
                num_images=total,
                batch_size=batch_size,
            ):
                if gpu_lock is not None:
                    async with gpu_lock:
                        return await engine.ocr_batch(
                            images, output_dir, _on_batch_progress,
                        )
                return await engine.ocr_batch(
                    images, output_dir, _on_batch_progress,
                )

        # Fallback：逐张 ocr()，保留旧路径
        pages: list[PageOCR] = []
        for i, img in enumerate(images):
            with profiler.stage("ocr.single", stem=img.stem):
                if gpu_lock is not None:
                    async with gpu_lock:
                        page = await engine.ocr(img, output_dir)
                else:
                    page = await engine.ocr(img, output_dir)
            report_fn(
                "ocr", i + 1, total,
                f"OCR 第 {i + 1}/{total} 张...",
            )
            pages.append(page)
        return pages

    async def _refine_segments(
        self,
        merged: MergedDocument,
        output_dir: Path,
        llm: LLMConfig | None,
        report_fn: ReportFn,
    ) -> tuple[list[RefinedResult], list[Gap]]:
        """分段 LLM 精修，返回 (精修结果列表, gap 列表)。"""
        if llm is None:
            llm_cfg = self._config.llm
            refiner = self._refiner
        else:
            llm_cfg = llm
            refiner = self._create_refiner(llm_cfg)

        profiler = current_profiler()
        with profiler.stage("llm.segment"):
            segmenter = DocumentSegmenter(
                max_chars_per_segment=llm_cfg.max_chars_per_segment,
                overlap_lines=llm_cfg.segment_overlap_lines,
            )
            segments = segmenter.segment(merged.markdown)

        all_gaps: list[Gap] = []
        refined_results: list[RefinedResult] = []

        for i, seg in enumerate(segments):
            report_fn(
                "refine", i + 1, len(segments),
                f"精修第 {i + 1}/{len(segments)} 段...",
            )
            await self._save_debug(
                output_dir, f"segments/{i}_input.md", seg.text
            )

            with profiler.stage(
                "llm.refine_segment",
                index=i + 1,
                total=len(segments),
                input_chars=len(seg.text),
            ):
                result = await self._refine_one_segment(
                    refiner, seg.text, i, len(segments),
                )

            # 截断检测：行数比例启发式（finish_reason 已标记的不重复检测）
            input_lines = seg.text.count("\n") + 1
            output_lines = result.markdown.count("\n") + 1
            if (
                not result.truncated
                and input_lines > llm_cfg.truncation_min_input_lines
                and output_lines
                < input_lines * (1 - llm_cfg.truncation_ratio_threshold)
            ):
                result = RefinedResult(
                    markdown=result.markdown,
                    gaps=result.gaps,
                    truncated=True,
                )
                logger.warning(
                    "段 %d 疑似截断（输入 %d 行 → 输出 %d 行）",
                    i + 1, input_lines, output_lines,
                )

            refined_results.append(result)
            all_gaps.extend(result.gaps)
            await self._save_debug(
                output_dir, f"segments/{i}_output.md", result.markdown,
            )

        return refined_results, all_gaps

    @staticmethod
    async def _refine_one_segment(
        refiner: LLMRefiner | None,
        text: str,
        index: int,
        total: int,
    ) -> RefinedResult:
        """精修单个分段，失败时回退到原文。"""
        if refiner is None:
            return RefinedResult(markdown=text)
        ctx = RefineContext(
            segment_index=index + 1,
            total_segments=total,
            overlap_before="",
            overlap_after="",
        )
        try:
            return await refiner.refine(text, ctx)
        except Exception:
            logger.warning(
                "段 %d 精修失败，回退到原文",
                index + 1,
                exc_info=True,
            )
            return RefinedResult(markdown=text)

    async def shutdown(self) -> None:
        """释放所有资源"""
        if self._engine_manager is not None:
            await self._engine_manager.shutdown()
        elif self._ocr_engine is not None:
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

    def _split_by_doc_boundaries(
        self,
        doc: MergedDocument,
        pages: list[PageOCR],
    ) -> list[tuple[str, list[str], MergedDocument]]:
        """按 DOC_BOUNDARY 标记拆分文档。

        返回 list[(title, page_names, sub_document)]。
        无边界时返回单元素列表（向下兼容单文档场景）。
        """
        cleaned_md, boundaries = parse_doc_boundaries(doc.markdown)

        # 收集所有 page marker 的名称和位置
        page_positions: list[tuple[str, int]] = []
        for m in _PAGE_MARKER_RE.finditer(cleaned_md):
            page_positions.append((m.group(1).strip(), m.start()))

        all_page_names = [name for name, _ in page_positions]

        if not boundaries or not page_positions:
            title = extract_first_heading(cleaned_md)
            return [(
                title,
                [p.image_path.name for p in pages],
                MergedDocument(
                    markdown=cleaned_md,
                    images=doc.images,
                    gaps=doc.gaps,
                ),
            )]

        # 解析有效的切分点
        split_indices, boundary_titles = self._resolve_split_points(
            boundaries, page_positions,
        )

        if not split_indices:
            title = extract_first_heading(cleaned_md)
            return [(
                title,
                [p.image_path.name for p in pages],
                MergedDocument(
                    markdown=cleaned_md,
                    images=doc.images,
                    gaps=doc.gaps,
                ),
            )]

        return self._build_sub_docs(
            cleaned_md, doc.images,
            split_indices, boundary_titles,
            page_positions, all_page_names,
        )

    @staticmethod
    def _resolve_split_points(
        boundaries: list[DocBoundary],
        page_positions: list[tuple[str, int]],
    ) -> tuple[list[int], list[str]]:
        """将 DOC_BOUNDARY 列表映射为 page_positions 索引。

        返回 (排序后的索引列表, 对应的标题列表)。
        找不到对应 page marker 的 boundary 忽略并记录 warning。
        """
        split_indices: list[int] = []
        boundary_titles: list[str] = []
        for b in boundaries:
            for pi, (pname, _) in enumerate(page_positions):
                if pname == b.after_page:
                    split_indices.append(pi)
                    boundary_titles.append(b.new_title)
                    break
            else:
                logger.warning(
                    "DOC_BOUNDARY after_page=%s 未找到对应 page marker，忽略",
                    b.after_page,
                )

        if not split_indices:
            return [], []

        # 排序并去重
        paired = sorted(
            zip(split_indices, boundary_titles, strict=True),
            key=lambda x: x[0],
        )
        return [p[0] for p in paired], [p[1] for p in paired]

    @staticmethod
    def _build_sub_docs(
        cleaned_md: str,
        all_images: list[Region],
        split_indices: list[int],
        boundary_titles: list[str],
        page_positions: list[tuple[str, int]],
        all_page_names: list[str],
    ) -> list[tuple[str, list[str], MergedDocument]]:
        """根据切分点构造子文档列表。"""
        # 切分 markdown 文本
        split_positions: list[int] = []
        for si in split_indices:
            if si + 1 < len(page_positions):
                split_positions.append(page_positions[si + 1][1])

        md_parts: list[str] = []
        prev = 0
        for pos in split_positions:
            md_parts.append(cleaned_md[prev:pos])
            prev = pos
        md_parts.append(cleaned_md[prev:])

        # 为每部分分配 page_names
        page_name_groups: list[list[str]] = []
        prev_pi = 0
        for si in split_indices:
            page_name_groups.append(all_page_names[prev_pi:si + 1])
            prev_pi = si + 1
        page_name_groups.append(all_page_names[prev_pi:])

        # 构造标题列表：首篇从 heading 提取，后续从 boundary
        titles = [extract_first_heading(md_parts[0])]
        titles.extend(boundary_titles[:len(md_parts) - 1])

        # 图片引用正则
        img_ref_re = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

        result: list[tuple[str, list[str], MergedDocument]] = []
        for i, md_part in enumerate(md_parts):
            refs = set(img_ref_re.findall(md_part))
            sub_images = [
                img for img in all_images
                if img.cropped_path is not None
                and any(
                    str(img.cropped_path).endswith(ref)
                    or ref in str(img.cropped_path)
                    for ref in refs
                )
            ]

            result.append((
                titles[i] if i < len(titles) else f"文档_{i + 1}",
                page_name_groups[i] if i < len(page_name_groups) else [],
                MergedDocument(
                    markdown=md_part,
                    images=sub_images,
                    gaps=[],
                ),
            ))

        return result

    @staticmethod
    def _resolve_sub_output_dir(
        output_dir: Path,
        title: str,
        index: int,
        total: int,
    ) -> Path:
        """确定子文档输出目录。

        单文档(total==1)：返回 output_dir 本身（兼容）。
        多文档：返回 output_dir / sanitized_title。
        """
        if total <= 1:
            return output_dir

        dirname = sanitize_dirname(title)
        if not dirname:
            dirname = f"文档_{index + 1}"
        return output_dir / dirname

    async def _maybe_fill_gaps(
        self,
        doc: MergedDocument,
        gaps: list[Gap],
        pages: list[PageOCR],
        output_dir: Path,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        report_fn: ReportFn,
        entity_lexicon: EntityLexicon | None = None,
    ) -> MergedDocument:
        """条件检查后调用 _fill_gaps，不满足条件直接返回原文档。"""
        if not self._config.llm.enable_gap_fill or not gaps:
            return doc

        # OCR 引擎必须支持 reocr_page
        active_engine = (
            self._engine_manager.engine
            if self._engine_manager is not None
            else self._ocr_engine
        )
        if not hasattr(active_engine, "reocr_page"):
            logger.info("OCR 引擎不支持 reocr_page，跳过缺口补充")
            return doc

        # 需要 refiner 且支持 fill_gap
        refiner = self._get_refiner(llm)
        if refiner is None or not hasattr(refiner, "fill_gap"):
            logger.info("LLM 精修器不支持 fill_gap，跳过缺口补充")
            return doc

        page_map = {
            p.image_path.name: p.image_path for p in pages
        }
        page_order = [p.image_path.name for p in pages]

        doc, filled_count = await self._fill_gaps(
            doc, gaps, page_map, page_order,
            gpu_lock, refiner, report_fn, entity_lexicon,
        )
        if filled_count > 0:
            await self._save_debug(
                output_dir, "after_gap_fill.md", doc.markdown,
            )
        return doc

    async def _fill_gaps(
        self,
        doc: MergedDocument,
        gaps: list[Gap],
        page_map: dict[str, Path],
        page_order: list[str],
        gpu_lock: asyncio.Lock | None,
        refiner: object,
        report_fn: ReportFn,
        entity_lexicon: EntityLexicon | None = None,
    ) -> tuple[MergedDocument, int]:
        """遍历 gap 列表，re-OCR + LLM 提取缺失内容并插入文档。"""
        reocr_cache: dict[str, str] = {}
        markdown = doc.markdown
        filled_count = 0
        profiler = current_profiler()

        for gi, gap in enumerate(gaps):
            report_fn(
                "gap_fill", gi + 1, len(gaps),
                f"补充缺口 {gi + 1}/{len(gaps)}...",
            )

            # 安全检查：after_image 必须在已知页面中
            if gap.after_image not in page_map:
                logger.warning(
                    "gap.after_image=%s 不在已知页面中，跳过",
                    gap.after_image,
                )
                continue

            try:
                with profiler.stage(
                    "llm.gap_fill_one",
                    after_image=gap.after_image,
                    index=gi + 1,
                    total=len(gaps),
                ):
                    filled_text = await self._fill_one_gap(
                        gap, page_map, page_order,
                        reocr_cache, gpu_lock, refiner,
                        entity_lexicon,
                    )
            except Exception:
                logger.warning(
                    "缺口补充失败（after_image=%s），跳过",
                    gap.after_image,
                    exc_info=True,
                )
                continue

            if not filled_text:
                continue

            # 在 markdown 中找到插入点并插入
            markdown = self._insert_gap_content(
                markdown, gap.after_image, page_order, filled_text,
            )
            gap.filled = True
            gap.filled_content = filled_text
            filled_count += 1

        return MergedDocument(
            markdown=markdown,
            images=doc.images,
            gaps=doc.gaps,
        ), filled_count

    async def _fill_one_gap(
        self,
        gap: Gap,
        page_map: dict[str, Path],
        page_order: list[str],
        reocr_cache: dict[str, str],
        gpu_lock: asyncio.Lock | None,
        refiner: object,
        entity_lexicon: EntityLexicon | None = None,
    ) -> str:
        """对单个 gap 做 re-OCR + LLM 提取。

        返回填充内容（空字符串表示无法填充）。
        若启用 PII 脱敏，re-OCR 文本在送入 LLM 前先脱敏。
        """
        # re-OCR 当前页
        current_text = await self._reocr_cached(
            gap.after_image, page_map, reocr_cache, gpu_lock,
        )

        # re-OCR 下一页（如果有）
        idx = page_order.index(gap.after_image)
        next_page_name: str | None = None
        next_page_text: str | None = None
        if idx + 1 < len(page_order):
            next_page_name = page_order[idx + 1]
            next_page_text = await self._reocr_cached(
                next_page_name, page_map, reocr_cache, gpu_lock,
            )

        # PII 脱敏 re-OCR 文本（轻量模式，不调用 LLM）
        if self._config.pii.enable:
            redactor = PIIRedactor(self._config.pii)
            current_text, _ = redactor.redact_snippet(
                current_text, entity_lexicon,
            )
            if next_page_text is not None:
                next_page_text, _ = redactor.redact_snippet(
                    next_page_text, entity_lexicon,
                )

        # LLM 提取缺失内容
        filled: str = await refiner.fill_gap(  # type: ignore[attr-defined]
            gap, current_text, next_page_text, next_page_name,
        )
        return filled

    async def _reocr_cached(
        self,
        page_name: str,
        page_map: dict[str, Path],
        cache: dict[str, str],
        gpu_lock: asyncio.Lock | None,
    ) -> str:
        """带缓存的 re-OCR，同一页只跑一次。"""
        if page_name in cache:
            return cache[page_name]

        image_path = page_map[page_name]
        active_engine = (
            self._engine_manager.engine
            if self._engine_manager is not None
            else self._ocr_engine
        )
        if gpu_lock is not None:
            async with gpu_lock:
                text: str = await active_engine.reocr_page(image_path)  # type: ignore[union-attr]
        else:
            text = await active_engine.reocr_page(image_path)  # type: ignore[union-attr]

        cache[page_name] = text
        return text

    @staticmethod
    def _insert_gap_content(
        markdown: str,
        after_image: str,
        page_order: list[str],
        content: str,
    ) -> str:
        """在 markdown 中定位插入点，将填充内容插入。

        策略：找到 after_image 对应的 page marker，
        然后找到下一个 page marker，在其之前插入内容。
        """
        # 找到所有 page marker 的位置
        markers = list(_PAGE_MARKER_RE.finditer(markdown))

        # 找到 after_image 对应的 marker 索引
        after_marker_idx: int | None = None
        for i, m in enumerate(markers):
            if m.group(1).strip() == after_image:
                after_marker_idx = i
                # 可能有多个同名 marker，取最后一个
                # 但通常每页只有一个

        if after_marker_idx is None:
            # 找不到 page marker，追加到末尾
            return markdown + "\n" + content + "\n"

        # 找到下一页的 page marker
        idx_in_order = page_order.index(after_image)
        insert_pos: int | None = None
        if idx_in_order + 1 < len(page_order):
            next_page = page_order[idx_in_order + 1]
            for m in markers:
                if m.group(1).strip() == next_page:
                    insert_pos = m.start()
                    break

        if insert_pos is not None:
            return (
                markdown[:insert_pos]
                + content + "\n\n"
                + markdown[insert_pos:]
            )

        # 无下一页 marker，追加到文档末尾
        return markdown + "\n" + content + "\n"

    def _get_refiner(
        self,
        llm: LLMConfig | None,
    ) -> LLMRefiner | None:
        """获取 refiner 实例：llm 非空时按请求快照新建，否则复用默认实例。"""
        if llm is None:
            return self._refiner
        return self._create_refiner(llm)

    async def _do_final_refine(
        self,
        doc: MergedDocument,
        output_dir: Path,
        llm: LLMConfig | None,
        report_fn: ReportFn,
    ) -> tuple[MergedDocument, bool]:
        """整篇文档级精修（去跨段重复 + 页眉水印）。"""
        refiner = self._get_refiner(llm)
        if (
            not self._config.llm.enable_final_refine
            or refiner is None
        ):
            return doc, False

        return await self._final_refine(
            refiner, doc, output_dir, report_fn,
        )

    async def _final_refine(
        self,
        refiner: LLMRefiner,
        doc: MergedDocument,
        output_dir: Path,
        report_fn: ReportFn,
    ) -> tuple[MergedDocument, bool]:
        """整篇文档级精修，失败时回退到原文。返回 (文档, 是否截断)。"""
        if not hasattr(refiner, "final_refine"):
            return doc, False

        report_fn(
            "final_refine", 0, 1, "整篇文档级精修...",
        )
        try:
            result: RefinedResult = (
                await refiner.final_refine(doc.markdown)
            )
            await self._save_debug(
                output_dir,
                "final_refined.md",
                result.markdown,
            )
            return MergedDocument(
                markdown=result.markdown,
                images=doc.images,
                gaps=doc.gaps + result.gaps,
            ), result.truncated
        except Exception:
            logger.warning(
                "整篇文档级精修失败，回退到原文",
                exc_info=True,
            )
            return doc, False

    @staticmethod
    def _collect_warnings(
        refined_results: list[RefinedResult],
        all_gaps: list[Gap],
        final_truncated: bool,
    ) -> list[str]:
        """聚合所有警告信息。"""
        warnings: list[str] = []
        for i, r in enumerate(refined_results):
            if r.truncated:
                warnings.append(f"段 {i + 1} 精修输出疑似被截断")
        if final_truncated:
            warnings.append("整篇文档级精修输出疑似被截断")
        for g in all_gaps:
            if not g.filled:
                warnings.append(
                    f"缺口（{g.after_image} 之后）未能自动补充"
                )
        return warnings

    async def _detect_doc_boundaries(
        self,
        merged: MergedDocument,
        llm: LLMConfig | None,
        report_fn: ReportFn,
    ) -> list[DocBoundary]:
        """检测文档边界。"""
        report_fn("doc_boundary", 0, 1, "检测文档边界...")
        refiner = self._get_refiner(llm)
        if refiner is None:
            logger.warning("未配置 LLM refiner，跳过文档边界检测")
            return []
        boundaries = await refiner.detect_doc_boundaries(merged.markdown)
        logger.info("检测到 %d 个文档边界", len(boundaries))
        return boundaries

    @staticmethod
    def _insert_doc_boundaries(
        merged: MergedDocument,
        boundaries: list[DocBoundary],
    ) -> MergedDocument:
        """将文档边界标记插入到markdown中。"""
        if not boundaries:
            return merged

        # 找到所有page marker位置
        page_positions: dict[str, int] = {}
        for m in _PAGE_MARKER_RE.finditer(merged.markdown):
            page_name = m.group(1).strip()
            page_positions[page_name] = m.end()

        # 按位置倒序插入（避免位置偏移）
        insertions: list[tuple[int, str]] = []
        for b in boundaries:
            pos = page_positions.get(b.after_page)
            if pos is not None:
                marker = (
                    f'\n<!-- DOC_BOUNDARY: {{"after_page":"{b.after_page}",'
                    f'"new_title":"{b.new_title}"}} -->\n'
                )
                insertions.append((pos, marker))

        insertions.sort(reverse=True)
        md = merged.markdown
        for pos, marker in insertions:
            md = md[:pos] + marker + md[pos:]

        return MergedDocument(
            markdown=md,
            images=merged.images,
            gaps=merged.gaps,
        )

    async def _redact_pii(
        self,
        merged: MergedDocument,
        llm: LLMConfig | None,
        pii_config: PIIConfig,
        output_dir: Path,
        report_fn: ReportFn,
    ) -> tuple[
        MergedDocument,
        list[RedactionRecord],
        EntityLexicon | None,
        bool,
    ]:
        """PII 脱敏阶段。

        返回 (脱敏后文档, 脱敏记录, 实体词典, 是否阻断云端)。
        """
        report_fn(
            "pii_redaction", 0, 1, "PII 脱敏...",
        )

        redactor = PIIRedactor(pii_config)

        # LLMRefiner Protocol 统一暴露 detect_pii_entities；
        # 本地实现返回空列表，云端实现调用 LLM 做真实识别。
        refiner = self._get_refiner(llm)

        text, records, lexicon = (
            await redactor.redact_for_cloud(
                merged.markdown, refiner,
            )
        )

        await self._save_debug(
            output_dir, "after_pii_redaction.md", text,
        )

        # 判断是否需要阻断云端调用
        cloud_blocked = False
        needs_entity = (
            pii_config.redact_person_name
            or pii_config.redact_org_name
        )
        if (
            needs_entity
            and lexicon is None
            and pii_config.block_cloud_on_detect_failure
        ):
            cloud_blocked = True

        new_doc = MergedDocument(
            markdown=text,
            images=merged.images,
            gaps=merged.gaps,
        )
        return new_doc, records, lexicon, cloud_blocked
