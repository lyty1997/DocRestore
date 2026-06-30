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

"""PDF 逐页渲染：把单个 PDF 渲染成零填充命名的 RGB PNG，落指定目录（Epic A）。

设计见 ``docs/zh/pdf-mode.md``。要点：

- **幂等**：目标目录写 ``.render_done.json`` sentinel，PDF 内容哈希命中则整本跳过，
  支撑 resume / retry 复用同 image_dir 时不重渲染、OCR 缓存键不漂移。
- **命名**：``{name_prefix}page_{N:0Wd}.png``（N 从 1，零填充），保证 scan_images
  字典序 = 页序，且多 PDF 间 basename 全局唯一（name_prefix 带净化后的 pdf stem）。
- **鲁棒**：单页渲染异常跳过记 warning 不中断整本；加密 / 损坏 PDF 由 PdfDocument
  构造抛异常上浮，交调用方转 PipelineResult.error。
- **防爆**：max_pages 截断超长 PDF；max_long_side 降采样超大幅面页。

使用 pypdfium2（PDFium 绑定，Apache/BSD，无 GPL 传染）。渲染是阻塞 IO，async 调用方
须用 ``asyncio.to_thread`` 包裹。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from PIL import Image

if TYPE_CHECKING:
    from docrestore.pipeline.config import PdfRenderConfig

logger = logging.getLogger(__name__)

#: 渲染完成标记文件名（落目标目录）：既做渲染幂等短路，也标识"该目录来自 PDF 渲染"
_SENTINEL_NAME = ".render_done.json"


def _pdfium_version() -> str:
    """pypdfium2 包版本（懒加载，写入 sentinel 供检测渲染器升级导致的产物漂移）。"""
    import pypdfium2 as pdfium

    return str(getattr(pdfium, "PYPDFIUM_INFO", "unknown"))


def is_pdf_rendered_dir(image_dir: Path) -> bool:
    """该目录的图片是否由 PDF 渲染而来（据 sentinel 判定）。

    供 pipeline 判断：PDF 渲染页无屏摄侧栏 UI，应跳过 content_crop 正文区裁剪。
    """
    return (image_dir / _SENTINEL_NAME).is_file()


def safe_pdf_stem(name: str) -> str:
    """把 PDF 文件名（或 stem）净化成安全的目录名 / 文件前缀。

    对齐 ``upload._secure_filename`` 语义（保留字母数字与 ``- _ .``，其余转 ``_``），
    额外折叠连续下划线、去首尾 ``_`` 与 ``.``，避免：路径穿越、renderer 图片重写正则
    ``([^/)]+)_OCR/images/`` 被特殊字符（如 ``)``）破坏、首点造成隐藏目录。
    撞名（净化后相同）由调用方加后缀去重。``isalnum`` 保留 CJK，中文文件名不丢。

    参数 ``name`` 既可是含 ``.pdf`` 的文件名，也可是已去后缀的 stem。
    """
    stem = Path(name).stem if name.lower().endswith(".pdf") else name
    cleaned = stem.replace("\x00", "")
    kept = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in cleaned)
    collapsed = re.sub(r"_+", "_", kept).strip("_.")
    return collapsed or "pdf"


def _file_sha256(path: Path) -> str:
    """流式计算文件 SHA-256（大 PDF 不全量驻留内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sentinel(out_dir: Path, expected_digest: str) -> int | None:
    """sentinel 命中（PDF 哈希一致）则返回已渲染页数，否则 None（需重渲染）。"""
    path = out_dir / _SENTINEL_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("pdf_sha256") != expected_digest:
        return None
    rendered = data.get("rendered")
    if not isinstance(rendered, int):
        return None
    # 仅当上次渲染完整才算幂等命中：expected_pages 记录上次预期页数
    # （min(源页数, 上限)）。若 rendered < expected_pages 说明上次有坏页被跳过，
    # 不复用此缓存——下次重跑重试缺页，避免缺页被 sentinel 永久固化。旧 sentinel
    # 无该字段时视为完成（向后兼容）。
    expected = data.get("expected_pages")
    if isinstance(expected, int) and rendered < expected:
        return None
    return rendered


def _write_sentinel(
    out_dir: Path,
    digest: str,
    *,
    source_pages: int,
    rendered: int,
    expected_pages: int,
    width: int,
    cfg: PdfRenderConfig,
    name_prefix: str,
) -> None:
    """落渲染完成 sentinel，记录幂等校验与排障所需信息。

    ``expected_pages`` 为本次预期渲染页数（min(源页数, 上限)）；幂等命中判定靠
    它与 ``rendered`` 是否相等，缺页时不被复用。
    """
    payload = {
        "pdf_sha256": digest,
        "source_pages": source_pages,
        "rendered": rendered,
        "expected_pages": expected_pages,
        "dpi": cfg.dpi,
        "max_long_side": cfg.max_long_side,
        "width": width,
        "name_prefix": name_prefix,
        "naming": f"{name_prefix}page_{{N:0{width}d}}.png",
        "pdfium": _pdfium_version(),
    }
    (out_dir / _SENTINEL_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _to_rgb_bounded(image: Image.Image, max_long_side: int) -> Image.Image:
    """转 RGB + 超长边按比例降采样（防超大幅面页撑爆 OCR 引擎图像阈值）。"""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    long_side = max(rgb.size)
    if long_side > max_long_side:
        ratio = max_long_side / long_side
        new_size = (round(rgb.size[0] * ratio), round(rgb.size[1] * ratio))
        rgb = rgb.resize(new_size, Image.Resampling.LANCZOS)
    return rgb


class PdfRenderResult(NamedTuple):
    """单 PDF 渲染结果（#96）：``expected - rendered`` 即坏页跳过的缺页数。"""

    rendered: int
    expected: int


def render_pdf_to_dir(
    pdf_path: Path,
    out_dir: Path,
    *,
    cfg: PdfRenderConfig,
    name_prefix: str = "",
) -> PdfRenderResult:
    """把单个 PDF 逐页渲染成 RGB PNG 落 ``out_dir``，返回成功/预期页数（#96）。

    幂等（sentinel 命中跳过）+ 坏页鲁棒（单页失败跳过）+ 超长截断 + 超大降采样。
    加密 / 损坏 PDF 的 ``PdfDocument`` 构造异常**不在此捕获**，由调用方转
    ``PipelineResult.error``（单 PDF 失败不影响同任务其他 PDF）。
    ``rendered < expected`` = 部分缺页，调用方据此挂软降级 warning。
    """
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    digest = _file_sha256(pdf_path)

    cached = _read_sentinel(out_dir, digest)
    if cached is not None:
        logger.info(
            "PDF 渲染幂等命中，跳过: %s (%d 页)", pdf_path.name, cached,
        )
        # 命中即上次渲染完整（_read_sentinel 仅在 rendered==expected 时命中），
        # 故 expected == rendered，无缺页。
        return PdfRenderResult(cached, cached)

    doc = pdfium.PdfDocument(str(pdf_path))
    rendered = 0
    try:
        source_pages = len(doc)
        limit = min(source_pages, cfg.max_pages)
        if source_pages > cfg.max_pages:
            logger.warning(
                "PDF 页数 %d 超上限 %d，截断渲染前 %d 页: %s",
                source_pages, cfg.max_pages, limit, pdf_path.name,
            )
        width = max(cfg.zero_pad, len(str(limit)))
        scale = cfg.dpi / 72.0
        for i in range(limit):
            try:
                raw = doc[i].render(scale=scale).to_pil()
                image = _to_rgb_bounded(raw, cfg.max_long_side)
                dst = out_dir / f"{name_prefix}page_{i + 1:0{width}d}.png"
                image.save(dst)
                rendered += 1
            except (pdfium.PdfiumError, OSError, ValueError):
                # 只吞渲染/图像 IO 异常（坏页、不可保存）：坏页跳过而非炸整篇。
                # AttributeError/TypeError 等编程 bug 不在此列，照常向上抛由调用方
                # 记为整篇失败，避免被当成"坏页"长期掩盖。
                logger.warning(
                    "PDF 第 %d 页渲染失败，跳过: %s",
                    i + 1, pdf_path.name, exc_info=True,
                )
        if rendered < limit:
            logger.warning(
                "PDF 渲染不完整：%d/%d 页成功，其余坏页已跳过；本次不计为完成态，"
                "重跑将重试缺页: %s",
                rendered, limit, pdf_path.name,
            )
    finally:
        doc.close()

    _write_sentinel(
        out_dir, digest, source_pages=source_pages, rendered=rendered,
        expected_pages=limit, width=width, cfg=cfg, name_prefix=name_prefix,
    )
    return PdfRenderResult(rendered, limit)
