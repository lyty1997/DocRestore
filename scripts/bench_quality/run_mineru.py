#!/usr/bin/env python3
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

"""benchmark：用 MinerU pipeline 后端对逐页 PNG 产出 markdown（直调 SDK）。

直接调用 mineru.cli.common.do_parse（进程内、同步），绕开 `mineru` CLI 自起的
临时 HTTP fast_api 服务——后者在本机会卡死。read_fn 把每张 PNG 包成单页 PDF
字节，do_parse 一次吃整批（同进程 ModelSingleton 缓存，两个 set 只加载一次模型）。
产物 {raw}/{stem}/ocr/{stem}.md 收敛到 mineru/{set}/{stem}.md。

用法（在 mineru 环境跑，需先下载 pipeline 权重）：
    conda run -n mineru python scripts/bench_quality/run_mineru.py --gpu-id 0
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INPUTS = PROJECT_ROOT / "output" / "bench" / "quality" / "inputs"
OUT = PROJECT_ROOT / "output" / "bench" / "quality" / "mineru"
RAW = PROJECT_ROOT / "output" / "bench" / "quality" / "mineru_raw"
SETS: dict[str, str] = {"formula_pdf": "en", "photos": "ch"}


def _collect(raw_set_dir: Path, stem: str) -> str:
    """从 {raw}/{stem}/{method}/{stem}.md 读回 MinerU 单页 markdown。"""
    hits = sorted(raw_set_dir.glob(f"{stem}/*/{stem}.md"))
    return hits[0].read_text(encoding="utf-8") if hits else ""


def run_set(set_name: str, lang: str) -> list[dict[str, object]]:
    """对一个输入集直调 do_parse，收集逐页 md，返回耗时统计。"""
    from mineru.cli.common import do_parse, read_fn  # type: ignore[import-not-found]

    in_dir = INPUTS / set_name
    raw_out = RAW / set_name
    out_dir = OUT / set_name
    raw_out.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    pngs = sorted(in_dir.glob("*.png"))
    stems = [p.stem for p in pngs]
    bytes_list = [read_fn(p) for p in pngs]
    langs = [lang] * len(pngs)

    print(f"[{set_name}] do_parse {len(pngs)} 页 (lang={lang})...", flush=True)
    t0 = time.time()
    do_parse(
        output_dir=str(raw_out),
        pdf_file_names=stems,
        pdf_bytes_list=list(bytes_list),
        p_lang_list=langs,
        backend="pipeline",
        parse_method="ocr",
        formula_enable=True,
        table_enable=True,
        f_draw_layout_bbox=False,
        f_draw_span_bbox=False,
        f_dump_middle_json=True,
        f_dump_model_output=False,
        f_dump_orig_pdf=False,
        f_dump_content_list=True,
    )
    elapsed = time.time() - t0
    print(f"  [{set_name}] 完成: {elapsed:.1f}s", flush=True)

    stats: list[dict[str, object]] = []
    for stem in stems:
        md_text = _collect(raw_out, stem)
        (out_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")
        stats.append({"stem": stem, "md_chars": len(md_text)})
        print(f"    {stem}: {len(md_text)} chars", flush=True)
    stats.append({"set_elapsed_s": round(elapsed, 2)})
    return stats


def main() -> None:
    """对所有输入集跑 MinerU pipeline（同进程，模型只加载一次）。"""
    parser = argparse.ArgumentParser(description="MinerU pipeline benchmark")
    parser.add_argument("--gpu-id", default="0")
    args = parser.parse_args()
    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu_id
    os.environ.setdefault("MINERU_FORMULA_CH_SUPPORT", "True")
    os.environ.setdefault("MINERU_DEVICE_MODE", "cuda")

    OUT.mkdir(parents=True, exist_ok=True)
    timings: dict[str, object] = {"engine": "mineru-pipeline"}
    for set_name, lang in SETS.items():
        timings[set_name] = run_set(set_name, lang)

    (OUT / "timing.json").write_text(
        json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\n✔ MinerU pipeline 完成，输出 {OUT}")


if __name__ == "__main__":
    main()
