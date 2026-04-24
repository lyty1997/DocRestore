# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""AGE-8 Phase 1.3：视觉切分预览 CLI

批量跑 ``ide_ui_strip`` + ``code_columns``，为每张图导出：
  - ``original.png``       原图缩略
  - ``stripped.png``       剪裁后纯代码区
  - ``col_{N}.png``        多栏切出的子图
  - ``summary.html``       网格一页展示全部，便于人工验收

用法：
    python scripts/preview_ide_columns.py \\
        --input test_images/Chromium_VDA_code/ \\
        --output output/age8-preview/ \\
        [--strategy hybrid|geometric|ocr_anchored] \\
        [--sample 20]     # 抽样 N 张避免全量 273

验收：人工浏览 ``summary.html``，目测剪裁/分栏正确率。
"""

from __future__ import annotations

import argparse
import html
import logging
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 允许 `python scripts/preview_ide_columns.py` 直接跑（不 pip install）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from docrestore.processing.code_columns import (  # noqa: E402  — see sys.path fix above
    ColumnsConfig,
    split_columns,
)
from docrestore.processing.ide_ui_strip import (  # noqa: E402
    IDEUIConfig,
    strip_ide_ui,
)

logger = logging.getLogger("age8.preview")


@dataclass
class PreviewEntry:
    """单张图的预览产出路径 + 元数据，供 summary HTML 渲染"""

    page_stem: str
    original_path: Path
    stripped_path: Path
    column_paths: list[Path]
    ide_flags: list[str]
    columns_flags: list[str]
    detect_strategy: str
    detected_split: bool


def _make_thumb(image: Image.Image, max_edge: int = 800) -> Image.Image:
    """等比缩略（避免 HTML 加载几百张 4K 原图卡浏览器）"""
    w, h = image.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        return image.resize((int(w * scale), int(h * scale)))
    return image.copy()


def _annotate(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    color: tuple[int, int, int] = (255, 80, 80),
) -> Image.Image:
    """在缩略上框出 bbox（原图坐标系），供肉眼核验"""
    w, h = image.size
    # image 已经是缩略；要把原图 bbox 映射到缩略坐标系需要比例
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(bbox, outline=color, width=3)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14,
        )
    except OSError:
        font = ImageFont.load_default()
    draw.text((bbox[0] + 4, bbox[1] + 4), "code", fill=color, font=font)
    # silence unused params
    _ = (w, h)
    return canvas


def _process_one(
    image_path: Path,
    output_root: Path,
    ide_cfg: IDEUIConfig,
    col_cfg: ColumnsConfig,
) -> PreviewEntry:
    """处理一张图，返回 PreviewEntry"""
    page_stem = image_path.stem
    page_dir = output_root / page_stem
    page_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(image_path).convert("RGB")

    # 原图缩略 + 框出 code_region
    original_thumb_path = page_dir / "original.png"
    # ide_ui_strip
    strip_res = strip_ide_ui(image, ide_cfg)
    scale = min(800 / image.size[0], 800 / image.size[1], 1.0)
    thumb = _make_thumb(image)
    sx, sy, ex, ey = strip_res.code_region_bbox
    annotated = _annotate(
        thumb, (int(sx * scale), int(sy * scale), int(ex * scale), int(ey * scale)),
    )
    annotated.save(original_thumb_path)

    # stripped 图
    stripped_thumb_path = page_dir / "stripped.png"
    _make_thumb(strip_res.cropped).save(stripped_thumb_path)

    # 多栏切割
    col_res = split_columns(strip_res.cropped, col_cfg)
    column_paths: list[Path] = []
    for col in col_res.columns:
        col_path = page_dir / f"col_{col.column_index}.png"
        _make_thumb(col.image).save(col_path)
        column_paths.append(col_path)

    return PreviewEntry(
        page_stem=page_stem,
        original_path=original_thumb_path,
        stripped_path=stripped_thumb_path,
        column_paths=column_paths,
        ide_flags=list(strip_res.flags),
        columns_flags=list(col_res.flags),
        detect_strategy=strip_res.detect_strategy,
        detected_split=col_res.detected_split,
    )


def _render_summary_html(
    entries: list[PreviewEntry],
    output_root: Path,
    input_dir: Path,
    strategy: str,
) -> Path:
    """渲染所有条目到一个 HTML 网格页面"""
    summary_path = output_root / "summary.html"

    rows = []
    for entry in entries:
        # 相对 summary.html 的路径，便于浏览器打开
        def rel(p: Path) -> str:
            return str(p.relative_to(output_root)).replace("\\", "/")

        col_imgs = "".join(
            f'<div class="col-img"><div class="col-label">col {i}</div>'
            f'<img src="{html.escape(rel(p))}" alt="col"/></div>'
            for i, p in enumerate(entry.column_paths)
        )
        flags_html = " ".join(
            f'<span class="flag">{html.escape(f)}</span>'
            for f in (*entry.ide_flags, *entry.columns_flags)
        ) or '<span class="flag-ok">ok</span>'

        rows.append(
            f"""
            <div class="entry">
              <h3>{html.escape(entry.page_stem)}</h3>
              <div class="meta">
                strategy={html.escape(entry.detect_strategy)}
                split={str(entry.detected_split).lower()}
                cols={len(entry.column_paths)}
                {flags_html}
              </div>
              <div class="grid">
                <div class="col-img"><div class="col-label">original (red=code region)</div>
                  <img src="{html.escape(rel(entry.original_path))}" alt="original"/></div>
                <div class="col-img"><div class="col-label">stripped</div>
                  <img src="{html.escape(rel(entry.stripped_path))}" alt="stripped"/></div>
                {col_imgs}
              </div>
            </div>
            """
        )

    html_content = f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>AGE-8 Phase 1 preview — {html.escape(input_dir.name)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#1e1e1e; color:#ddd; margin: 20px; }}
  h1 {{ color: #4ec9b0; }}
  .header-meta {{ color: #888; font-size: 14px; margin-bottom: 20px; }}
  .entry {{ border: 1px solid #333; padding: 12px; margin-bottom: 20px; border-radius: 6px; background:#252526; }}
  .entry h3 {{ margin: 0 0 6px 0; color: #ce9178; }}
  .meta {{ color:#888; font-size: 12px; margin-bottom: 8px; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .col-img {{ flex: 0 0 auto; }}
  .col-label {{ font-size: 11px; color:#666; margin-bottom: 2px; }}
  .col-img img {{ max-width: 380px; max-height: 260px; border: 1px solid #444; display: block; }}
  .flag {{ background:#b74747; padding: 2px 6px; border-radius: 3px; font-size: 11px; margin-right: 4px; }}
  .flag-ok {{ background:#0d7a0d; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
</style>
</head>
<body>
<h1>AGE-8 Phase 1 视觉切分预览</h1>
<div class="header-meta">
  input={html.escape(str(input_dir))} · strategy={html.escape(strategy)} ·
  entries={len(entries)}
</div>
{"".join(rows)}
</body>
</html>
"""
    summary_path.write_text(html_content, encoding="utf-8")
    return summary_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AGE-8 Phase 1 视觉切分预览（IDE-UI 剪裁 + 多栏切割）",
    )
    parser.add_argument(
        "--input", type=Path, required=True,
        help="IDE 截图目录",
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="预览输出目录（会清空再写）",
    )
    parser.add_argument(
        "--strategy", choices=["geometric", "hybrid", "ocr_anchored"],
        default="hybrid",
    )
    parser.add_argument(
        "--sample", type=int, default=0,
        help="抽样 N 张；0 = 全量",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="随机抽样种子（保证可重复）",
    )
    return parser.parse_args()


def _list_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"input dir not found: {input_dir}")
    return sorted(
        p for p in input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()

    images = _list_images(args.input)
    if args.sample and args.sample < len(images):
        rng = random.Random(args.seed)  # noqa: S311 — 预览脚本，非安全场景
        images = rng.sample(images, args.sample)
    logger.info("processing %d images from %s", len(images), args.input)

    args.output.mkdir(parents=True, exist_ok=True)

    ide_cfg = IDEUIConfig(enable=True, detect_strategy=args.strategy)
    col_cfg = ColumnsConfig(enable=True)

    entries: list[PreviewEntry] = []
    for idx, img_path in enumerate(images, 1):
        try:
            entry = _process_one(img_path, args.output, ide_cfg, col_cfg)
            entries.append(entry)
            logger.info(
                "[%d/%d] %s strategy=%s cols=%d flags=%s",
                idx, len(images), img_path.name, entry.detect_strategy,
                len(entry.column_paths), [*entry.ide_flags, *entry.columns_flags],
            )
        except Exception as exc:  # noqa: BLE001 — 预览脚本，单张失败不中断
            logger.exception("failed on %s: %s", img_path, exc)

    summary = _render_summary_html(entries, args.output, args.input, args.strategy)
    logger.info("summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
