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
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from docrestore.processing.code_context import CodeContextProvider
    from docrestore.processing.code_diagnostics import CodeDiagnostic
    from docrestore.processing.code_file_grouping import SourceFile
    from docrestore.processing.ide_meta_extract import IDEMeta

import aiofiles

from docrestore.llm.base import BaseLLMRefiner, LLMRefiner
from docrestore.llm.cache import LLMCache
from docrestore.llm.cloud import CloudLLMRefiner
from docrestore.llm.egress_gate import (
    CloudEgressPolicy,
    egress_scope,
    update_egress_policy,
)
from docrestore.pipeline.quality_report import (
    UI_NOISE_RESIDUAL_RE,
    QualityIssue,
    QualityReport,
    detect_cleaner_quality,
    detect_code_mode_quality,
    detect_final_refine_quality,
    detect_llm_segment_quality,
    find_duplicate_h2_titles,
)
from docrestore.processing.heading_dedup import dedup_h2_sections
from docrestore.processing.markdown_polish import (
    strip_code_block_line_numbers,
    strip_residual_ui_noise,
)
from docrestore.processing.table_dedup import dedup_html_tables
from docrestore.llm.prompts import (
    extract_first_heading,
    parse_gaps,
)
from docrestore.processing.segmenter import StreamSegmentExtractor
from docrestore.models import (
    Gap,
    LayoutRegion,
    MergedDocument,
    PageOCR,
    PipelineResult,
    PipelineWarning,
    RefineContext,
    RefinedResult,
    TaskProgress,
)
from docrestore.ocr.base import OCREngine
from docrestore.ocr.engine_manager import EngineManager
from docrestore.output.renderer import Renderer
from docrestore.pipeline.config import (
    CodeRestoreConfig,
    ContentCropConfig,
    LLMConfig,
    OCRConfig,
    PIIConfig,
    PipelineConfig,
    PowerPointRestoreConfig,
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
from docrestore.pipeline.rate_controller import RateController
from docrestore.privacy.guard import PIIGuard
from docrestore.privacy.redactor import EntityLexicon
from docrestore.processing.cleaner import OCRCleaner
from docrestore.processing.dedup import (
    IncrementalMerger,
    rewrite_image_refs_to_ocr_dir,
)


#: 流式 Pipeline 延迟 PII 实体检测的页面阈值（见 streaming-pipeline §6）。
_PII_DETECT_THRESHOLD = 5

# 进度回调类型
class ReportFn(Protocol):
    """进度上报回调。

    - `message` 是服务端拼出的人类可读中文（CLI / 日志 / 老客户端 fallback）
    - `message_key` + `message_params` 是 i18n 入口，前端按当前语言渲染，
      服务端不写死任何语言
    """

    def __call__(
        self,
        stage: str,
        current: int,
        total: int,
        message: str = "",
        *,
        message_key: str = "",
        message_params: dict[str, str] | None = None,
    ) -> None:
        ...

# page marker 正则
_PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*(.+?)\s*-->")
_PAGE_DROP_COMMENT_RE = re.compile(
    r"<!--\s*本页内容与上一页[^>]*(?:重复|已去除)[^>]*-->",
)

logger = logging.getLogger(__name__)


#: C/C++ 预处理指令，``#`` 开头但不是注释，遇到要停止 header 收集
_C_PREPROCESSOR_RE = re.compile(
    r"^\s*#\s*"
    r"(include|define|undef|if|ifdef|ifndef|else|elif|endif|"
    r"pragma|error|warning|line)\b",
)

_CODE_REPAIR_LARGE_FILE_LINE_THRESHOLD = 400


def _is_comment_line(stripped: str) -> bool:
    """判断一行（已去除首尾空白）是否属于 leading comment block。

    - ``//`` / ``/*`` / ``*`` / ``*/`` → 一律算注释
    - ``#`` 开头：先排除 C/C++ 预处理指令（``#include`` / ``#define`` 等），
      其余视为 Python/shell/gn 风格注释
    """
    if not stripped:
        return False
    if stripped.startswith(("//", "/*", "*")):
        return True
    if stripped.startswith("#"):
        return _C_PREPROCESSOR_RE.match(stripped) is None
    return False


def _split_leading_comment(text: str) -> tuple[str, str]:
    """切出文件开头的 leading comment block（含其中空行）。

    AGE-50 PII 用：仅对 header 做实体脱敏，body（import 路径/namespace/
    字符串字面量）保持原样，避免误伤代码语义。

    识别规则（保守、跨语言）：
      - 行 1 起，``//`` / ``#`` (排除 C 预处理) / ``/*`` / ``*`` 算注释
      - 注释行之间的空行也归 header（保留 Copyright + 空行 + Author 这类格式）
      - 一旦遇到非注释非空行，停止；最后一行注释之前的所有内容都是 header

    返回 ``(header, body)`` 满足 ``header + body == text``。
    无 leading comment 时返回 ``("", text)``。
    """
    if not text:
        return "", text
    lines = text.split("\n")
    last_comment = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # 空行：暂归 header，等后续行决定
            continue
        if _is_comment_line(stripped):
            last_comment = i
            continue
        break
    if last_comment < 0:
        return "", text
    end = last_comment + 1
    header = "\n".join(lines[:end])
    body = "\n".join(lines[end:])
    if end < len(lines):
        # split + join 丢了行末换行符；header/body 衔接处补回
        header += "\n"
    return header, body


def _pick_cut_points(
    marker_starts: list[int],
    target_positions: list[int],
    total: int,
) -> list[int] | None:
    """从 marker 候选里给每个目标位置挑最近的切点。

    返回 None 表示找不到 N-1 个有效切点（含 marker_starts 耗尽或切点重叠）。
    """
    cut_points: list[int] = []
    used: set[int] = set()
    for target in target_positions:
        best = -1
        best_dist = total + 1
        for idx, pos in enumerate(marker_starts):
            if idx in used or pos == 0:
                continue
            d = abs(pos - target)
            if d < best_dist:
                best_dist = d
                best = idx
        if best < 0:
            return None
        used.add(best)
        cut_points.append(marker_starts[best])
    cut_points.sort()
    # 去重/乱序校验：相邻切点必须严格递增
    for i in range(1, len(cut_points)):
        if cut_points[i] <= cut_points[i - 1]:
            return None
    return cut_points


def _split_by_page_markers(markdown: str, n_chunks: int) -> list[str]:
    """按 <!-- page: --> 边界把 markdown 切成近似等长的 N 块。

    策略：
    - 枚举所有 page marker 的起始位置作为候选切点
    - 目标切点 = 字符数均匀划分位置，取最接近的 page marker 起点
    - 切分后任何一块为空或切点不足 N-1 个 → 返回 [markdown] 让调用方回退

    返回的块之间无重叠，拼接起来等于原文（保序）。
    """
    if n_chunks <= 1:
        return [markdown]
    markers = list(_PAGE_MARKER_RE.finditer(markdown))
    if len(markers) < n_chunks:
        return [markdown]

    total = len(markdown)
    marker_starts = [m.start() for m in markers]
    target_positions = [
        total * (i + 1) // n_chunks for i in range(n_chunks - 1)
    ]
    cut_points = _pick_cut_points(marker_starts, target_positions, total)
    if cut_points is None:
        return [markdown]

    chunks: list[str] = []
    prev = 0
    for cp in cut_points:
        chunks.append(markdown[prev:cp])
        prev = cp
    chunks.append(markdown[prev:])
    if any(not c.strip() for c in chunks):
        return [markdown]
    return chunks


def _has_syntax_dirty_diagnostic(
    diagnostics: list[CodeDiagnostic],
) -> bool:
    """是否存在需要 scoped repair 的语法诊断。"""
    return any(
        diagnostic.status == "syntax_dirty"
        and bool(diagnostic.failing_lines)
        for diagnostic in diagnostics
    )


def _make_repair_progress(
    report_fn: ReportFn, file_index: int, file_total: int,
) -> Callable[[int, int], None]:
    """构造 repair 逐窗口进度回调（#94）。

    病态大文件的 scoped repair 单文件要跑十几个窗口、每个 ~30s，原先只在整文件
    修完才上报一次，前端长时间停在「归类得到 N 个源文件」。此回调让每个窗口都推
    一帧（文件级 current/total 不变，message 带窗口进度），避免看起来卡死。
    """
    def _cb(window: int, windows: int) -> None:
        report_fn(
            "code_refine", file_index + 1, file_total,
            f"代码修复 第 {file_index + 1}/{file_total} 个文件"
            f"（窗口 {window}/{windows}）",
            message_key="progress.codeRepairWindow",
            message_params={
                "current": str(file_index + 1),
                "total": str(file_total),
                "window": str(window),
                "windows": str(windows),
            },
        )
    return _cb


def _apply_pdf_missing_warnings(
    results: list[PipelineResult], missing_by_doc: dict[str, int],
) -> None:
    """把 PDF 缺页数挂到对应文档结果的 warnings（#96，按 doc_dir 匹配，就地改）。

    单 PDF 落根=doc_dir ""、多 PDF=stem；坏页跳过后文档不完整但仍可用（任务仍
    COMPLETED，不翻 error），软降级 warning 让用户知道"缺了几页"。
    """
    if not missing_by_doc:
        return
    for r in results:
        missing = missing_by_doc.get(r.doc_dir)
        if missing:
            r.warnings = [
                *r.warnings,
                PipelineWarning("pdf_pages_missing", {"count": missing}),
            ]


def _augment_metas_with_code_context(
    metas: list[IDEMeta],
    context_provider: CodeContextProvider,
) -> None:
    """把参考源码路径候选追加到 IDEMeta.path_candidates，不覆盖 OCR。"""
    from docrestore.processing.ide_meta_extract import PathCandidate

    for meta in metas:
        query = meta.path or meta.filename
        if not query:
            continue
        seen = {candidate.path for candidate in meta.path_candidates}
        for candidate in context_provider.search_paths(
            query, language=meta.language, limit=3,
        ):
            if candidate.path in seen:
                continue
            seen.add(candidate.path)
            meta.path_candidates.append(PathCandidate(
                path=candidate.path,
                filename=candidate.filename,
                language=candidate.language,
                source=candidate.source,
                confidence=candidate.score,
                raw_text=query,
            ))


def _ocr_config_force_pipeline(
    ocr: OCRConfig | None,
    default_ocr: OCRConfig,
    pipeline_name: str,
) -> OCRConfig | None:
    """把有效 OCR 配置的 ``paddle_pipeline`` 强制为 ``pipeline_name``。

    底座（请求级 ``ocr`` 或 ``default_ocr``）已是目标 pipeline 则原样返回 ``ocr``
    （可能为 None=无请求级覆盖）；否则在底座上 ``model_copy`` 强制覆盖。代码模式
    与 PPT 模式共用，避免两份近乎相同的 force 逻辑各自漂移。
    """
    base = ocr if ocr is not None else default_ocr
    if base.paddle_pipeline == pipeline_name:
        return ocr
    return base.model_copy(update={"paddle_pipeline": pipeline_name})


def _ocr_config_for_code_mode(
    ocr: OCRConfig | None,
    default_ocr: OCRConfig,
) -> OCRConfig | None:
    """代码模式所需的有效 OCR 配置：强制 PaddleOCR basic（产出行级 bbox）。

    basic(PP-OCRv5) 才产 ``text_lines``（行级 bbox）；vl 不产，代码模式会因无可
    组装内容而失败。统一强制使任何代码模式请求都走 basic（B4 H5）。
    """
    return _ocr_config_force_pipeline(ocr, default_ocr, "basic")


def _ocr_config_for_ppt_mode(
    ocr: OCRConfig | None,
    default_ocr: OCRConfig,
) -> OCRConfig | None:
    """PPT 模式所需的有效 OCR 配置：强制 PaddleOCR-VL。

    官方文档结论：PPT 还原所需输出（带格式 markdown + LaTeX 公式 + 化学结构/
    图表裁图 + 阅读序）只有 PaddleOCR-VL 端到端产出；basic(PP-OCRv5) 仅纯文本
    行、PP-StructureV3 无 markdown/无 VLM 语义。强制 ``paddle_pipeline="vl"`` 防止
    默认或误配 basic 让 PPT 静默降级为纯文字拼接（与代码模式强制 basic 对称）。
    """
    return _ocr_config_force_pipeline(ocr, default_ocr, "vl")


def _ocr_config_for_mode(
    *,
    code_enabled: bool,
    ppt_enabled: bool,
    ocr: OCRConfig | None,
    default_ocr: OCRConfig,
) -> OCRConfig | None:
    """按处理模式选有效 OCR 配置：代码模式强制 basic、PPT 模式强制 vl，
    文档模式（或都不启用）原样返回请求级 ``ocr``。code/ppt 互斥由 API 层保证。"""
    if code_enabled:
        return _ocr_config_for_code_mode(ocr, default_ocr)
    if ppt_enabled:
        return _ocr_config_for_ppt_mode(ocr, default_ocr)
    return ocr


def _stitch_final_chunks(chunks: list[str]) -> str:
    """拼接分块 final_refine 的结果。

    - 普通场景每块以 page marker 开头（切分点落在 marker 处），直接 join
    - 末尾清理连续多空行为单空行
    """
    if not chunks:
        return ""
    joined = "\n".join(c.rstrip() for c in chunks)
    # 压多空行
    return re.sub(r"\n{3,}", "\n\n", joined)


def _make_regex_redactor(
    pii_cfg: PIIConfig,
    lexicon: EntityLexicon | None = None,
) -> Callable[[str], str] | None:
    """构造代码模式 prompt 字段（file_path / 源码片段 / 诊断）送云脱敏投影函数
    （#36 + #67 字段级加固），未开 PII 返回 None。

    结构化 PII（手机/邮箱/证件/卡/凭据/host/内链）+ 自定义词走 ``full``（§9.5：
    prompt 字段不随正文降 ``tokens_only``）；``lexicon`` 非空时**额外施实体替换**
    （人名/机构名）——实体是精确串替换，不误伤 import 路径/namespace/标识符（误伤
    风险只来自结构化正则，与实体无关）。lexicon 由 ``_redact_code_pii`` 检测后回传。

    与出云闸口（#67）的关系：闸口在 ``_call_llm`` 已对全部出云内容兜底实体替换；
    此处字段级**额外**补结构化脱敏（闸口不跑结构化）——尤其覆盖闸口够不到的
    ``unresolved_items`` 自由文本，属纵深防御。
    """
    if not pii_cfg.enable:
        return None
    guard = PIIGuard(pii_cfg)

    def _redact(text: str) -> str:
        return guard.redact_for_cloud(text, lexicon)

    return _redact


_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def scan_images(image_dir: Path) -> list[Path]:
    """扫描目录下所有支持的图片文件，排序返回。"""
    return sorted(
        p
        for p in image_dir.iterdir()
        if p.suffix.lower() in _IMAGE_EXTS
    )


def resolve_excluded_paths(
    image_dir: Path,
    rels: Sequence[str],
) -> frozenset[Path]:
    """把任务级排除清单（相对 ``image_dir`` 的路径）解析为绝对路径集合。

    - 拒绝绝对路径与含 ``..`` 的 key（路径穿越防护，静默忽略）；
    - **不 resolve 软链**：stage 目录的源图是指向外部真实文件的软链，
      跟随会落到 image_dir 之外；``scan_images`` 返回的也是未 resolve
      路径，同构拼接即可精确比对。
    """
    out: set[Path] = set()
    for rel in rels:
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            continue
        out.add(image_dir / rel)
    return frozenset(out)


async def _filter_excluded_leaves(
    leaf_dirs: list[Path],
    exclude_abs: frozenset[Path],
) -> list[Path]:
    """剔除"排除清单生效后剩余图为空"的叶子目录。"""
    kept: list[Path] = []
    for d in leaf_dirs:
        imgs = await asyncio.to_thread(scan_images, d)
        if any(p not in exclude_abs for p in imgs):
            kept.append(d)
    return kept


#: 裁剪框 (x0, y0, x1, y1)。与 content_crop.CropBoxTuple 同构；本地定义避免
#: 顶层 import content_crop 引入 cv2 硬依赖（cv2 调用均走分支内延迟导入）。
_CropBoxTuple = tuple[int, int, int, int]


def resolve_crop_boxes(
    image_dir: Path,
    boxes: Mapping[str, _CropBoxTuple],
) -> dict[Path, _CropBoxTuple]:
    """把用户裁剪框（相对 ``image_dir`` 的路径 → 框）解析为绝对路径键。

    净化规则与 ``resolve_excluded_paths`` 一致：拒绝绝对路径与含 ``..`` 的
    key（静默忽略），不 resolve 软链。
    """
    out: dict[Path, _CropBoxTuple] = {}
    for rel, box in boxes.items():
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            continue
        out[image_dir / rel] = box
    return out


@dataclass(frozen=True)
class ImageOverrides:
    """任务级输入图覆盖：排除清单 + 用户手动裁剪框（key 已解析为绝对路径）。

    key 相对**根** image_dir（与前端 detect / crop_boxes 同空间），必须在根
    入口解析后整体下传——叶子目录层按叶子相对路径解析会失配（多文档任务的
    子目录前缀 key 在叶子层拼不出存在的路径，覆盖静默失效）。
    """

    exclude: frozenset[Path]
    crop_boxes: dict[Path, _CropBoxTuple]

    @classmethod
    def resolve(cls, image_dir: Path, ocr: OCRConfig) -> ImageOverrides:
        """按 OCR 配置在 ``image_dir``（根）上解析两类覆盖。"""
        return cls(
            exclude=resolve_excluded_paths(image_dir, ocr.exclude_images),
            crop_boxes=resolve_crop_boxes(image_dir, ocr.crop_boxes),
        )


def _count_images(d: Path) -> int:
    """统计目录下图片文件数量（不递归，与 scan_images 一致）。"""
    try:
        return sum(
            1 for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )
    except OSError:
        return 0


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


def _scan_pdfs(image_dir: Path) -> list[Path]:
    """扫描目录根层的 PDF 文件，排序返回（不递归，与 scan_images 同层语义）。"""
    return sorted(
        p
        for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


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

        # 只要配置了 model 就预建 refiner —— 它是"LLM 客户端能力"，PII 实体
        # 检测 / 代码头脱敏也复用它，不受 enable_refine 影响。是否做精修的策略
        # 开关在 `_get_refiner(for_refine=True)` 里判 enable_refine，不在这里拦。
        if self._refiner is None and self._config.llm.model:
            self._refiner = self._create_refiner(self._config.llm)

    async def _expand_pdfs(
        self, image_dir: Path,
    ) -> tuple[list[PipelineResult], dict[str, int]]:
        """摄取入口 PDF 展开（Epic A）：image_dir 根层 *.pdf 逐页渲染成 PNG。

        - 单 PDF → 渲染到 image_dir 根（命中 process_many 快路）；
        - 多 PDF → 各渲染到 ``{safe_stem}/`` 子目录（多文档分支，一个 PDF 一个结果）。

        统一 ``{safe_stem}_`` 命名前缀保 basename 全局唯一（净化后撞名加后缀去重）。
        渲染纯 CPU/IO，用 ``asyncio.to_thread`` 包裹、不持 gpu_lock。坏 / 加密 PDF
        转占位失败结果返回，交 process_tree 合入 results（复用部分失败聚合）。
        """
        if not self._config.pdf.enable:
            return [], {}

        from docrestore.pipeline.render import render_pdf_to_dir, safe_pdf_stem

        pdfs = await asyncio.to_thread(_scan_pdfs, image_dir)
        if not pdfs:
            return [], {}

        cfg = self._config.pdf
        single = len(pdfs) == 1
        used: set[str] = set()
        failures: list[PipelineResult] = []
        # #96：部分缺页（rendered<expected，坏页跳过但非整篇失败）→ 缺页数按 doc_dir
        # 回传（单 PDF=根 ""、多 PDF=stem），由 process_tree 挂到对应结果 warnings。
        missing_by_doc: dict[str, int] = {}
        for pdf in pdfs:
            base = safe_pdf_stem(pdf.name)
            stem, suffix = base, 2
            while stem in used:  # 净化后撞名去重（"a b.pdf" 与 "a_b.pdf" → a_b）
                stem, suffix = f"{base}_{suffix}", suffix + 1
            used.add(stem)
            out_dir = image_dir if single else image_dir / stem
            doc_key = "" if single else stem
            try:
                render = await asyncio.to_thread(
                    render_pdf_to_dir,
                    pdf,
                    out_dir,
                    cfg=cfg,
                    name_prefix=f"{stem}_",
                )
            except Exception as exc:
                logger.warning(
                    "PDF 渲染失败（记为部分失败）: %s: %s",
                    pdf.name, exc, exc_info=exc,
                )
                failures.append(
                    PipelineResult(
                        output_path=out_dir / "document.md",
                        markdown="",
                        doc_dir=doc_key,
                        error=f"{type(exc).__name__}: {str(exc)[:200]}",
                    ),
                )
                continue
            missing = render.expected - render.rendered
            if missing > 0:
                missing_by_doc[doc_key] = missing
        return failures, missing_by_doc

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
        code: CodeRestoreConfig | None = None,
        ppt: PowerPointRestoreConfig | None = None,
    ) -> list[PipelineResult]:
        """统一入口：处理叶子目录，或多子目录 → warmup cold start 并发。

        - 输入目录本身含图片 → 直接 `process_many()`（单文档）
        - 含多个子目录 → 按页数降序，最长子目录先串行 warmup（让
          `RateController` 完成冷启动采样），再并发剩余子目录
        - 返回 `list[PipelineResult]`，每个子目录一份（单目录 list 长度 1）
        """
        async with self._task_profiler(output_dir) as (profiler, _is_root):
            with profiler.stage(
                "pipeline.total",
                image_dir=str(image_dir),
                mode="tree",
            ):
                # Epic A：PDF 输入 → 逐页 PNG 展开（摄取入口，未持 gpu_lock）。
                # 单 PDF 落根命中 process_many 快路，多 PDF 分子目录走多文档分支；
                # 坏 PDF 转占位失败结果，合入返回供 TaskManager 聚合。
                pdf_failures, pdf_missing = await self._expand_pdfs(image_dir)

                leaf_dirs = await asyncio.to_thread(
                    find_image_dirs, image_dir,
                )
                if not leaf_dirs:
                    if pdf_failures:
                        return pdf_failures  # 全部 PDF 渲染失败 → task FAILED
                    msg = f"未找到图片文件: {image_dir}"
                    raise FileNotFoundError(msg)

                # 任务级覆盖（排除清单 + 用户裁剪框，key 相对根 image_dir）：
                # 必须在根入口解析为绝对路径后整体下传（叶子层解析会失配）；
                # 剩余图为空的叶子目录整个跳过
                overrides = ImageOverrides.resolve(
                    image_dir, ocr or self._config.ocr,
                )
                if overrides.exclude:
                    leaf_dirs = await _filter_excluded_leaves(
                        leaf_dirs, overrides.exclude,
                    )
                    if not leaf_dirs:
                        msg = f"图片已全部被排除: {image_dir}"
                        raise FileNotFoundError(msg)

                # 单目录：直接委托 process_many（无需 warmup）
                if len(leaf_dirs) == 1 and leaf_dirs[0] == image_dir:
                    result = await self.process_many(
                        image_dir, output_dir, on_progress,
                        llm, gpu_lock, pii, ocr, code=code, ppt=ppt,
                        overrides=overrides,
                    )
                    _apply_pdf_missing_warnings([result], pdf_missing)
                    return [result, *pdf_failures]

                # 多子目录：warmup cold start + 并发剩余（详见 _process_subdirs）
                results = await self._process_subdirs(
                    leaf_dirs, image_dir, output_dir, on_progress,
                    llm, gpu_lock, pii, ocr, code, ppt, overrides,
                )
                _apply_pdf_missing_warnings(results, pdf_missing)
                return [*results, *pdf_failures]

    async def _process_subdirs(
        self,
        leaf_dirs: list[Path],
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        pii: PIIConfig | None,
        ocr: OCRConfig | None,
        code: CodeRestoreConfig | None,
        ppt: PowerPointRestoreConfig | None,
        overrides: ImageOverrides,
    ) -> list[PipelineResult]:
        """多子目录分支：按页数降序 warmup cold start，再并发剩余子目录。

        - leaves 按页数降序（最长子目录作 warmup 样本源最稳）
        - RateController 全局共享：warmup 期间采集 3+ 个 LLM 样本，剩余子目录读到的
          target_segment_chars() 已是解析解 L*
        - 严格"先串行 warmup → 等 cold_start_done → 再 gather 剩余"，不再 LPT
          （LPT 在 gather 下 acquire 顺序被 async IO race 污染）
        - 容错：某子目录失败不拖垮其他，异常转占位 PipelineResult；CancelledError
          不吞，外层 cancel（shutdown / 用户取消）应一路传播
        """
        leaves_sorted = sorted(
            leaf_dirs,
            key=lambda p: (-_count_images(p), str(p)),
        )
        controller = RateController(self._config.llm)
        warmup_leaf, *rest = leaves_sorted

        warmup_task = asyncio.create_task(
            self._process_leaf(
                0, warmup_leaf, image_dir, output_dir,
                on_progress, llm, gpu_lock, pii, ocr, code, ppt,
                total=len(leaves_sorted),
                controller=controller,
                overrides=overrides,
            ),
            name=f"warmup-leaf-{warmup_leaf.name}",
        )
        # 仅云端流式文档精修才需冷启动校准段长 L*；code/PPT/关精修/未配 model 的
        # 多目录任务不走该路径，跳过 wait_cold_start 直接并发，免白等最长 60s（#44）。
        if self._will_stream_refine(llm, code, ppt):
            try:
                await controller.wait_cold_start()
            except BaseException:
                warmup_task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, Exception,
                ):
                    await warmup_task
                raise

        rest_tasks = [
            asyncio.create_task(
                self._process_leaf(
                    i + 1, leaf, image_dir, output_dir,
                    on_progress, llm, gpu_lock, pii, ocr, code, ppt,
                    total=len(leaves_sorted),
                    controller=controller,
                    overrides=overrides,
                ),
                name=f"leaf-{leaf.name}",
            )
            for i, leaf in enumerate(rest)
        ]
        raw = await asyncio.gather(
            warmup_task, *rest_tasks, return_exceptions=True,
        )
        leaves_in_order = [warmup_leaf, *rest]
        results: list[PipelineResult] = []
        for leaf, item in zip(leaves_in_order, raw, strict=True):
            if isinstance(item, asyncio.CancelledError):
                raise item
            if isinstance(item, BaseException):
                rel = leaf.relative_to(image_dir)
                logger.warning(
                    "子目录 %s 处理失败（记为部分失败）: %s",
                    rel, item, exc_info=item,
                )
                results.append(
                    PipelineResult(
                        output_path=output_dir / rel / "document.md",
                        markdown="",
                        doc_dir=str(rel),
                        error=f"{type(item).__name__}: {str(item)[:200]}",
                    ),
                )
            else:
                results.append(item)
        return results

    async def _process_leaf(
        self,
        index: int,
        leaf: Path,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        pii: PIIConfig | None,
        ocr: OCRConfig | None,
        code: CodeRestoreConfig | None,
        ppt: PowerPointRestoreConfig | None = None,
        *,
        total: int,
        controller: RateController | None = None,
        overrides: ImageOverrides | None = None,
    ) -> PipelineResult:
        """process_tree 并行分支：处理单个叶子目录并补全 doc_dir。

        `controller` 非空时使用共享实例（warmup cold start 复用）。
        `overrides` 为根入口解析好的任务级覆盖（排除 + 用户框），必须转传——
        漏传时 _stream_pipeline 会按叶子目录重解析，根相对 key 失配静默失效。
        """
        profiler = current_profiler()
        rel = leaf.relative_to(image_dir)
        sub_output = output_dir / rel

        logger.info(
            "process_tree: [%d/%d] %s", index + 1, total, rel,
        )

        wrapped_progress = self._wrap_progress(
            on_progress, str(rel), index, total,
        )

        with profiler.stage(
            "pipeline.subdir",
            subdir=str(rel),
            index=index + 1,
            total=total,
        ):
            result = await self.process_many(
                leaf, sub_output, wrapped_progress,
                llm, gpu_lock, pii, ocr,
                code=code, ppt=ppt,
                controller=controller,
                overrides=overrides,
            )

        result.doc_dir = (
            str(rel / result.doc_dir) if result.doc_dir else str(rel)
        )
        return result

    @staticmethod
    def _wrap_progress(
        on_progress: Callable[[TaskProgress], None] | None,
        dir_label: str,
        dir_index: int,
        dir_total: int,
    ) -> Callable[[TaskProgress], None] | None:
        """包装进度回调：标记 subtask + 附加 message 前缀。

        - `p.subtask = dir_label`：前端按该字段分轨渲染每个子目录进度条
        - message 前缀保留，供 CLI / 非结构化客户端阅读
        """
        if on_progress is None:
            return None

        def wrapped(p: TaskProgress) -> None:
            p.subtask = dir_label
            p.message = (
                f"[{dir_index + 1}/{dir_total} {dir_label}] "
                f"{p.message}"
            )
            on_progress(p)

        return wrapped

    async def process_many(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None]
        | None = None,
        llm: LLMConfig | None = None,
        gpu_lock: asyncio.Lock | None = None,
        pii: PIIConfig | None = None,
        ocr: OCRConfig | None = None,
        code: CodeRestoreConfig | None = None,
        ppt: PowerPointRestoreConfig | None = None,
        controller: RateController | None = None,
        overrides: ImageOverrides | None = None,
    ) -> PipelineResult:
        """单文档流式处理：OCR Producer + Stream Processor。

        OCR 边产出，LLM 边消费；RateController 运行时自适应段长。
        一个目录视为一篇文档（不做 LLM 文档聚合拆分）。

        `controller` 非空时跨 process_many 调用共享（process_tree 并行分支
        warmup cold start 使用），否则本次内部临时创建。

        `exclude_abs` 为任务级排除图的绝对路径集合（process_tree 在根目录
        解析后下传）；为 None 时按 ocr 配置对本目录自行解析（直调场景）。
        """
        async with self._task_profiler(output_dir) as (profiler, is_root):
            root_stage = profiler.stage(
                "pipeline.total",
                image_dir=str(image_dir),
                mode="many",
            ) if is_root else contextlib.nullcontext()
            with root_stage:
                # 出云闸口（#67）：每个 leaf 在此安装任务级出云策略（ContextVar
                # task-local，process_tree 并发子目录互不串味）；guard 据请求级
                # pii_cfg 建一次，block_cloud/lexicon 由三模式就绪后 update。
                pii_cfg = pii or self._config.pii
                egress_guard = (
                    PIIGuard(pii_cfg) if pii_cfg.enable else None
                )
                with egress_scope(CloudEgressPolicy(guard=egress_guard)):
                    return await self._stream_pipeline(
                        image_dir, output_dir, on_progress,
                        llm, gpu_lock, pii, ocr, code, ppt, controller,
                        overrides,
                    )

    async def _scan_task_images(
        self,
        image_dir: Path,
        exclude_abs: frozenset[Path],
    ) -> list[Path]:
        """扫描输入图并应用任务级排除清单；剩余为空抛 FileNotFoundError。"""
        images = await asyncio.to_thread(scan_images, image_dir)
        if exclude_abs:
            images = [p for p in images if p not in exclude_abs]
        if not images:
            msg = f"未找到图片文件: {image_dir}"
            raise FileNotFoundError(msg)
        return images

    async def _stream_pipeline(
        self,
        image_dir: Path,
        output_dir: Path,
        on_progress: Callable[[TaskProgress], None] | None,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        pii: PIIConfig | None,
        ocr: OCRConfig | None,
        code: CodeRestoreConfig | None,
        ppt: PowerPointRestoreConfig | None,
        controller: RateController | None,
        overrides: ImageOverrides | None = None,
    ) -> PipelineResult:
        """process_many 的实际实现：启动 OCR Producer + Stream Processor。"""
        await asyncio.to_thread(
            output_dir.mkdir, parents=True, exist_ok=True,
        )

        def _report(
            stage: str,
            current: int,
            total: int,
            message: str = "",
            *,
            message_key: str = "",
            message_params: dict[str, str] | None = None,
        ) -> None:
            if on_progress is not None:
                percent = (
                    (current / total * 100) if total > 0 else 0
                )
                on_progress(TaskProgress(
                    stage=stage, current=current, total=total,
                    percent=round(percent, 1), message=message,
                    message_key=message_key,
                    message_params=dict(message_params or {}),
                ))

        # 任务级覆盖归一：process_tree 链路已在根目录解析并下传；
        # process_many 直调（本目录即根）按本目录解析
        if overrides is None:
            overrides = ImageOverrides.resolve(
                image_dir, ocr or self._config.ocr,
            )
        images = await self._scan_task_images(image_dir, overrides.exclude)
        if self._engine_manager is None and self._ocr_engine is None:
            msg = "OCR 引擎未初始化"
            raise RuntimeError(msg)

        if controller is None:
            controller = RateController(self._config.llm)

        page_queue: asyncio.Queue[PageOCR | None] = asyncio.Queue()
        pages_ref: list[PageOCR] = []
        # #96：本任务 ensure() 时刻捕获的引擎降级原因（生产者同步写入）。绝不读共享
        # live 标志——并发混模式任务会互改全局 degraded_reason，造成误报/漏报。
        degraded_sink: list[str] = []
        pii_cfg = pii or self._config.pii

        # 质量报告收集：每阶段的异常信号汇总到 .quality_report.json
        quality = QualityReport()

        # 熔断器告警订阅：OPEN 时推 `llm_unavailable` 进度帧给前端，
        # finally 里无条件 unsubscribe 防止 listener 泄漏到后续任务。
        # 传 (model, api_base) 与 BaseLLMRefiner._call_llm 用同一 key
        llm_cfg_for_breaker = llm if llm is not None else self._config.llm
        unsub_breaker = await self._subscribe_breaker(
            llm_cfg_for_breaker.model,
            llm_cfg_for_breaker.api_base,
            _report,
        )

        # 请求级 code 覆盖优先；为 None 时回退到 pipeline 启动配置。
        code_cfg = code if code is not None else self._config.code
        # PPT 模式同理（与 code 互斥，由 API 层校验）。
        ppt_cfg = ppt if ppt is not None else self._config.ppt
        # 按模式选有效 OCR 配置：代码模式强制 basic（行级 bbox），PPT 模式强制 vl
        # （只有 PaddleOCR-VL 产 markdown+公式+裁图，见官方文档），文档模式原样。
        ocr_effective = _ocr_config_for_mode(
            code_enabled=code_cfg.enable,
            ppt_enabled=ppt_cfg.enable,
            ocr=ocr,
            default_ocr=self._config.ocr,
        )

        # 自动 content_crop：仅文档模式生效。其余模式一律跳过自动裁剪（仍可手动框）：
        # 代码模式坐标依赖强 + 已有列裁剪 + 文档正文列检测不适配 IDE；PPT 屏摄幻灯无
        # 固定正文列、透视矫正后再裁易误伤图文版式（2026-06-29 回退 §14.2 自动串联，
        # 改回仅手动框）；PDF 渲染页无屏摄侧栏 UI（Epic A D8）。
        from docrestore.pipeline.render import is_pdf_rendered_dir

        skip_content_crop = (
            code_cfg.enable
            or ppt_cfg.enable
            or is_pdf_rendered_dir(image_dir)
        )

        ocr_task = asyncio.create_task(
            self._ocr_producer(
                images, output_dir, gpu_lock, page_queue,
                pages_ref, controller, _report, ocr_effective, pii_cfg,
                degraded_sink=degraded_sink,
                quality=quality, ppt=ppt_cfg,
                content_crop=(
                    None if skip_content_crop else self._config.content_crop
                ),
                overrides=overrides,
            ),
            name=f"ocr-producer-{image_dir.name}",
        )
        try:
            if code_cfg.enable:
                # AGE-8 代码模式：跳过 LLM 流式精修，OCR 收齐后跑 ide_layout
                # → ide_meta_extract → code_assembly → group_into_files →
                # render_code_files，按需 LLM 字符级修正每个 SourceFile
                result = await self._code_pipeline(
                    page_queue, pages_ref, output_dir,
                    llm, pii_cfg, _report, ocr_effective, code_cfg,
                    quality=quality,
                )
            elif ppt_cfg.enable:
                # PPT 模式：透视矫正在 producer 逐页完成，本分支逐页出队
                # → 按页精修（开关开时）→ 单页保序组装 document.md（不跨页去重）
                result = await self._ppt_pipeline(
                    page_queue, output_dir, _report,
                    llm=llm, total=len(images), pii_cfg=pii_cfg,
                    quality=quality,
                )
            else:
                result = await self._stream_process(
                    page_queue, pages_ref, output_dir,
                    llm, gpu_lock, pii_cfg, controller, _report,
                    quality=quality,
                )
        except BaseException:
            # 消费者异常/取消 → 立即取消仍在跑的 OCR 生产者，避免它把剩余图全部
            # OCR 完才结束（持 gpu_lock 阻塞 shutdown / 遗弃任务 + GPU 空转）；
            # 吞掉其 CancelledError 等清理异常，保留消费者原异常向上抛。
            await self._cancel_producer_log_real(ocr_task)
            raise
        finally:
            unsub_breaker()
        # 成功路径：生产者已在自身 finally put(None) 收尾、随即结束；这里 await
        # 让其真实异常（如某页 OCR 失败）浮现为任务失败，而非静默产出截断文档。
        await ocr_task

        # 写质量报告（只读操作，放在 finally 外，shutdown 异常时不写）
        try:
            await quality.dump_to_file(
                output_dir / ".quality_report.json",
            )
        except OSError:
            logger.warning(
                "质量报告写入失败 (path=%s)",
                output_dir / ".quality_report.json",
                exc_info=True,
            )
        # #96：VL 退本地推理降级，所有模式（doc/ppt/code）统一在此挂任务级 warning，让
        # "请求 VL 实际跑本地"在文档结果侧也可见。原因码由生产者在本任务 ensure() 时刻
        # 同步捕获（degraded_sink），不读共享 live 标志，避免并发任务互相污染。
        # 无降级时返回空列表，extend 为 no-op（不加分支）。
        captured_reason = degraded_sink[0] if degraded_sink else ""
        result.warnings = [
            *result.warnings, *self._engine_degraded_warnings(captured_reason),
        ]
        return result

    @staticmethod
    async def _cancel_producer_log_real(
        ocr_task: asyncio.Task[None],
    ) -> None:
        """取消并 await OCR 生产者，记录其真实异常（避免被消费者异常掩盖）。

        消费者异常（如「OCR producer 未产出任何页」）往往是生产者首图异常的
        二次结果；旧实现 ``suppress(Exception)`` 把根因吞掉，难以排障。此处取消
        后 await，对 ``CancelledError`` 静默、对真实异常 ``warning`` 记录后由调用
        方重新抛出消费者原异常（不改变抛出语义）。
        """
        ocr_task.cancel()
        try:
            await ocr_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning(
                "OCR 生产者异常（被消费者异常掩盖，仅记录不改变抛出）",
                exc_info=True,
            )

    @staticmethod
    async def _subscribe_breaker(
        model: str,
        api_base: str,
        report_fn: ReportFn,
    ) -> Callable[[], None]:
        """订阅 per-(model, api_base) 熔断器的 OPEN 事件，翻译为
        ``llm_unavailable`` 进度帧。

        返回 unsubscribe 句柄；空 model 时返回 no-op。
        """
        if not model:
            return lambda: None
        from docrestore.llm.circuit_breaker import get_breaker
        breaker = await get_breaker(model, api_base)

        def listener(m: str, open_until: float) -> None:
            remain_s = max(0.0, open_until - time.monotonic())
            report_fn(
                "llm_unavailable", 0, 0,
                f"LLM provider 暂不可用 ({m})，"
                f"已熔断 {remain_s:.0f}s，段级精修降级",
                message_key="progress.llmUnavailable",
                message_params={
                    "model": m,
                    "cool_down_s": f"{remain_s:.0f}",
                },
            )

        return breaker.subscribe_open(listener)

    async def _ppt_pipeline(  # noqa: C901
        self,
        page_queue: asyncio.Queue[PageOCR | None],
        output_dir: Path,
        report_fn: ReportFn,
        *,
        llm: LLMConfig | None,
        total: int,
        pii_cfg: PIIConfig | None = None,
        quality: QualityReport | None = None,
    ) -> PipelineResult:
        """PPT 模式分支：逐页 OCR 出队 → 重写图片引用 →（开关开时）按页 LLM
        精修 → 单页保序组装合并为单个 document.md（不跨页去重）。

        透视矫正在 ``_ocr_producer`` 逐页前处理。**按页精修在出队循环内完成**：
        精修第 i 页时 producer 可并行 OCR 第 i+1 页（与文档模式段级精修同样的
        OCR/LLM 重叠）。是否精修由 ``_get_refiner`` 统一开关控制
        （``llm.enable_refine=False`` → refiner=None → 跳过，回退原始组装）。
        ``total`` 为预期页数（``len(images)``），仅用于进度与精修上下文。
        """
        from docrestore.output.ppt_renderer import render_ppt_document

        refiner = self._get_refiner(llm)
        refining = refiner is not None
        llm_cfg = llm if llm is not None else self._config.llm
        # 实体脱敏：开 PII 时按已收页文本建一次 lexicon，每页送云端精修前替换
        # 人名/机构名；结构化 PII 已由 producer 入队前正则脱敏。
        pii = pii_cfg if pii_cfg is not None else PIIConfig()
        guard = PIIGuard(pii) if pii.enable else None
        entity_lexicon: EntityLexicon | None = None
        pii_done = False
        raw_accum: list[str] = []
        # 段级精修缓存（resume 复用 output_dir 时按页命中）；enabled 关联 refiner：
        # 关精修（refiner=None）时禁用 → LLMCache 不建目录，不留空 .llm_cache/。
        cache = LLMCache(
            output_dir / ".llm_cache",
            enabled=llm_cfg.enable_cache and refining,
        )

        ordered_pages: list[PageOCR] = []
        bodies: list[str] = []
        pending: list[int] = []  # 早窗口推迟精修的页在 bodies 中的下标
        idx = 0

        async def _finish_page(bi: int) -> None:
            """精修 bodies[bi]（refining 时，送云端前用就绪词表脱敏人名/机构名）
            并报进度。闭包读 refiner/refining/entity_lexicon 调用时的当前值。"""
            if refining:
                # slide_mode：用 SLIDE_REFINE_SYSTEM_PROMPT，只修格式不跨页去重
                result, _used = await self._refine_segment_with_cache(
                    refiner, bodies[bi], bi, total, cache, llm_cfg, quality,
                    slide_mode=True,
                    guard=guard, entity_lexicon=entity_lexicon,
                )
                bodies[bi] = result.markdown
            # 进度文案区分是否真精修：关精修时只是逐页组装，不报"精修"误导用户
            report_fn(
                "ppt_refine" if refining else "ppt_page", bi + 1, total,
                (
                    f"PPT 模式：精修第 {bi + 1}/{total} 页" if refining
                    else f"PPT 模式：处理第 {bi + 1}/{total} 页"
                ),
                message_key=(
                    "progress.pptPage" if refining
                    else "progress.pptPagePlain"
                ),
                message_params={"current": str(bi + 1), "total": str(total)},
            )

        while True:
            page = await page_queue.get()
            if page is None:
                break
            # 图片引用先加 OCR 目录前缀（与文档模式同一真相源），精修在其上做
            body = rewrite_image_refs_to_ocr_dir(page).strip()
            raw_accum.append(body)
            # 积累阈值页后建一次实体词表（仅开 name 开关时实际检测，否则 None）
            if (
                guard is not None
                and not pii_done
                and len(raw_accum) >= _PII_DETECT_THRESHOLD
            ):
                entity_lexicon = await self._detect_entities(
                    "\n".join(raw_accum), pii,
                )
                pii_done = True
                ppt_block_cloud = self._should_block_cloud(
                    entity_lexicon, pii,
                )
                # 出云闸口同步（#67）：按页精修发云端前写入 block_cloud + lexicon。
                update_egress_policy(
                    block_cloud=ppt_block_cloud, lexicon=entity_lexicon,
                )
                if ppt_block_cloud:
                    # fail-closed：检测失败 → 停用按页云端精修，后续页退原文
                    refiner = None
                    refining = False
                    logger.warning(
                        "PPT 模式 PII 实体检测失败且 block_cloud_on_detect_"
                        "failure=True：停用云端精修，后续页退原文（不外发"
                        "人名/机构名）",
                    )
            ordered_pages.append(page)
            bodies.append(body)
            # 早窗口防泄漏：要求实体脱敏且词表未就绪 → 推迟本页精修，避免人名/
            # 机构名在检测完成前外发；词表就绪后统一追平。
            if pii_done or not self._entity_redaction_pending(pii):
                await _finish_page(idx)
            else:
                pending.append(idx)
            idx += 1

        if not ordered_pages:
            msg = "PPT 模式：OCR producer 未产出任何页"
            raise RuntimeError(msg)

        # 短 PPT 兜底：页数不足阈值时上面未建过词表，这里补建一次（name 开关关 → None）
        if guard is not None and not pii_done:
            entity_lexicon = await self._detect_entities(
                "\n".join(raw_accum), pii,
            )
            pii_done = True
            ppt_block_cloud = self._should_block_cloud(entity_lexicon, pii)
            # 出云闸口同步（#67）：短 PPT 推迟页精修发云端前写入策略。
            update_egress_policy(
                block_cloud=ppt_block_cloud, lexicon=entity_lexicon,
            )
            if ppt_block_cloud:
                refiner = None
                refining = False
                logger.warning(
                    "PPT 模式 PII 实体检测失败且 block_cloud_on_detect_failure="
                    "True：停用云端精修，推迟页退原文（不外发人名/机构名）",
                )

        # 早窗口推迟的页：词表就绪后统一精修（送云端前用词表脱敏人名/机构名）
        for bi in pending:
            await _finish_page(bi)

        # 实体脱敏输出兜底：词表就绪前已精修的早窗口页可能漏掉实体，对组装正文
        # 再脱敏一遍（已脱敏页为占位符，幂等无副作用）。
        if entity_lexicon is not None and guard is not None:
            bodies = [
                guard.redact_for_cloud(b, entity_lexicon) for b in bodies
            ]

        report_fn(
            "ppt_render", len(ordered_pages), len(ordered_pages),
            "PPT 模式：组装文档",
            message_key="progress.pptRender",
            message_params={"total": str(len(ordered_pages))},
        )
        # 单页保序组装 + 多页按文件序合并 document.md（复用 Renderer）；
        # bodies 为已重写 + 按页精修结果，render 直接拼装不再重复 rewrite。
        doc_path, memory_md = await render_ppt_document(
            ordered_pages, output_dir,
            output_config=self._config.output,
            bodies=bodies,
        )
        # PPT 版面定位 sidecar（Phase-2b）：落 .ppt_layout.json 位置真相源，供
        # pptx 导出器按 bbox 定位。文字区域 content 过同一 PII 闸口（与
        # document.md 同口径脱敏），图片区域映射到最终输出引用；本子任务用捕获的
        # raw 区域内容（开精修按 idx 锚点重挂留待 subtask4）。非 VL/捕获失败 →
        # build 返回 None 不落盘，导出端 fail-safe 退竖排。
        await self._write_ppt_layout_sidecar(
            ordered_pages, output_dir, guard, entity_lexicon,
        )
        return PipelineResult(
            output_path=doc_path,
            markdown=memory_md,
            images=[r for page in ordered_pages for r in page.regions],
            warnings=[],
        )

    async def _write_ppt_layout_sidecar(
        self,
        ordered_pages: list[PageOCR],
        output_dir: Path,
        guard: PIIGuard | None,
        entity_lexicon: EntityLexicon | None,
    ) -> None:
        """落 PPT 版面定位 sidecar ``.ppt_layout.json``（Phase-2b 位置真相源）。

        每页把捕获的 ``layout_regions`` 转成 sidecar 区域：文字区域 content 过同一
        PII 出云闸口（``redact_for_cloud``，与 ``document.md`` 同口径脱敏，本地产物），
        图片区域映射到最终输出引用（``images/{stem}_N.ext``）。非 VL/无版面区域 →
        ``build_ppt_layout`` 返回 None 不落盘，导出端 fail-safe 退竖排。落盘失败仅告警，
        不阻断主流程（版面定位是增强，缺 sidecar 退竖排）。
        """
        from docrestore.output.ppt_layout import (
            build_ppt_layout,
            layout_region_from_ocr,
            write_ppt_layout,
        )

        def _redact(text: str) -> str:
            """文字区域脱敏：开 PII 时走出云闸口（结构化 + 实体），否则原文。"""
            if guard is None:
                return text
            return guard.redact_for_cloud(text, entity_lexicon)

        def _render_stem(page: PageOCR) -> str:
            """渲染期裁图命名用的 stem：与 ``Renderer`` 一致，取 ``page.output_dir``
            （``{stem}_OCR`` / 矫正后 ``{stem}_after_OCR`` / ``{stem}_cropped_OCR``）
            去掉 ``_OCR``。**不能用 ``page.image_path.stem``**：PPT 矫正后 OCR 跑在
            ``{stem}_after.jpg`` 上、裁图落 ``images/{stem}_after_N.jpg``，而
            producer 已把 ``image_path`` 改回原图（stem 无 ``_after``）。"""
            ocr_dir = page.output_dir
            if ocr_dir is not None and ocr_dir.name.endswith("_OCR"):
                return ocr_dir.name[: -len("_OCR")]
            return page.image_path.stem

        layout_pages = [
            (
                page.image_path.name,
                page.image_size,
                [
                    layout_region_from_ocr(
                        region,
                        stem=_render_stem(page),
                        content=_redact(region.content),
                    )
                    for region in page.layout_regions
                ],
            )
            for page in ordered_pages
        ]
        layout = build_ppt_layout(layout_pages)
        if layout is None:
            return
        try:
            await asyncio.to_thread(write_ppt_layout, output_dir, layout)
        except OSError:
            logger.warning(
                "PPT 版面 sidecar 落盘失败（不阻断主流程，导出退竖排）",
                exc_info=True,
            )

    async def _write_doc_layout_sidecar(
        self,
        pages_ref: list[PageOCR],
        output_dir: Path,
        pii_cfg: PIIConfig,
        entity_lexicon: EntityLexicon | None,
    ) -> None:
        """落通用版面 sidecar ``.layout.json``（Epic E 光标↔原图高亮真相源）。

        每页把捕获的 ``layout_regions`` 转成 sidecar 块（``bbox + label + text``）：
        文字过同一 PII 出云闸口（``redact_for_cloud``，与 ``document.md`` 同口径——
        PII 开时脱敏后再落，保证前端拿光标块文字与 sidecar 文字归一化一致可匹配）。
        非 VL / 无版面区域 → ``build_doc_layout`` 返回 None 不落盘，前端无数据不高亮。
        落盘失败仅告警，不阻断主流程（高亮是增强）。
        """
        from docrestore.output.layout_sidecar import (
            build_doc_layout,
            layout_block_from_region,
            write_doc_layout,
        )
        from docrestore.output.ppt_layout import resolve_output_image_ref

        guard = PIIGuard(pii_cfg) if pii_cfg.enable else None

        def _redact(text: str) -> str:
            """文字脱敏：PII 开时走出云闸口（结构化 + 实体），否则原文。"""
            if guard is None:
                return text
            return guard.redact_for_cloud(text, entity_lexicon)

        def _image_ref(page: PageOCR, region: LayoutRegion) -> str:
            """图片 / 图表区域：OCR 相对引用 → 最终输出引用，对齐 markdown <img src>。

            命名 stem 用 OCR 目录名去 ``_OCR``（与 renderer 同源；裁剪/矫正时是处理图
            stem，如 ``page01_crop``）。非图片区域 / 无引用 / 无 OCR 目录 → 空。
            """
            if not region.image_ref or page.output_dir is None:
                return ""
            stem = page.output_dir.name.removesuffix("_OCR")
            return resolve_output_image_ref(stem, region.image_ref)

        def _build_and_write() -> None:
            """构建 sidecar（含 PII 脱敏电池，CPU/NER 密集）+ 落盘。

            整体在线程里跑：``_redact`` 逐区域走 redact_for_cloud（regex + 本地 NER），
            大文档多页多区域时同步跑会阻塞事件循环（原实现只 offload 落盘）。
            ``asyncio.to_thread`` 传播 contextvars，出云脱敏策略不受影响。
            """
            layout_pages = [
                (
                    page.image_path.name,
                    page.image_size,
                    [
                        layout_block_from_region(
                            region,
                            text=_redact(region.content),
                            image_ref=_image_ref(page, region),
                        )
                        for region in page.layout_regions
                    ],
                )
                for page in pages_ref
            ]
            layout = build_doc_layout(layout_pages)
            if layout is not None:
                write_doc_layout(output_dir, layout)

        try:
            await asyncio.to_thread(_build_and_write)
        except OSError:
            logger.warning(
                "版面高亮 sidecar 落盘失败（不阻断主流程，前端不高亮）",
                exc_info=True,
            )

    async def _write_code_layout_sidecar(
        self,
        sources: list[SourceFile],
        output_dir: Path,
    ) -> None:
        """落代码版面 sidecar ``.code_layout.json``（#93 悬停行↔原图放大真相源）。

        逐行取胜出页 bbox（``build_code_layout``，重叠区按 ``line_provenance``）；
        只含 ``line_no + page + bbox``、无正文 → 无 PII 面、无需脱敏。无任何行
        bbox（非 VL 引擎）→ ``build_code_layout`` 返回 None 不落盘，前端不放大。
        落盘失败仅告警，不阻断主流程（放大镜是增强）。
        """
        from docrestore.output.code_layout_sidecar import (
            build_code_layout,
            write_code_layout,
        )

        layout = build_code_layout(sources)
        if layout is None:
            return
        try:
            await asyncio.to_thread(write_code_layout, output_dir, layout)
        except OSError:
            logger.warning(
                "代码版面 sidecar 落盘失败（不阻断主流程，前端不放大）",
                exc_info=True,
            )

    async def _code_pipeline(  # noqa: C901
        self,
        page_queue: asyncio.Queue[PageOCR | None],
        pages_ref: list[PageOCR],
        output_dir: Path,
        llm: LLMConfig | None,
        pii_cfg: PIIConfig,
        report_fn: ReportFn,
        ocr_cfg: OCRConfig | None,
        code_cfg: CodeRestoreConfig,
        quality: QualityReport | None = None,
    ) -> PipelineResult:
        """AGE-8 代码模式分支：OCR 收齐 → 代码链 → render_code_files。

        只消费 page_queue 直到哨兵；不做 markdown 合并/精修。LLM 仅做单文件
        字符级修正（CodeLLMRefiner），失败/超时回退原文。
        """
        from docrestore.llm.code_refine import CodeLLMRefiner, CodeRefineResult
        from docrestore.output.code_renderer import render_code_files
        from docrestore.processing.code_assembly import assemble_columns
        from docrestore.processing.code_column_ocr import (
            ColumnOCRConfig,
            rerun_column_ocr,
        )
        from docrestore.processing.code_context import create_code_context_provider
        from docrestore.processing.code_file_grouping import (
            GroupingConfig,
            PageColumn,
            group_into_files,
        )
        from docrestore.processing.code_line_ledger import (
            LineLedger,
            build_line_ledger,
        )
        from docrestore.processing.code_path_reconcile import (
            ReconcileConfig,
            build_vocabulary,
            reconcile_paths,
        )
        from docrestore.processing.ide_layout import analyze_layout
        from docrestore.processing.ide_meta_extract import extract_ide_metas
        context_provider = create_code_context_provider(code_cfg.context_root)

        # 1. 排空 OCR 队列；pages_ref 已被 producer 填充
        while True:
            page = await page_queue.get()
            if page is None:
                break

        if not pages_ref:
            msg = "代码模式：OCR producer 未产出任何页"
            raise RuntimeError(msg)

        # 2. 每张图跑 ide_layout / extract_metas / assemble_columns，组装 PageColumn
        report_fn(
            "code_layout", 0, len(pages_ref),
            "代码模式：分析 IDE 布局",
            message_key="progress.codeLayout",
            message_params={"current": "0", "total": str(len(pages_ref))},
        )
        all_pcs: list[PageColumn] = []
        ledgers: dict[tuple[str, int], LineLedger] = {}
        missing_line_pages: list[str] = []
        for i, page in enumerate(pages_ref):
            text_lines = page.text_lines
            if not text_lines:
                missing_line_pages.append(page.image_path.name)
                logger.warning(
                    "代码模式：page %s 无 text_lines（当前 OCR 引擎未提供行级输出）",
                    page.image_path.stem,
                )
                continue
            try:
                from PIL import Image
                with Image.open(page.image_path) as img:
                    image_size = img.size
            except OSError as exc:
                # 用 bbox 兜底（max x2,y2）；记 warning：兜底尺寸通常小于真实画幅
                # （不含右/下留白），会让列检测/版面分析略偏，应可见而非静默。
                logger.warning(
                    "代码模式：打开原图失败，改用 bbox 兜底尺寸"
                    "（版面可能略偏）: %s: %s",
                    page.image_path.name, exc,
                )
                image_size = (
                    max((ln.bbox[2] for ln in text_lines), default=0),
                    max((ln.bbox[3] for ln in text_lines), default=0),
                )
            layout = analyze_layout(text_lines, image_size)
            if code_cfg.secondary_column_ocr:
                secondary_ocr_engine = await self._resolve_ocr_engine(
                    ocr_cfg, report_fn,
                )
                layout = await rerun_column_ocr(
                    page,
                    layout,
                    secondary_ocr_engine,
                    output_dir,
                    ColumnOCRConfig(
                        enabled=True,
                        scale=code_cfg.secondary_column_ocr_scale,
                        padding_px=code_cfg.secondary_column_ocr_padding_px,
                        contrast=code_cfg.secondary_column_ocr_contrast,
                        sharpness=code_cfg.secondary_column_ocr_sharpness,
                    ),
                )
            metas = extract_ide_metas(layout)
            if context_provider is not None:
                # search_paths 首次会 rglob 整个参考源码树 + 逐文件 stat/读取，
                # 是阻塞 IO，放到线程里跑避免阻塞事件循环（B7 S3）。
                await asyncio.to_thread(
                    _augment_metas_with_code_context, metas, context_provider,
                )
            columns = assemble_columns(layout)
            page_stem = page.image_path.stem
            for col, meta, col_lines in zip(
                columns, metas, layout.columns, strict=True,
            ):
                # Stage 0：行账本完整性校验（AGE-79）。用该栏的源 text_lines
                # （而非整页，兼容二次 column OCR）回查行号↔文本配对，把列级风险
                # flag 并入 col.flags，经 quality_report / code_renderer 暴露。
                ledger = build_line_ledger(page_stem, col, col_lines)
                col.flags.extend(ledger.flags)
                ledgers[(page_stem, col.column_index)] = ledger
                all_pcs.append(PageColumn(
                    page_stem=page_stem,
                    column_index=col.column_index,
                    meta=meta,
                    column=col,
                ))
            report_fn(
                "code_layout", i + 1, len(pages_ref),
                f"分析 {i + 1}/{len(pages_ref)}",
                message_key="progress.codeLayout",
                message_params={
                    "current": str(i + 1),
                    "total": str(len(pages_ref)),
                },
            )

        if not all_pcs:
            if missing_line_pages:
                pages = ", ".join(missing_line_pages[:5])
                suffix = ""
                if len(missing_line_pages) > 5:
                    suffix = f" 等 {len(missing_line_pages)} 页"
                msg = (
                    "代码模式需要 OCR 引擎在 PageOCR.text_lines 中提供行级 bbox；"
                    f"当前任务未获得任何行级 OCR 输出（{pages}{suffix}）。"
                    "请切换到支持行级输出的 OCR 引擎，或为当前引擎实现 text_lines。"
                )
            else:
                msg = "代码模式已获得行级 OCR 输出，但未识别出可组装的代码列。"
            raise RuntimeError(msg)

        # 3. Stage 1：批量文件名/路径归一（AGE-80）。全 batch 建权威词表，把
        # 少数派噪声 path 碎片 snap 回权威值，就地改写 meta，再交给归类。
        reconcile_cfg = ReconcileConfig(
            support_threshold=code_cfg.vocab_support_threshold,
            min_frequency=code_cfg.vocab_min_frequency,
            filename_max_distance=code_cfg.snap_filename_max_distance,
            dir_max_distance=code_cfg.snap_dir_max_distance,
            minority_ratio=code_cfg.snap_minority_ratio,
        )
        vocab = build_vocabulary(
            [pc.meta for pc in all_pcs], reconcile_cfg,
        )
        reconcile_paths(all_pcs, vocab, reconcile_cfg)

        # 4. 跨张归类 + 落盘（S2：行号锚定 + 跨桶救援，传入 S0 行账本）
        grouping_cfg = GroupingConfig(
            overlap_min_lines=code_cfg.overlap_min_lines,
            overlap_confirm_ratio=code_cfg.overlap_confirm_ratio,
            overlap_conflict_ratio=code_cfg.overlap_conflict_ratio,
            rescue_max_orphan_pages=code_cfg.rescue_max_orphan_pages,
        )
        sources = group_into_files(all_pcs, ledgers, grouping_cfg)
        report_fn(
            "code_group", len(sources), len(sources),
            f"代码模式：归类得到 {len(sources)} 个源文件",
            message_key="progress.codeGroup",
            message_params={"count": str(len(sources))},
        )

        # 3.1 OCR 后处理：过滤 IDE UI 噪声 + 保守 OCR 纠错（行数保持）。
        # 必须在 PII 脱敏 / LLM refine 之前 —— 让下游看到的就是已纠错文本，
        # PII regex 才能正确命中邮箱里的 0/O，LLM 也少花精力修这类小毛病。
        from docrestore.processing.ocr_postfix import clean_code_ocr_text
        for src in sources:
            postfix_result = clean_code_ocr_text(src.merged_text, src.language)
            src.merged_text = postfix_result.text
            if postfix_result.flags:
                src.flags = list({*src.flags, *postfix_result.flags})

        # 共享一个 LLM 客户端：PII 实体检测 + 代码字符级精修都用它。用
        # for_refine=False 取——只要 model 配了就拿到客户端，不受 enable_refine
        # 影响（否则"关精修 + 开脱敏"会把代码头的人名/邮箱/公司名检测一并关掉）。
        # 是否做精修单独看 llm_cfg.enable_refine。
        llm_cfg = llm if llm is not None else self._config.llm
        refine_on = llm_cfg.enable_refine
        base_refiner = (
            self._get_refiner(llm, for_refine=False)
            if (llm_cfg.model and sources and (refine_on or pii_cfg.enable))
            else None
        )
        base_refiner_obj = (
            base_refiner if isinstance(base_refiner, BaseLLMRefiner) else None
        )
        pre_refine_diagnostics_by_path: dict[str, list[CodeDiagnostic]] = {}
        if refine_on and base_refiner_obj is not None and sources:
            from docrestore.processing.code_diagnostics import diagnose_source_files

            pre_refine_diagnostics = await asyncio.to_thread(
                diagnose_source_files, sources,
            )
            for diagnostic in pre_refine_diagnostics:
                pre_refine_diagnostics_by_path.setdefault(
                    diagnostic.path, [],
                ).append(diagnostic)

        # 3.5 PII：每个 SourceFile 脱敏——header 全量（regex + 实体 + 自定义词），
        # 正文 regex（结构化 PII + 凭据/token）+ 自定义词但不做实体脱敏（避免误伤
        # import 路径 / namespace / 标识符）。送云端 refine/repair/audit 前完成。
        pii_block_cloud = False
        code_lexicon: EntityLexicon | None = None
        if pii_cfg.enable and sources:
            pii_block_cloud, code_lexicon = await self._redact_code_pii(
                sources, pii_cfg,
            )
        # 出云闸口同步（#67）：code refine/repair/audit 即将发云端，写入 block_cloud
        # 与 code lexicon——闸口据此对诊断自由文本（g++/clang 回显源码行）与
        # file_path 施实体替换，堵 N2（实体 lexicon 此前从未线程化到 code 诊断）。
        update_egress_policy(block_cloud=pii_block_cloud, lexicon=code_lexicon)

        # 4. 可选 LLM 字符级精修（每个 SourceFile 独立调用，失败回退原文）；
        # 受统一精修开关约束，关精修时跳过（但上面的 PII 头脱敏仍照常执行）。
        # fail-closed：实体检测失败且 block_cloud_on_detect_failure 为真时，
        # 跳过整段 refine / repair / audit 云端调用，避免 header 里未脱敏的
        # 人名/机构名外发（退化为不精修的本地输出）。
        if refine_on and base_refiner_obj is not None and not pii_block_cloud:
            from docrestore.llm.code_repair import (
                CodeConsistencyAuditor,
                DiagnosticCodeRepairer,
            )
            from docrestore.processing.code_diagnostics import (
                diagnose_source_files,
            )

            refine_mode = getattr(llm_cfg, "code_refine_mode", "refine")
            # #36：file_path / 源码片段 / 诊断拼进云端 prompt 前的脱敏函数（请求级
            # pii_cfg；未开 PII 则 None，不脱）。三类 refiner（refine/repair/audit）
            # 共用，确保任何送云端的 prompt 字段都先 redact_regex_only。
            prompt_redact = _make_regex_redactor(pii_cfg, code_lexicon)
            code_refiner = CodeLLMRefiner(
                base_refiner_obj, mode=refine_mode, redact=prompt_redact,
            )
            code_repairer = DiagnosticCodeRepairer(
                base_refiner_obj, redact=prompt_redact,
            )
            code_auditor = CodeConsistencyAuditor(
                base_refiner_obj, redact=prompt_redact,
            )
            # #5：精修可能改行数（rewrite/repair），放大镜需「精修后行→原 OCR line_no」
            # 映射；在回写 merged_text 处按原文 vs 精修文 difflib 求映射挂到 src。
            from docrestore.output.code_layout_sidecar import build_refined_line_map
            for i, src in enumerate(sources):
                try:
                    diagnostics = pre_refine_diagnostics_by_path.get(
                        src.path, [],
                    )
                    if _has_syntax_dirty_diagnostic(diagnostics):
                        result = await code_repairer.repair(
                            src,
                            diagnostics,
                            related_sources=sources,
                            context_provider=context_provider,
                            progress_cb=_make_repair_progress(
                                report_fn, i, len(sources),
                            ),
                        )
                        audit_source = replace(
                            src,
                            merged_text=result.refined_text,
                            line_count=(
                                result.refined_text.count("\n") + 1
                                if result.refined_text else 0
                            ),
                        )
                        # repair 可能改变行数：audit 必须基于 refine 后文本重新
                        # 诊断（授权窗口行号对齐 + 正确的接受/拒绝基线），不能沿用
                        # 原文行号的 pre-refine 诊断把窗口打到错误行（B7 C3）。
                        if result.refined_text != src.merged_text:
                            audit_diagnostics = await asyncio.to_thread(
                                diagnose_source_files, [audit_source],
                            )
                        else:
                            audit_diagnostics = diagnostics
                        audit_result = await code_auditor.audit(
                            audit_source,
                            audit_diagnostics,
                            previous_result=result,
                            related_sources=sources,
                            context_provider=context_provider,
                        )
                        if audit_result.refined_text != audit_source.merged_text:
                            result.refined_text = audit_result.refined_text
                        result.flags = list({*result.flags, *audit_result.flags})
                        result.unresolved = audit_result.unresolved
                    elif (
                        src.line_count
                        > _CODE_REPAIR_LARGE_FILE_LINE_THRESHOLD
                    ):
                        result = CodeRefineResult(
                            refined_text=src.merged_text,
                            flags=[
                                "code.repair.skipped_large_file_no_window"
                            ],
                        )
                    else:
                        result = await code_refiner.refine(src)
                    # 先捕原文再覆盖：精修文已落 merged_text（即使下行映射计算异常也
                    # 不丢精修结果）；再按原文 vs 精修文求行映射（守恒→空=identity）。
                    original_text = src.merged_text
                    src.merged_text = result.refined_text
                    src.refined_line_map = build_refined_line_map(
                        original_text, src.merged_text, src.line_no_range[0],
                    )
                    if result.flags:
                        src.flags = list({*src.flags, *result.flags})
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "代码模式 LLM 精修失败 (path=%s)，回退原文",
                        src.path,
                    )
                report_fn(
                    "code_refine", i + 1, len(sources),
                    f"LLM 精修 {i + 1}/{len(sources)}",
                    message_key="progress.codeRefine",
                    message_params={
                        "current": str(i + 1),
                        "total": str(len(sources)),
                    },
                )

        render_result = await render_code_files(
            sources, output_dir, enable_diagnostics=True,
        )
        # 落代码版面 sidecar（悬停行↔原图放大；失败仅告警不阻断）。
        await self._write_code_layout_sidecar(sources, output_dir)
        if quality is not None:
            await detect_code_mode_quality(
                quality,
                sources,
                skipped_paths=render_result.skipped,
                diagnostics=render_result.diagnostics,
            )
        report_fn(
            "code_render", 1, 1,
            f"代码模式：写出 {len(render_result.written_files)} 个文件",
            message_key="progress.codeRender",
            message_params={
                "count": str(len(render_result.written_files)),
            },
        )

        # 5. 构造 PipelineResult：output_path 指向 document.md（兼容旧 UI），
        # markdown 暂留空（前端按 files-index.json 单独渲染）
        return PipelineResult(
            output_path=render_result.document_path,
            markdown="",
            warnings=[
                PipelineWarning(
                    "code_files_summary",
                    {
                        "files": len(render_result.written_files),
                        "skipped": len(render_result.skipped),
                    },
                ),
            ],
        )

    async def _redact_code_pii(
        self,
        sources: list[SourceFile],
        pii_cfg: PIIConfig,
    ) -> tuple[bool, EntityLexicon | None]:
        """对每个 SourceFile 脱敏（in-place），送云端 refine/repair/audit 前执行。

        分 header / body 两段差异化处理（``_split_leading_comment`` 切分）：
        - **leading comment header**：``redact_for_cloud(..., "full")`` = 全量结构化
          PII（手机/邮箱/证件/卡/凭据/host/内链）+ 实体 lexicon（人名/机构名）+
          自定义词。lexicon 只从所有非空 header 拼接检测，``person_names`` /
          ``org_names`` 来自注释，不会污染正文标识符。
        - **正文 body**：``redact_structured(..., "tokens_only")`` = 仅高置信密钥
          token（``sk-``/``gh?_``/``AKIA``/JWT）+ 自定义词，**不做实体脱敏**——避免把
          import 路径 / namespace / 变量名误当人名/机构名替换（AGE-50）；也**不跑**
          KV/手机/邮箱等全量正则——否则 ``password = get_secret()`` 右侧被吞坏代码
          （pii-unification.md §4.2，2026-06-14「稳一点」决策）。

        无 header 的文件也照常脱正文。实体检测改本地 NER（``guard.detect_entities``，
        名字不出本机）；不可用 / 未请求时仅 regex + 自定义词。

        返回 ``block_cloud``：实体检测**已尝试且失败** + ``block_cloud_on_detect_
        failure`` 为真时返回 True，调用方据此跳过后续代码精修的云端调用
        （fail-closed，避免 header 里未脱敏的人名/机构名外发）。其余返回 False。
        """
        if not sources:
            return False, None

        guard = PIIGuard(pii_cfg)
        # 每个文件切出 (header, body)，header + body == merged_text
        split = [_split_leading_comment(s.merged_text) for s in sources]

        # 实体检测仅用所有非空 header 拼接（来源限注释，不取正文标识符）。
        # #36：拼接做实体检测**之前**，先对每个 header 做全量结构化脱敏（结构化
        # PII + 凭据/token + 自定义词先掉）——否则注释里 `Author: 张三 <a@corp.com>`
        # 的邮箱/手机会随 combined 外发。人名/机构名不被 regex 触及，实体检测仍基于
        # （已结构化脱敏的）注释正常工作。检测改本地 NER（guard.detect_entities，名字
        # 不出本机），CPU 阻塞用 to_thread 卸载。
        lexicon: EntityLexicon | None = None
        detect_failed = False
        combined = "\n\n".join(
            guard.redact_structured(h) for h, _ in split if h
        )
        if combined:
            lexicon = await asyncio.to_thread(guard.detect_entities, combined)
            # combined 非空即"已尝试检测"：lexicon=None 仅当已请求实体脱敏却失败
            # （ner_backend=none 属知情放弃，_should_block_cloud 已排除，不阻断）。
            detect_failed = self._should_block_cloud(lexicon, pii_cfg)

        for i, (header, body) in enumerate(split):
            new_header = (
                guard.redact_for_cloud(header, lexicon) if header else ""
            )
            new_body = guard.redact_structured(body, profile="tokens_only")
            sources[i].merged_text = new_header + new_body

        # 检测已尝试且失败 + fail-closed → 通知调用方跳过后续云端精修
        # （_should_block_cloud 已含 block_cloud_on_detect_failure 与 none 排除）；
        # 同时回传 lexicon，供出云闸口对 code 诊断/路径施实体兜底（堵 N2）。
        return detect_failed, lexicon

    async def _resolve_ocr_engine(
        self,
        ocr: OCRConfig | None,
        report_fn: ReportFn,
    ) -> OCREngine:
        """EngineManager 优先；无则用已注入的 `self._ocr_engine`（测试场景）。"""
        if self._engine_manager is not None:
            def _init_progress(msg: str) -> None:
                report_fn("init", 0, 0, msg)
            return await self._engine_manager.ensure(
                ocr, on_progress=_init_progress,
            )
        if self._ocr_engine is not None:
            return self._ocr_engine
        msg = "OCR 引擎未初始化"
        raise RuntimeError(msg)

    async def _ocr_producer(
        self,
        images: list[Path],
        output_dir: Path,
        gpu_lock: asyncio.Lock | None,
        queue: asyncio.Queue[PageOCR | None],
        pages_ref: list[PageOCR],
        controller: RateController,
        report_fn: ReportFn,
        ocr: OCRConfig | None,
        pii_cfg: PIIConfig,
        degraded_sink: list[str] | None = None,
        quality: QualityReport | None = None,
        ppt: PowerPointRestoreConfig | None = None,
        content_crop: ContentCropConfig | None = None,
        overrides: ImageOverrides | None = None,
    ) -> None:
        """OCR 生产者：逐张 OCR → 清洗 → 可选 regex-only PII → 入队。

        异常路径也必须发哨兵（finally），避免 _stream_process 永远阻塞在
        `await queue.get()`。

        ``degraded_sink`` 非空时在本任务 ensure() 返回后**同步**写入引擎降级原因码
        （#96）：ensure 已由 ``_switch_lock`` 序列化、读写间无 await，不被并发任务的
        ensure 抢改——避免读共享 live 标志的误报/漏报。
        """
        profiler = current_profiler()
        total = len(images)
        try:
            engine = await self._resolve_ocr_engine(ocr, report_fn)
            # 紧跟 ensure() 同步捕获本任务降级原因（下一行不得插入 await）。
            if degraded_sink is not None and self._engine_manager is not None:
                degraded_sink.append(self._engine_manager.degraded_reason)
            cleaner = OCRCleaner()
            guard = (
                PIIGuard(pii_cfg) if pii_cfg.enable else None
            )
            for i, img in enumerate(images):
                t0 = time.perf_counter()
                # OCR 前逐页前处理（CPU）：用户手动框（任务级，最优先）/
                # PPT 透视矫正 / 文档自动裁剪。处理后图喂 OCR；page.image_path
                # 改回原图，marker / 前端源图按原文件名匹配。任何失败都回退
                # 原图，不中断 OCR。手动框裁到任务输出目录，绝不写用户目录。
                ocr_input = img
                user_box = (
                    None if overrides is None
                    else overrides.crop_boxes.get(img)
                )
                if user_box is not None:
                    # 手动框：独占（用户显式选定，不再叠自动处理）。
                    from docrestore.processing.content_crop import (
                        crop_page_manual,
                    )
                    cc = content_crop or ContentCropConfig()
                    ocr_input = await crop_page_manual(
                        img, output_dir, user_box,
                        save_debug=cc.save_debug,
                        debug_dir=cc.debug_dir,
                    )
                else:
                    # 自动预处理串联（§14.2）：PPT 透视矫正（先）→ content_crop 正文
                    # 裁剪（后，可裁矫正图）。各步 fail-safe 失败/无效回退上一步图。
                    if ppt is not None and ppt.enable and ppt.rectify:
                        from docrestore.processing.slide_rectify import (
                            rectify_page,
                        )
                        ocr_input = await rectify_page(
                            ocr_input, output_dir,
                            save_debug=ppt.rectify_save_debug,
                            debug_dir=ppt.rectify_debug_dir,
                            top_extend_ratio=ppt.rectify_top_extend_ratio,
                        )
                    if content_crop is not None and content_crop.enable:
                        from docrestore.processing.content_crop import (
                            crop_page,
                        )
                        ocr_input = await crop_page(
                            ocr_input, output_dir,
                            save_debug=content_crop.save_debug,
                            debug_dir=content_crop.debug_dir,
                        )
                with profiler.stage("ocr.single", stem=img.stem):
                    if gpu_lock is not None:
                        async with gpu_lock:
                            page = await engine.ocr(ocr_input, output_dir)
                    else:
                        page = await engine.ocr(ocr_input, output_dir)
                if ocr_input is not img:
                    page.image_path = img
                raw_before_clean = page.raw_text
                with profiler.stage(
                    "cleaner.page", stem=page.image_path.stem,
                ):
                    await cleaner.clean(page)

                if quality is not None:
                    await detect_cleaner_quality(
                        quality,
                        page_name=page.image_path.name,
                        raw_text=raw_before_clean,
                        cleaned_text=page.cleaned_text,
                    )

                if guard is not None:
                    page.cleaned_text = guard.redact_structured(
                        page.cleaned_text,
                    )

                await self._save_debug(
                    output_dir,
                    f"{page.image_path.stem}_cleaned.md",
                    page.cleaned_text,
                )

                controller.record_ocr(
                    time.perf_counter() - t0,
                    chars=len(page.cleaned_text),
                )
                pages_ref.append(page)
                await queue.put(page)
                controller.set_queue_depth(queue.qsize())
                report_fn(
                    "ocr", i + 1, total,
                    f"OCR {i + 1}/{total}...",
                    message_key="progress.ocrPage",
                    message_params={
                        "current": str(i + 1),
                        "total": str(total),
                    },
                )
        finally:
            await queue.put(None)

    async def _stream_process(  # noqa: C901
        self,
        page_queue: asyncio.Queue[PageOCR | None],
        pages_ref: list[PageOCR],
        output_dir: Path,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        pii_cfg: PIIConfig,
        controller: RateController,
        report_fn: ReportFn,
        quality: QualityReport | None = None,
    ) -> PipelineResult:
        """消费 OCR 队列：增量合并 → 按 L* 切段 → LLM 精修 → 收齐终结化。"""
        profiler = current_profiler()
        merger = IncrementalMerger(self._config.dedup)
        extractor = StreamSegmentExtractor(
            overlap_lines=self._config.llm.segment_overlap_lines,
        )
        refiner = self._get_refiner(llm)
        llm_cfg = llm if llm is not None else self._config.llm
        # 实体脱敏器：开 PII 时复用 lexicon 把人名/机构名替换在送云端精修前
        # （结构化 PII 已由 producer 入队前正则脱敏）。
        guard = PIIGuard(pii_cfg) if pii_cfg.enable else None
        # LLM 精修缓存：resume 任务复用 output_dir → 直接命中已精修段，省时间
        cache = LLMCache(
            output_dir / ".llm_cache",
            enabled=llm_cfg.enable_cache,
        )

        segmented_offset = 0
        segment_index = 0
        refined_results: list[RefinedResult] = []
        all_gaps: list[Gap] = []
        entity_lexicon: EntityLexicon | None = None
        pii_entity_done = False
        # fail-closed：实体检测失败且 block_cloud_on_detect_failure 为真时，停用
        # 云端精修（refiner→None：后续段/尾段/终结化一律退原文，不外发人名/机构名）。
        pii_block_cloud = False

        with profiler.stage("stream.consume"):
            while True:
                page = await page_queue.get()
                if page is None:
                    break
                merger.add_page(page)

                if (
                    pii_cfg.enable
                    and not pii_entity_done
                    and merger.page_count >= _PII_DETECT_THRESHOLD
                ):
                    entity_lexicon = await self._delayed_pii_detect(
                        merger, pii_cfg,
                    )
                    pii_entity_done = True
                    if self._should_block_cloud(entity_lexicon, pii_cfg):
                        refiner = None
                        pii_block_cloud = True
                        logger.warning(
                            "PII 实体检测失败且 block_cloud_on_detect_failure="
                            "True：停用云端精修，后续段退原文（不外发人名/机构名）",
                        )

                # 早窗口防泄漏：要求实体脱敏时，词表就绪前只攒页不送云端精修，
                # 避免人名/机构名在检测完成前外发；词表就绪后下一次调用一次性追平。
                if pii_entity_done or not self._entity_redaction_pending(
                    pii_cfg
                ):
                    segmented_offset, segment_index = (
                        await self._try_extract_and_refine(
                            merger, extractor, refiner, controller,
                            segmented_offset, segment_index,
                            refined_results, all_gaps, report_fn,
                            cache, llm_cfg, quality,
                            guard=guard, entity_lexicon=entity_lexicon,
                        )
                    )

        # 短文档兜底：页数不足阈值时上面循环未建过 lexicon；这里补建一次，
        # 让尾段脱敏与最终输出兜底拿到词表（仅开 name 开关时实际检测，否则 None）。
        if pii_cfg.enable and entity_lexicon is None:
            entity_lexicon = await self._detect_entities(
                merger.get_markdown(), pii_cfg,
            )
            if self._should_block_cloud(entity_lexicon, pii_cfg):
                refiner = None
                pii_block_cloud = True
                logger.warning(
                    "PII 实体检测失败且 block_cloud_on_detect_failure=True："
                    "停用云端精修，尾段/终结化退原文（不外发人名/机构名）",
                )

        # 早窗口推迟的分段：词表就绪后在此一次性追平（短文档 / 检测后才首次精修）。
        # ≥阈值文档循环内已追平，此处 try_extract 直接返回 None，幂等无副作用。
        if self._entity_redaction_pending(pii_cfg):
            segmented_offset, segment_index = (
                await self._try_extract_and_refine(
                    merger, extractor, refiner, controller,
                    segmented_offset, segment_index,
                    refined_results, all_gaps, report_fn,
                    cache, llm_cfg, quality,
                    guard=guard, entity_lexicon=entity_lexicon,
                )
            )

        # 处理剩余文本（最后一段）
        md = merger.get_markdown()
        if segmented_offset < len(md):
            remaining, _ = extractor.extract_remaining(
                md, segmented_offset,
            )
            if remaining.strip():
                t0 = time.perf_counter()
                with profiler.stage(
                    "llm.refine_one", index=segment_index, tail=True,
                ):
                    result, used_refiner = (
                        await self._refine_segment_with_cache(
                            refiner, remaining, segment_index, 0,
                            cache, llm_cfg, quality,
                            guard=guard,
                            entity_lexicon=entity_lexicon,
                        )
                    )
                if used_refiner:
                    # tail 段无 target（extractor 的剩余），按 chars 归桶
                    controller.record_llm(
                        len(remaining), time.perf_counter() - t0,
                    )
                refined_results.append(result)
                all_gaps.extend(result.gaps)
                segment_index += 1
                report_fn(
                    "refine", segment_index, 0,
                    f"流式精修 第 {segment_index} 小段",
                    message_key="progress.refineStream",
                    message_params={"index": str(segment_index)},
                )

        await self._save_debug(
            output_dir, "merged_raw.md", merger.get_markdown(),
        )
        await self._save_debug(
            output_dir,
            "rate_controller.json",
            json.dumps(controller.snapshot(), indent=2),
        )

        # 出云闸口同步（#67）：gap-fill / final_refine / dup-H2 重试此刻才发云端，
        # 此处一次性把 block_cloud（堵 N1）与实体 lexicon 写入任务策略——
        # final_refine 送云内容的实体保护此刻**仅靠闸口**（输出兜底 2212 在其后）。
        update_egress_policy(
            block_cloud=pii_block_cloud, lexicon=entity_lexicon,
        )
        return await self._finalize_single_doc(
            merger, pages_ref, refined_results, all_gaps,
            output_dir, llm, gpu_lock, report_fn, entity_lexicon,
            cache, llm_cfg, quality,
            pii_cfg=pii_cfg,
            block_cloud=pii_block_cloud,
        )

    async def _try_extract_and_refine(
        self,
        merger: IncrementalMerger,
        extractor: StreamSegmentExtractor,
        refiner: LLMRefiner | None,
        controller: RateController,
        segmented_offset: int,
        segment_index: int,
        refined_results: list[RefinedResult],
        all_gaps: list[Gap],
        report_fn: ReportFn,
        cache: LLMCache,
        llm_cfg: LLMConfig,
        quality: QualityReport | None = None,
        *,
        guard: PIIGuard | None = None,
        entity_lexicon: EntityLexicon | None = None,
    ) -> tuple[int, int]:
        """合并器有新文本时按 controller.target L* 尝试切段精修。

        `guard` + `entity_lexicon` 透传到段级精修：词表就绪后的分段送云端前
        脱敏人名/机构名（早窗口段词表未就绪 → 由最终输出兜底覆盖）。
        """
        profiler = current_profiler()
        md = merger.get_markdown()
        logger.info(
            "_try_extract_and_refine: md_len=%d offset=%d pages=%d",
            len(md), segmented_offset, merger.page_count,
        )
        while True:
            target = controller.target_segment_chars()
            seg = extractor.try_extract(md, segmented_offset, target)
            if seg is None:
                logger.info(
                    "try_extract 返回 None (offset=%d target=%d md_len=%d)",
                    segmented_offset, target, len(md),
                )
                break
            seg_text, new_offset = seg
            logger.info(
                "refine 开始: seg_index=%d chars=%d",
                segment_index, len(seg_text),
            )
            t0 = time.perf_counter()
            with profiler.stage(
                "llm.refine_one", index=segment_index,
                chars=len(seg_text), target=target,
            ):
                result, used_refiner = (
                    await self._refine_segment_with_cache(
                        refiner, seg_text, segment_index, 0,
                        cache, llm_cfg, quality,
                        guard=guard, entity_lexicon=entity_lexicon,
                    )
                )
            elapsed = time.perf_counter() - t0
            logger.info(
                "refine 完成: seg_index=%d chars=%d duration=%.2fs%s",
                segment_index, len(seg_text), elapsed,
                " (cached)" if not used_refiner else "",
            )
            # 缓存命中/refiner=None 不喂 RateController，避免低估 LLM 成本
            # target 传给 record_llm：按意图归桶，而不是按 segmenter 实际切出
            # 的 chars —— 防止 target=5250 切出 3000 时样本错归小桶
            if used_refiner:
                controller.record_llm(
                    len(seg_text), elapsed, target=target,
                )
            refined_results.append(result)
            all_gaps.extend(result.gaps)
            segmented_offset = new_offset
            segment_index += 1
            report_fn(
                "refine", segment_index, 0,
                f"流式精修 第 {segment_index} 小段",
                message_key="progress.refineStream",
                message_params={"index": str(segment_index)},
            )
        return segmented_offset, segment_index

    async def _finalize_single_doc(  # noqa: C901
        self,
        merger: IncrementalMerger,
        pages_ref: list[PageOCR],
        refined_results: list[RefinedResult],
        all_gaps: list[Gap],
        output_dir: Path,
        llm: LLMConfig | None,
        gpu_lock: asyncio.Lock | None,
        report_fn: ReportFn,
        entity_lexicon: EntityLexicon | None,
        cache: LLMCache,
        llm_cfg: LLMConfig,
        quality: QualityReport | None = None,
        *,
        pii_cfg: PIIConfig,
        block_cloud: bool = False,
    ) -> PipelineResult:
        """单文档终结化：reassemble → gap fill → final refine → render。

        ``block_cloud=True``（PII fail-closed：实体检测失败）时跳过 gap fill 与
        final refine 两处云端调用，仅做本地 reassemble / 去重 / polish / render，
        避免把人名 / 机构名外发到云端。

        ``pii_cfg`` 为**请求级** PII 配置（#36）：gap fill re-OCR 文本送云端前的
        脱敏、以及输出实体兜底，全程以它为准，**禁止回落 ``self._config.pii``**
        （启动默认 ``enable=False``，会让前端单次开的 PII 在这两处静默失效）。
        """
        profiler = current_profiler()

        base = MergedDocument(
            markdown="",
            images=merger.get_all_images(),
            gaps=[],
        )
        with profiler.stage("reassemble"):
            doc = self._reassemble(refined_results, base)
        await self._save_debug(
            output_dir, "reassembled.md", doc.markdown,
        )

        truncated = False
        # fail-closed：实体检测失败时跳过 gap fill 与 final refine 两处云端调用，
        # 仅保留本地组装/去重/polish/render，避免人名/机构名外发到云端。
        if not block_cloud:
            with profiler.stage(
                "llm.gap_fill_phase", num_gaps=len(all_gaps),
            ):
                doc = await self._maybe_fill_gaps(
                    doc, all_gaps, pages_ref, output_dir,
                    llm, gpu_lock, report_fn, entity_lexicon,
                    pii_cfg=pii_cfg,
                )
            with profiler.stage("llm.final_refine"):
                doc, truncated = await self._do_final_refine(
                    doc, output_dir, llm, report_fn, cache, llm_cfg,
                )

        # A-2 信号 4：final_refine 输出仍有重复 H2 → 带提示重做一次
        # 出云闸口 N1 源头双保险（#67）：fail-closed（block_cloud）时不发起 dup-H2
        # 重试——此处从源头消除「重试在 block_cloud 守卫外」的物理缺陷；闸口层
        # 另在 _call_llm 兜底拒发，两层缺一仍安全。
        if not truncated and not block_cloud:
            doc, truncated = await self._maybe_retry_final_refine_on_dup_h2(
                doc, output_dir, llm, report_fn, cache, llm_cfg,
                quality, initial_truncated=truncated,
            )

        # 程序化 HTML 表格去重：LLM 对长表（200+ 字符 HTML）跨段去重不稳定，
        # 实测 U-Boot 两轮都有 4-8 对完全相同的 23 行表残留。这里 0 LLM 成本
        # 兜底删重复，比让 LLM 重做更可靠。
        new_md, removed = dedup_html_tables(doc.markdown)
        if removed:
            doc = MergedDocument(
                markdown=new_md,
                images=doc.images,
                gaps=doc.gaps,
            )
            if quality is not None:
                await quality.add(QualityIssue(
                    stage="llm_final_refine",
                    code="llm.final_duplicate_table_removed",
                    severity="info",
                    message=(
                        f"程序化去重删除 {len(removed)} 张重复 HTML 表"
                    ),
                    metadata={
                        "count": len(removed),
                        "details": removed,
                    },
                ))

        # 程序化 H2 章节去重：signal 4 LLM retry 还是有 1-3 个残留（实测
        # 4/6 doc）。双轨判定：near_identical (ratio≥0.98 + 末尾匹配) 或
        # truncated_prefix (短的是长的截断版)。同名但内容差异大的章节不动。
        new_md, removed_h2 = dedup_h2_sections(doc.markdown)
        if removed_h2:
            doc = MergedDocument(
                markdown=new_md,
                images=doc.images,
                gaps=doc.gaps,
            )
            if quality is not None:
                await quality.add(QualityIssue(
                    stage="llm_final_refine",
                    code="llm.final_duplicate_h2_removed",
                    severity="info",
                    message=(
                        f"程序化去重删除 {len(removed_h2)} 个重复 H2 章节"
                    ),
                    metadata={
                        "count": len(removed_h2),
                        "details": removed_h2,
                    },
                ))

        # 轻量 polish：剥代码块视觉行号（U-Boot 51 行 / EMMC 27 行实测残留）
        new_md, n_lineno = strip_code_block_line_numbers(doc.markdown)
        if n_lineno:
            doc = MergedDocument(
                markdown=new_md, images=doc.images, gaps=doc.gaps,
            )
            if quality is not None:
                await quality.add(QualityIssue(
                    stage="llm_final_refine",
                    code="polish.code_line_numbers_stripped",
                    severity="info",
                    message=f"剥离代码块视觉行号 {n_lineno} 行",
                    metadata={"count": n_lineno},
                ))

        # UI 噪音兜底：cleaner 已剥过一次，但 LLM 可能漏带回少数（如 EMMC
        # `Makefile 复制代码`）。复用同正则再扫一遍。
        new_md, n_ui = strip_residual_ui_noise(doc.markdown)
        if n_ui:
            doc = MergedDocument(
                markdown=new_md, images=doc.images, gaps=doc.gaps,
            )
            if quality is not None:
                await quality.add(QualityIssue(
                    stage="llm_final_refine",
                    code="polish.residual_ui_noise_stripped",
                    severity="info",
                    message=f"兜底删除残留 UI 噪音 {n_ui} 行",
                    metadata={"count": n_ui},
                ))

        if quality is not None:
            await detect_final_refine_quality(
                quality, output_markdown=doc.markdown,
            )

        _, extra_gaps = parse_gaps(doc.markdown)
        final_gaps = list(all_gaps)
        final_gaps.extend(extra_gaps)

        # 实体脱敏输出兜底：词表就绪前已精修的"早窗口"段可能漏掉实体，这里对
        # 组装结果再脱敏一遍（lexicon=None 时跳过；结构化 PII 已在 producer 完成）。
        # 用请求级 pii_cfg（#36）：回落 self._config.pii 时标准部署恒 False → 兜底失效。
        if entity_lexicon is not None and pii_cfg.enable:
            redacted_md = PIIGuard(pii_cfg).redact_for_cloud(
                doc.markdown, entity_lexicon,
            )
            if redacted_md != doc.markdown:
                doc = MergedDocument(
                    markdown=redacted_md, images=doc.images, gaps=doc.gaps,
                )

        report_fn(
            "render", 1, 1, "渲染输出...",
            message_key="progress.render",
        )
        with profiler.stage("render.write"):
            renderer = Renderer(self._config.output)
            doc_path, final_md = await renderer.render(doc, output_dir)
        # final_md 来自 renderer 返回值（带 <!-- page: xxx --> marker 的
        # 预览版），供前端左右同步滚动锚点定位。磁盘上的 document.md 是
        # 剥除 marker 的下载版，两者互不干扰。

        # Epic E：落通用版面 sidecar .layout.json（光标↔原图 bbox 高亮真相源）。
        # 用捕获期 layout_regions（OCR 期、按页原图，早于 dedup/精修），与
        # document.md 同口径脱敏；非 VL/无区域不落盘，fail-safe 不阻断主流程。
        await self._write_doc_layout_sidecar(
            pages_ref, output_dir, pii_cfg, entity_lexicon,
        )

        warnings = self._collect_warnings(
            refined_results, final_gaps, truncated,
        )
        title = extract_first_heading(doc.markdown)

        return PipelineResult(
            output_path=doc_path,
            markdown=final_md,
            images=doc.images,
            gaps=final_gaps,
            warnings=warnings,
            redaction_records=[],
            doc_title=title,
            doc_dir="",
        )

    async def _detect_entities(
        self,
        text: str,
        pii_cfg: PIIConfig,
    ) -> EntityLexicon | None:
        """对给定文本做一次本地 NER 人名/机构名实体检测，返回 EntityLexicon。

        取代原云端 ``refiner.detect_pii_entities``——人名/机构名不出本机
        （pii-local-ner.md §3）。委托 ``PIIGuard.detect_entities``：未开 name 开关 /
        ``ner_backend="none"`` / 检测异常 → 返回 None（调用方按 ``_should_block_cloud``
        fail-closed，退化为仅正则结构化 PII）；成功（含查无实体）→ EntityLexicon。
        spaCy NER 为 CPU 阻塞 → ``to_thread`` 卸载，不阻塞事件循环（detector 内部
        ``threading.Lock`` 串行化首次加载）。
        """
        return await asyncio.to_thread(
            PIIGuard(pii_cfg).detect_entities, text,
        )

    async def _delayed_pii_detect(
        self,
        merger: IncrementalMerger,
        pii_cfg: PIIConfig,
    ) -> EntityLexicon | None:
        """积累到阈值后做一次本地 NER 实体检测获取 lexicon（委托 _detect_entities）。

        成功：返回 EntityLexicon（gap fill re-OCR + 主分段/输出兜底复用）；
        失败/未请求：返回 None（仅靠 regex PII 保护，按 _should_block_cloud 处理）。
        """
        return await self._detect_entities(merger.get_markdown(), pii_cfg)

    @staticmethod
    def _should_block_cloud(
        lexicon: EntityLexicon | None,
        pii_cfg: PIIConfig,
    ) -> bool:
        """实体检测已尝试但失败时，是否按 fail-closed 阻断云端精修。

        仅在「开 PII + 要求人名/机构名脱敏 + 检测返回 None（失败）+
        ``block_cloud_on_detect_failure`` 为真」时返回 True。

        调用点须保证 detection 已实际尝试过 —— 此处 ``lexicon=None`` 即代表
        检测失败，而非"早窗口尚未检测"。检测成功（含查无实体的空词表）返回
        非 None，故不会误判为失败。
        """
        if lexicon is not None:
            return False
        if not pii_cfg.enable:
            return False
        if not (pii_cfg.redact_person_name or pii_cfg.redact_org_name):
            return False
        if pii_cfg.ner_backend == "none":
            # 用户显式关本地 NER（知情放弃实体脱敏）→ 不算检测失败，不阻断云端
            return False
        return pii_cfg.block_cloud_on_detect_failure

    @staticmethod
    def _entity_redaction_pending(pii_cfg: PIIConfig) -> bool:
        """是否需要 LLM 实体（人名/机构名）脱敏 —— 决定早窗口是否推迟云端精修。

        True 时：实体词表（lexicon）就绪前不送任何分段/页去云端精修，避免人名/
        机构名在检测完成前外发（结构化 PII / 凭据 / 自定义词已由 producer 入队前
        正则脱敏，不受影响）。代价是词表就绪前的"先攒后发"流式延迟。
        """
        return pii_cfg.enable and (
            pii_cfg.redact_person_name or pii_cfg.redact_org_name
        )

    @staticmethod
    async def _refine_segment_with_cache(
        refiner: LLMRefiner | None,
        text: str,
        index: int,
        total: int,
        cache: LLMCache,
        llm_cfg: LLMConfig,
        quality: QualityReport | None = None,
        *,
        slide_mode: bool = False,
        guard: PIIGuard | None = None,
        entity_lexicon: EntityLexicon | None = None,
    ) -> tuple[RefinedResult, bool]:
        """段级精修带磁盘缓存。返回 `(result, used_refiner)`。

        `used_refiner=False` 表示**未拿到真实模型输出**：缓存命中、refiner=None，
        或精修调用失败/熔断 fail-fast 回退原文（#45）。调用方据此决定是否把本次
        elapsed 喂给 RateController——缓存命中的"伪时延"会低估 LLM 成本，熔断
        fail-fast 的"极短耗时"会误判 LLM 极快，二者都污染 L* 估算，一律不计入。

        异常 fallback 不写缓存（put 只在 refine 成功分支后调用），下次
        resume 仍会重试该段。truncated=True 由 LLMCache.put 内部过滤。

        `quality` 非 None 时，每次实际 refine 调用后写入 LLM 段级质量信号
        （截断 / 回退到原文 / UI 噪音残留）。缓存命中路径不写信号，
        避免历史重放污染当次任务的质量报告。

        `slide_mode=True`（PPT 按页精修）：ctx 标记 is_slide → 用 slide prompt
        （不跨页去重），缓存走独立 slide 命名空间，与文档分段缓存互不串味。

        `guard` + `entity_lexicon` 非空时，先把人名/机构名替换在送云端精修前
        （缓存键也用脱敏后文本，resume 一致）；`entity_lexicon=None`（早窗口/未开
        脱敏/检测失败）则跳过——结构化 PII 已由 producer 入队前正则脱敏。
        """
        if guard is not None and entity_lexicon is not None:
            text = guard.redact_for_cloud(text, entity_lexicon)
        if cache.enabled:
            cached = cache.get_segment(
                model=llm_cfg.model,
                api_base=llm_cfg.api_base,
                text=text,
                slide=slide_mode,
            )
            if cached is not None:
                logger.info(
                    "LLM 段级缓存命中 index=%d len=%d",
                    index + 1, len(text),
                )
                return cached, False

        if refiner is None:
            return RefinedResult(markdown=text), False
        ctx = RefineContext(
            segment_index=index + 1,
            total_segments=total,
            overlap_before="",
            overlap_after="",
            is_slide=slide_mode,
        )
        try:
            result = await refiner.refine(text, ctx)
        except Exception:
            logger.warning(
                "段 %d 精修失败，回退到原文",
                index + 1,
                exc_info=True,
            )
            if quality is not None:
                await detect_llm_segment_quality(
                    quality,
                    segment_index=index + 1,
                    truncated=False,
                    fallback_to_raw=True,
                    output_markdown=text,
                )
            # used_refiner=False：精修失败/熔断回退原文，未拿到真实模型输出，
            # 不能以"失败的极短耗时"喂 RateController 污染吞吐桶（#45）。
            return RefinedResult(markdown=text), False

        # A-2 信号 2：截断 → 递归二分重试，仍截断回退到原文。
        # 关键防护：流式 pipeline 历史 bug，截断后 LLM 输出会直接吞掉
        # 后半段（包括 page markers），导致 reassembled.md 比 merged_raw.md
        # 短一大截、文档尾页消失。这里强制做与批量版一致的截断检测兜底。
        if (
            result.truncated
            or Pipeline._heuristic_truncated(text, result.markdown, llm_cfg)
        ):
            result = await Pipeline._maybe_retry_on_truncation(
                refiner, text, ctx, result, llm_cfg, quality,
            )

        # A-2 信号 3：LLM 越权把整页替换成"本页重复已去除"注释。
        # 拍照页允许有大面积重叠，但不能用解释性注释代表整页内容；这会把
        # 重叠区中真实的新增文字和插图引用一起删掉。
        result = await Pipeline._maybe_retry_refine_on_page_drop(
            refiner, text, ctx, result, quality,
        )

        # A-2 选择性重跑：段输出仍含 UI 噪音 → 最多重试 1 次，带重试提示
        result = await Pipeline._maybe_retry_refine_on_ui_noise(
            refiner, text, ctx, result, quality,
        )

        if quality is not None:
            await detect_llm_segment_quality(
                quality,
                segment_index=index + 1,
                truncated=result.truncated,
                fallback_to_raw=False,
                output_markdown=result.markdown,
            )

        # refine 返回，尚未判 truncated — put 内部会按 truncated 过滤
        cache.put_segment(
            model=llm_cfg.model,
            api_base=llm_cfg.api_base,
            text=text,
            result=result,
            slide=slide_mode,
        )
        return result, True

    @staticmethod
    def _heuristic_truncated(
        input_text: str,
        output_md: str,
        llm_cfg: LLMConfig,
    ) -> bool:
        """与批量版一致的截断启发式：输出行数 < 输入 *(1 - ratio) 视为截断。

        小输入（≤ truncation_min_input_lines 行）误判率高，跳过启发式。
        """
        input_lines = input_text.count("\n") + 1
        if input_lines <= llm_cfg.truncation_min_input_lines:
            return False
        output_lines = output_md.count("\n") + 1
        return (
            output_lines
            < input_lines * (1 - llm_cfg.truncation_ratio_threshold)
        )

    @staticmethod
    def _split_segment_in_half(  # noqa: C901
        text: str,
    ) -> list[str] | None:
        """把段文本对半切，**避开 page marker 周围**。

        切点优先级（heading > blank line > 任意换行），但**故意避免**在
        page marker 前后 `_PAGE_MARKER_AVOID_CHARS` 字符内切。

        为什么避开 page marker：跨页拍照重叠造成的重复内容（典型 1-3 行
        在前页末尾、后页开头各出现一次）就盘踞在 page marker 前后。如果
        在 marker 处切，那段重叠区被分到两个子段，LLM 单独看任一子段都
        以为内容是单一页、不会去重。等到 reassemble 后跨段重复才会暴露
        给 final_refine —— 而 final_refine 看的是整篇压缩后的输入，
        细粒度去重能力远不如段级。

        与 stream segmenter 的策略相反（segmenter 优先 page marker）。
        两者目标不同：segmenter 控制 prompt 长度；truncation 二分要让
        LLM 看见完整的重叠区。

        返回长度 2 的列表；无法切（太短 / 无换行）返回 None。
        """
        n = len(text)
        if n < 200:
            return None
        target = n // 2

        # page marker 周围禁止切的"禁区"，按字符数定（折算约等于 ~5 行）
        avoid_chars = 240
        marker_zones: list[tuple[int, int]] = []
        for m in _PAGE_MARKER_RE.finditer(text):
            zone_start = max(0, m.start() - avoid_chars)
            zone_end = min(n, m.end() + avoid_chars)
            marker_zones.append((zone_start, zone_end))

        def in_marker_zone(pos: int) -> bool:
            for zs, ze in marker_zones:
                if zs <= pos < ze:
                    return True
            return False

        def best_match(matches: list[tuple[int, int]]) -> int | None:
            """从候选切点中挑距离 target 最近、且不在禁区里的位置。"""
            best_pos: int | None = None
            best_dist = n
            for pos, _ in matches:
                if in_marker_zone(pos):
                    continue
                d = abs(pos - target)
                if d < best_dist:
                    best_dist = d
                    best_pos = pos
            # 限定切点距 target 不要太远（不超过 n//3），否则切偏严重
            if best_pos is not None and best_dist <= n // 3:
                return best_pos
            return None

        # 优先级 1：heading 行起始（## / ### / #### 等）
        heading_re = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
        heading_candidates = [
            (m.start(), m.end()) for m in heading_re.finditer(text)
        ]
        cut = best_match(heading_candidates)
        if cut is not None:
            return [text[:cut].rstrip("\n"), text[cut:]]

        # 优先级 2：连续空行（段落边界）
        blank_re = re.compile(r"\n[\t ]*\n")
        blank_candidates = [
            (m.end(), m.end()) for m in blank_re.finditer(text)
        ]
        cut = best_match(blank_candidates)
        if cut is not None:
            return [text[:cut].rstrip("\n"), text[cut:]]

        # 优先级 3：中点附近的任意换行（仍尝试避禁区）
        # 在 [target - n//4, target + n//4] 区间扫所有换行
        window_lo = max(0, target - n // 4)
        window_hi = min(n, target + n // 4)
        nl_candidates: list[tuple[int, int]] = []
        idx = text.find("\n", window_lo)
        while 0 <= idx < window_hi:
            nl_candidates.append((idx + 1, idx + 1))
            idx = text.find("\n", idx + 1)
        cut = best_match(nl_candidates)
        if cut is not None and 0 < cut < n:
            return [text[:cut].rstrip("\n"), text[cut:]]

        # 兜底：如果所有合适位置都在 marker 禁区里（极小段 + 密集 marker
        # 的退化场景），允许切在禁区内，但仍按 heading > blank > line 找
        for cands in (heading_candidates, blank_candidates, nl_candidates):
            if not cands:
                continue
            best_pos: int | None = None
            best_dist = n
            for pos, _ in cands:
                d = abs(pos - target)
                if d < best_dist:
                    best_dist = d
                    best_pos = pos
            if best_pos is not None and 0 < best_pos < n:
                return [text[:best_pos].rstrip("\n"), text[best_pos:]]

        return None

    @staticmethod
    async def _maybe_retry_on_truncation(
        refiner: LLMRefiner,
        text: str,
        ctx: RefineContext,
        first_result: RefinedResult,  # noqa: ARG004 — API 对称占位
        llm_cfg: LLMConfig,
        quality: QualityReport | None,
        *,
        depth: int = 0,
        max_depth: int = 3,
        min_chunk_chars: int = 800,
    ) -> RefinedResult:
        """A-2 信号 2：段输出截断 → 递归二分重试。

        策略：
        - 把当前段对半切（page marker / 空行 / 换行优先），分别 refine
        - 任一子段仍截断 → 对它继续二分（depth+1）
        - depth ≥ max_depth 或 段长 < 2*min_chunk_chars → 回退原文
        - 二分子段调用异常 → 整段回退原文（保守）
        - 拼回时按原顺序 join，gaps 合并

        first_result 表示首轮截断输出。长段优先二分；短段无法二分时，
        带 retry_hint 对同一输入重试一次，避免 1KB 左右的小段因偶发短输出
        只能直接回退原文。

        重要：拼回的 markdown 长度可以 > LLM 单次 max_tokens，因为它由多段
        独立 refine 拼接，不再受单次响应 token 上限约束 —— 这正是修复
        U-Boot 尾页消失的核心：原本「LLM 截断 1 次就丢半段」的失败模式，
        被「截多少段就 refine 多少次」绕过。
        """
        if depth >= max_depth:
            logger.warning(
                "段 %d 截断递归到达上限 depth=%d，回退到原文",
                ctx.segment_index, depth,
            )
            if quality is not None:
                await quality.add(QualityIssue(
                    stage="llm_segment",
                    code="llm.seg_truncation_unrecoverable",
                    severity="warn",
                    message=(
                        f"段 {ctx.segment_index} 截断递归到达上限"
                        f" depth={depth}，回退原文"
                    ),
                    segment_index=ctx.segment_index,
                    metadata={
                        "depth": depth,
                        "input_chars": len(text),
                    },
                ))
            return RefinedResult(markdown=text, gaps=[], truncated=True)

        halves = Pipeline._split_segment_in_half(text)
        if halves is None or any(
            len(h) < min_chunk_chars for h in halves
        ):
            return await Pipeline._handle_unsplittable_truncation(
                refiner, text, ctx, first_result, llm_cfg, quality, depth,
            )

        sub_ctx_template = RefineContext(
            segment_index=ctx.segment_index,
            total_segments=ctx.total_segments,
            overlap_before="",
            overlap_after="",
            retry_hint=ctx.retry_hint,
            is_slide=ctx.is_slide,
        )
        sub_results: list[RefinedResult] = []
        for sub_text in halves:
            try:
                sub_result = await refiner.refine(
                    sub_text, sub_ctx_template,
                )
            except Exception:
                logger.warning(
                    "段 %d 二分子段精修失败 (depth=%d)，整段回退原文",
                    ctx.segment_index, depth, exc_info=True,
                )
                return RefinedResult(
                    markdown=text, gaps=[], truncated=True,
                )
            if (
                sub_result.truncated
                or Pipeline._heuristic_truncated(
                    sub_text, sub_result.markdown, llm_cfg,
                )
            ):
                sub_result = await Pipeline._maybe_retry_on_truncation(
                    refiner, sub_text, sub_ctx_template, sub_result,
                    llm_cfg, quality,
                    depth=depth + 1,
                    max_depth=max_depth,
                    min_chunk_chars=min_chunk_chars,
                )
            sub_results.append(sub_result)

        merged_md = "\n".join(r.markdown for r in sub_results)
        merged_gaps = [g for r in sub_results for g in r.gaps]
        any_truncated = any(r.truncated for r in sub_results)
        if quality is not None:
            await quality.add(QualityIssue(
                stage="llm_segment",
                code="llm.seg_truncation_split",
                severity="info",
                message=(
                    f"段 {ctx.segment_index} 截断 → 二分重试"
                    f" depth={depth + 1}"
                    + ("（仍部分截断）" if any_truncated else "（已恢复）")
                ),
                segment_index=ctx.segment_index,
                metadata={
                    "depth": depth + 1,
                    "still_truncated": any_truncated,
                    "halves_chars": [len(h) for h in halves],
                },
            ))
        # any_truncated=False 表示二分后所有子段都成功 → 拼回的结果是完整的
        return RefinedResult(
            markdown=merged_md,
            gaps=merged_gaps,
            truncated=any_truncated,
        )

    @staticmethod
    async def _handle_unsplittable_truncation(
        refiner: LLMRefiner,
        text: str,
        ctx: RefineContext,
        first_result: RefinedResult,
        llm_cfg: LLMConfig,
        quality: QualityReport | None,
        depth: int,
    ) -> RefinedResult:
        """处理短到无法继续二分的截断段。"""
        if not ctx.retry_hint:
            retry_result = await Pipeline._retry_short_truncated_segment(
                refiner, text, ctx, first_result, llm_cfg, quality,
            )
            if retry_result is not None:
                return retry_result

        logger.warning(
            "段 %d 截断但无法继续二分（input=%d 字符）→ 回退原文",
            ctx.segment_index, len(text),
        )
        if quality is not None:
            await quality.add(QualityIssue(
                stage="llm_segment",
                code="llm.seg_truncation_unrecoverable",
                severity="warn",
                message=(
                    f"段 {ctx.segment_index} 截断且无法继续二分"
                    f"（input={len(text)} 字符），回退原文"
                ),
                segment_index=ctx.segment_index,
                metadata={
                    "input_chars": len(text),
                    "depth": depth,
                },
            ))
        return RefinedResult(markdown=text, gaps=[], truncated=True)

    @staticmethod
    async def _retry_short_truncated_segment(
        refiner: LLMRefiner,
        text: str,
        ctx: RefineContext,
        first_result: RefinedResult,
        llm_cfg: LLMConfig,
        quality: QualityReport | None,
    ) -> RefinedResult | None:
        """短段无法二分时，同段带提示重试一次；成功则返回结果。"""
        retry_ctx = RefineContext(
            segment_index=ctx.segment_index,
            total_segments=ctx.total_segments,
            overlap_before=ctx.overlap_before,
            overlap_after=ctx.overlap_after,
            retry_hint=(
                "上一轮精修输出疑似被截断，且当前段较短无法继续二分。"
                "请完整保留输入全部内容，只修复 Markdown 格式，"
                "不要省略、总结或提前结束。"
            ),
            is_slide=ctx.is_slide,
        )
        try:
            retry_result = await refiner.refine(text, retry_ctx)
        except Exception:
            logger.warning(
                "段 %d 短段截断重试失败，回退原文",
                ctx.segment_index, exc_info=True,
            )
            return None

        retry_truncated = (
            retry_result.truncated
            or Pipeline._heuristic_truncated(
                text, retry_result.markdown, llm_cfg,
            )
        )
        if quality is not None:
            await quality.add(QualityIssue(
                stage="llm_segment",
                code="llm.seg_truncation_short_retry",
                severity="info",
                message=(
                    f"段 {ctx.segment_index} 短段截断同段重试"
                    + ("（仍截断）" if retry_truncated else "（已恢复）")
                ),
                segment_index=ctx.segment_index,
                metadata={
                    "input_chars": len(text),
                    "retry_truncated": retry_truncated,
                    "first_output_chars": len(first_result.markdown),
                },
            ))
        if retry_truncated:
            return None
        return retry_result

    @staticmethod
    async def _maybe_retry_refine_on_page_drop(
        refiner: LLMRefiner,
        text: str,
        ctx: RefineContext,
        first_result: RefinedResult,
        quality: QualityReport | None,
    ) -> RefinedResult:
        """LLM 把整页替换为重复删除注释时，重试一次；失败则回退原文。"""
        if _PAGE_DROP_COMMENT_RE.search(first_result.markdown) is None:
            return first_result
        if ctx.retry_hint:
            return RefinedResult(markdown=text, gaps=[], truncated=True)

        retry_ctx = RefineContext(
            segment_index=ctx.segment_index,
            total_segments=ctx.total_segments,
            overlap_before=ctx.overlap_before,
            overlap_after=ctx.overlap_after,
            retry_hint=(
                "上一轮输出把某一整页替换成“本页内容与上一页完全重复，"
                "已去除”这类解释性注释，这是错误的。拍照页可能高度重叠，"
                "但必须保留每个 page marker 后的有效正文和所有图片引用；"
                "只允许删除逐字重复的句子，不允许整页删除或添加解释性注释。"
            ),
            is_slide=ctx.is_slide,
        )
        try:
            retry_result = await refiner.refine(text, retry_ctx)
        except Exception:
            logger.warning(
                "段 %d 整页误删重试失败，回退原文",
                ctx.segment_index, exc_info=True,
            )
            return RefinedResult(markdown=text, gaps=[], truncated=True)

        retry_still_bad = (
            _PAGE_DROP_COMMENT_RE.search(retry_result.markdown) is not None
        )
        if quality is not None:
            await quality.add(QualityIssue(
                stage="llm_segment",
                code="llm.seg_page_drop_retry",
                severity="warn" if retry_still_bad else "info",
                message=(
                    f"段 {ctx.segment_index} 整页误删重试"
                    + ("（仍误删，回退原文）" if retry_still_bad else "（已恢复）")
                ),
                segment_index=ctx.segment_index,
                metadata={"retry_still_bad": retry_still_bad},
            ))
        if retry_still_bad:
            return RefinedResult(markdown=text, gaps=[], truncated=True)
        return retry_result

    @staticmethod
    async def _maybe_retry_refine_on_ui_noise(
        refiner: LLMRefiner,
        text: str,
        ctx: RefineContext,
        first_result: RefinedResult,
        quality: QualityReport | None,
    ) -> RefinedResult:
        """A-2 信号 1：段输出残留 UI 噪音 → 带提示重试一次。

        比较两次结果，保留噪音少的那一份（同数时保留第一次）。
        重试失败（异常 / 噪音更多）则回退第一次结果，不影响主流程。
        """
        first_hits = UI_NOISE_RESIDUAL_RE.findall(first_result.markdown)
        if not first_hits:
            return first_result
        if ctx.retry_hint:
            # 已是重试结果，不再递归
            return first_result

        retry_ctx = RefineContext(
            segment_index=ctx.segment_index,
            total_segments=ctx.total_segments,
            overlap_before=ctx.overlap_before,
            overlap_after=ctx.overlap_after,
            retry_hint=(
                f"上一轮输出仍含 {len(first_hits)} 处网页 UI 噪音"
                f"（如 `{first_hits[0]}`）。请逐行删除所有 "
                "`{语言} 复制代码` / 独立 `复制代码` /"
                "以 `▶▼☐` 开头的视觉 UI 行；若留在代码块内，"
                "剥离后保持代码块闭合。"
            ),
            is_slide=ctx.is_slide,
        )
        try:
            retry_result = await refiner.refine(text, retry_ctx)
        except Exception:
            logger.warning(
                "段 %d UI 噪音重试失败，保留首轮结果",
                ctx.segment_index, exc_info=True,
            )
            return first_result

        retry_hits = UI_NOISE_RESIDUAL_RE.findall(retry_result.markdown)
        if quality is not None:
            await quality.add(QualityIssue(
                stage="llm_segment",
                code="llm.seg_ui_noise_retry",
                severity="info",
                message=(
                    f"段 {ctx.segment_index} UI 噪音重试："
                    f"{len(first_hits)} → {len(retry_hits)} 处"
                ),
                segment_index=ctx.segment_index,
                metadata={
                    "first_count": len(first_hits),
                    "retry_count": len(retry_hits),
                    "kept_retry": len(retry_hits) < len(first_hits),
                },
            ))
        if len(retry_hits) < len(first_hits):
            return retry_result
        return first_result

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
        *,
        pii_cfg: PIIConfig,
    ) -> MergedDocument:
        """条件检查后调用 _fill_gaps，不满足条件直接返回原文档。

        ``pii_cfg`` 为请求级 PII 配置（#36），透传给 _fill_gaps → _fill_one_gap，
        让 gap re-OCR 文本在送云端前按请求级配置脱敏（不回落 self._config.pii）。
        """
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
            pii_cfg=pii_cfg,
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
        *,
        pii_cfg: PIIConfig,
    ) -> tuple[MergedDocument, int]:
        """遍历 gap 列表，re-OCR + LLM 提取缺失内容并插入文档。

        ``pii_cfg`` 请求级 PII 配置（#36），透传给 _fill_one_gap 脱敏 re-OCR 文本。
        """
        reocr_cache: dict[str, str] = {}
        markdown = doc.markdown
        filled_count = 0
        profiler = current_profiler()
        # PIIGuard 建一次复用（#66）：原 _fill_one_gap 每 gap 重建（含 NER 模型
        # 初始化），这里建一次下传；关 PII 时为 None。
        pii_guard = PIIGuard(pii_cfg) if pii_cfg.enable else None

        for gi, gap in enumerate(gaps):
            report_fn(
                "gap_fill", gi + 1, len(gaps),
                f"补充缺口 {gi + 1}/{len(gaps)}...",
                message_key="progress.gapFill",
                message_params={
                    "current": str(gi + 1),
                    "total": str(len(gaps)),
                },
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
                        pii_guard=pii_guard,
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
        *,
        pii_guard: PIIGuard | None,
    ) -> str:
        """对单个 gap 做 re-OCR + LLM 提取。

        返回填充内容（空字符串表示无法填充）。
        若启用 PII 脱敏，re-OCR 文本在送入 LLM 前先脱敏。

        ``pii_guard`` 由 ``_fill_gaps`` 用**请求级** pii_cfg 建一次下传（#66 复用 +
        #36 请求级）：gap 补全的 re-OCR 产生**全新文本**，绕过 producer 的逐页 regex
        脱敏，必须在送 fill_gap 云端前补脱；None 表示未开 PII。
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

        # PII 脱敏 re-OCR 文本（轻量模式，不调用 LLM）；guard 由 _fill_gaps 用请求级
        # pii_cfg 建一次下传（#66 复用 + #36 请求级），None 表示未开 PII。
        if pii_guard is not None:
            current_text = pii_guard.redact_for_cloud(
                current_text, entity_lexicon,
            )
            if next_page_text is not None:
                next_page_text = pii_guard.redact_for_cloud(
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
        *,
        for_refine: bool = True,
    ) -> LLMRefiner | None:
        """获取 refiner 实例：llm 非空时按请求快照新建，否则复用默认实例。

        ``for_refine`` 区分两类用途，避免"精修开关"误伤"LLM 客户端能力"：
        - ``True``（默认，所有精修调用点）：受统一精修开关约束，有效
          ``LLMConfig.enable_refine`` 为 False 时直接返回 None —— 文档（分段）
          / 代码 / PPT（按页）三模式既有的 ``if refiner is None: 跳过`` 回退
          路径统一生效，改这一处即可关停所有模式的精修。
        - ``False``（PII 实体检测 / 代码头脱敏等非精修用途）：**不看**
          enable_refine，只要配置了 model 就返回客户端。否则用户"关精修但开
          脱敏"时，基于 LLM 的人名 / 机构名检测会被精修开关连带关掉，
          导致正则兜不住的隐私（人名 / 公司名）泄漏到云端 / 输出。

        llm 非空但 `llm.model` 为空串时返回 None —— 下游调用点已有
        `if refiner is None: 跳过` 的回退路径。否则 `_create_refiner` 会
        把 model="" 塞进去，litellm 调用时对每次都抛 BadRequestError 并
        在 stderr 打"Provider List: https://docs.litellm.ai/docs/providers"。
        """
        effective = llm if llm is not None else self._config.llm
        if for_refine and not effective.enable_refine:
            return None
        if llm is None:
            return self._refiner
        if not llm.model:
            return None
        return self._create_refiner(llm)

    def _will_stream_refine(
        self,
        llm: LLMConfig | None,
        code: CodeRestoreConfig | None,
        ppt: PowerPointRestoreConfig | None,
    ) -> bool:
        """多子目录是否会走云端流式文档精修（决定是否需 warmup 冷启动校准 L*）。

        代码 / PPT 模式有各自的精修路径、不经 RateController 段长冷启动；关精修
        （enable_refine=False）或未配 model 则根本不精修。任一成立 → 无需冷启动，
        直接并发所有子目录免白等最长 60s（#44）。
        """
        effective_code = code if code is not None else self._config.code
        if effective_code is not None and effective_code.enable:
            return False
        effective_ppt = ppt if ppt is not None else self._config.ppt
        if effective_ppt is not None and effective_ppt.enable:
            return False
        return self._get_refiner(llm, for_refine=True) is not None

    async def _maybe_retry_final_refine_on_dup_h2(
        self,
        doc: MergedDocument,
        output_dir: Path,
        llm: LLMConfig | None,
        report_fn: ReportFn,
        cache: LLMCache,
        llm_cfg: LLMConfig,
        quality: QualityReport | None,
        *,
        initial_truncated: bool,
    ) -> tuple[MergedDocument, bool]:
        """A-2 信号 4：final_refine 输出仍有重复 H2 → 带提示重做一次。

        重试流程：
        - 检测 `doc.markdown` 中出现 ≥ 2 次的 H2 标题
        - 无重复 → 直接返回（no-op）
        - 有重复 → 构造 retry_hint，调 `_do_final_refine(retry_hint=...)`
        - 比较两轮的重复 H2 数量，保留更少的那份；结果回写 quality report
        - 重试截断 / 重试反而更糟 → 保留首轮

        返回 `(文档, 是否截断)`。首轮已截断时不触发重试，直接原路返回。
        """
        if initial_truncated:
            return doc, initial_truncated

        first_dups = find_duplicate_h2_titles(doc.markdown)
        if not first_dups:
            return doc, initial_truncated

        hint = (
            f"输出仍有 {len(first_dups)} 个重复的 `##` 二级标题："
            f"{', '.join(first_dups)}. 请按 system 规则 2 清理——"
            "若两次出现的内容几乎一样（跨页拍照重叠导致），保留靠前的；"
            "若前一份明显被 OCR 截断（乱码/半句），改保留靠后的完整版。"
            "重复的标题必须合并为一次，正文也按完整版保留。"
        )
        logger.info(
            "final_refine 重复 H2 重试 (%d 个): %s",
            len(first_dups), first_dups,
        )
        retry_doc, retry_trunc = await self._do_final_refine(
            MergedDocument(
                markdown=doc.markdown,
                images=doc.images,
                gaps=[],
            ),
            output_dir, llm, report_fn, cache, llm_cfg,
            retry_hint=hint,
        )
        if retry_trunc:
            logger.warning(
                "final_refine 重复 H2 重试截断，保留首轮",
            )
            if quality is not None:
                await quality.add(QualityIssue(
                    stage="llm_final_refine",
                    code="llm.final_duplicate_h2_retry",
                    severity="warn",
                    message="重复 H2 重试截断，已保留首轮结果",
                    metadata={
                        "first_dup_count": len(first_dups),
                        "retry_truncated": True,
                        "kept_retry": False,
                    },
                ))
            return doc, initial_truncated

        retry_dups = find_duplicate_h2_titles(retry_doc.markdown)
        if quality is not None:
            await quality.add(QualityIssue(
                stage="llm_final_refine",
                code="llm.final_duplicate_h2_retry",
                severity="info",
                message=(
                    f"重复 H2 重试：{len(first_dups)} → {len(retry_dups)} 个"
                ),
                metadata={
                    "first_dup_count": len(first_dups),
                    "retry_dup_count": len(retry_dups),
                    "kept_retry": len(retry_dups) < len(first_dups),
                },
            ))
        if len(retry_dups) < len(first_dups):
            # 重试更干净 → 采用；保留原 images，合并 gaps
            return MergedDocument(
                markdown=retry_doc.markdown,
                images=doc.images,
                gaps=doc.gaps + retry_doc.gaps,
            ), retry_trunc
        return doc, initial_truncated

    async def _do_final_refine(
        self,
        doc: MergedDocument,
        output_dir: Path,
        llm: LLMConfig | None,
        report_fn: ReportFn,
        cache: LLMCache,
        llm_cfg: LLMConfig,
        *,
        retry_hint: str = "",
    ) -> tuple[MergedDocument, bool]:
        """整篇文档级精修（去跨段重复 + 页眉水印）。

        retry_hint 非空时视为 A-2 重试：强制 single-chunk（整篇看）+
        绕过缓存（旧结果已经被检测为有问题），把 hint 透传给 prompt。
        """
        refiner = self._get_refiner(llm)
        if (
            not self._config.llm.enable_final_refine
            or refiner is None
        ):
            return doc, False

        return await self._final_refine(
            refiner, doc, output_dir, report_fn, cache, llm_cfg,
            retry_hint=retry_hint,
        )

    async def _final_refine(  # noqa: C901
        self,
        refiner: LLMRefiner,
        doc: MergedDocument,
        output_dir: Path,
        report_fn: ReportFn,
        cache: LLMCache,
        llm_cfg: LLMConfig,
        *,
        retry_hint: str = "",
    ) -> tuple[MergedDocument, bool]:
        """整篇文档级精修，失败时回退到原文。返回 (文档, 是否截断)。

        带磁盘缓存：命中直接返回，miss 才真正调 LLM 并落盘。
        大文档按 <!-- page: --> 边界切成多块并行调用，降低墙钟。

        retry_hint 非空（A-2 重跑）时：
        - 跳过缓存命中判断（旧结果已被检测为有问题）
        - 强制 single-chunk（重复 H2 这类问题是跨段全局问题，必须整篇看）
        - 不写缓存（重试结果不应覆盖后续真实运行）
        - 不覆盖 debug/final_refined.md（保留首轮输出供对比）
        """
        if not hasattr(refiner, "final_refine"):
            return doc, False

        is_retry = bool(retry_hint)

        # 先查缓存 — 整文档级精修通常是最昂贵的一步
        # cache key 以完整 markdown 为准，分块是纯实现细节、对缓存透明
        # 重试路径绕过缓存：旧结果已被质量检测判定为有问题
        if cache.enabled and not is_retry:
            cached = cache.get_final(
                model=llm_cfg.model,
                api_base=llm_cfg.api_base,
                markdown=doc.markdown,
            )
            if cached is not None:
                logger.info(
                    "LLM 整文档精修缓存命中 input_len=%d",
                    len(doc.markdown),
                )
                return MergedDocument(
                    markdown=cached.markdown,
                    images=doc.images,
                    gaps=doc.gaps + cached.gaps,
                ), False

        # 决定是否分块：文档够大 + 配置允许（重试路径强制 single chunk）
        if is_retry:
            chunks = [doc.markdown]
        else:
            n_chunks = max(1, int(llm_cfg.final_refine_chunks))
            if (
                n_chunks <= 1
                or len(doc.markdown) < llm_cfg.final_refine_min_chars
            ):
                chunks = [doc.markdown]
            else:
                chunks = _split_by_page_markers(doc.markdown, n_chunks)
                # 切分失败（页边界不足以支撑 N 块）则回退单次
                if len(chunks) <= 1:
                    chunks = [doc.markdown]

        report_fn(
            "final_refine", 0, len(chunks),
            (
                "整篇文档级精修（重试）..."
                if is_retry
                else (
                    f"整篇文档级精修...（{len(chunks)} 块并行）"
                    if len(chunks) > 1 else "整篇文档级精修..."
                )
            ),
            message_key=(
                "progress.finalRefine"
                if is_retry or len(chunks) == 1
                else "progress.finalRefineChunks"
            ),
            message_params={"chunks": str(len(chunks))}
            if len(chunks) > 1 and not is_retry else {},
        )

        try:
            total = len(chunks)
            # 并行调用；任意一块失败或截断由后处理统一回退到原文
            results: list[RefinedResult | BaseException] = (
                await asyncio.gather(
                    *(
                        refiner.final_refine(
                            c, chunk_index=i + 1, total_chunks=total,
                            retry_hint=retry_hint,
                        )
                        for i, c in enumerate(chunks)
                    ),
                    return_exceptions=True,
                )
            )
        except Exception:
            logger.warning(
                "整篇文档级精修调度失败，回退到原文", exc_info=True,
            )
            return doc, False

        # 汇总：任一块异常/截断 → 保守回退原文
        merged_parts: list[str] = []
        merged_gaps: list[Gap] = []
        any_truncated = False
        for i, r in enumerate(results):
            if isinstance(r, asyncio.CancelledError):
                # 取消须透传（项目约定 CancelledError 一路传播），不能当普通
                # "块失败"吞掉回退原文，否则破坏结构化取消 / 资源回收。
                raise r
            if isinstance(r, BaseException):
                logger.warning(
                    "整篇精修第 %d/%d 块失败，回退到原文: %s",
                    i + 1, len(chunks), r,
                )
                return doc, False
            if r.truncated:
                logger.warning(
                    "整篇精修第 %d/%d 块疑似截断，回退到原文",
                    i + 1, len(chunks),
                )
                return doc, True
            merged_parts.append(r.markdown)
            merged_gaps.extend(r.gaps)

        merged_markdown = _stitch_final_chunks(merged_parts)
        final_result = RefinedResult(
            markdown=merged_markdown,
            gaps=merged_gaps,
            truncated=False,
        )
        if is_retry:
            # 重试路径：另存文件便于对比，不覆盖首轮；不写缓存
            await self._save_debug(
                output_dir, "final_refined.retry.md", merged_markdown,
            )
        else:
            await self._save_debug(
                output_dir, "final_refined.md", merged_markdown,
            )
            # 真正成功才写缓存（重试路径跳过，避免污染主缓存）
            cache.put_final(
                model=llm_cfg.model,
                api_base=llm_cfg.api_base,
                markdown=doc.markdown,
                result=final_result,
            )
        return MergedDocument(
            markdown=merged_markdown,
            images=doc.images,
            gaps=doc.gaps + merged_gaps,
        ), any_truncated

    @staticmethod
    def _engine_degraded_warnings(reason: str) -> list[PipelineWarning]:
        """引擎降级原因码（#96）转任务级结构化 warning（空原因返回空列表）。

        ``reason`` 由生产者在本任务 ensure() 时刻同步捕获并透传（**不**读共享 live
        标志），避免并发混模式任务互改引擎全局 degraded_reason 造成误报/漏报。返回
        i18n code+params（前端按 code 本地化渲染）；/ocr/status 另透 degraded_reason
        码供前端徽章本地化，两条通道各司其职。
        """
        if not reason:
            return []
        if reason in ("vl_no_server_python", "vl_server_python_missing"):
            return [PipelineWarning("vl_fell_back_to_local")]
        return [PipelineWarning("engine_degraded", {"reason": reason})]

    @staticmethod
    def _collect_warnings(
        refined_results: list[RefinedResult],
        all_gaps: list[Gap],
        final_truncated: bool,
    ) -> list[PipelineWarning]:
        """聚合所有警告信息（结构化 code+params，前端本地化渲染）。"""
        warnings: list[PipelineWarning] = []
        for i, r in enumerate(refined_results):
            if r.truncated:
                warnings.append(
                    PipelineWarning("segment_truncated", {"index": i + 1}),
                )
        if final_truncated:
            warnings.append(PipelineWarning("document_truncated"))
        for g in all_gaps:
            if not g.filled:
                warnings.append(
                    PipelineWarning(
                        "gap_unfilled", {"after_image": g.after_image},
                    ),
                )
        return warnings
