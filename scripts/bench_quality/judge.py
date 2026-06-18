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

"""benchmark：多模态 LLM-as-judge 盲评 PaddleOCR-VL vs MinerU。

无 ground truth，以**源页图**为唯一真相：每页把源图 + 两份 OCR→markdown
（盲化为系统 A / 系统 B，按 stem 哈希决定谁是 A，消除位置偏置）喂给视觉 LLM，
按 公式 / 正文 / 表格 / 阅读序 / 结构 / 综合 六维各给 1-5 分 + 胜者 + 差异点。
落 judge/{set}/{stem}.json（含 A/B↔引擎 反查映射）。

用法：
    set -a && . ./.env && set +a
    conda run -n docrestore python scripts/bench_quality/judge.py \\
        --model "$GPT_MODEL" --api-base "$GPT_API_BASE" \\
        --api-key-env GPT_API_KEY
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import litellm
from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ROOT = PROJECT_ROOT / "output" / "bench" / "quality"
INPUTS = ROOT / "inputs"
SETS = ("formula_pdf", "photos")
JUDGE_IMG_MAX_SIDE = 1600  # 评审图限长边，控制 payload 与成本

_SYS_PROMPT = (
    "你是严谨的文档 OCR 质量评审专家。你会看到一张文档源页图，以及两套 OCR "
    "系统把它转成的 markdown（系统A、系统B）。以源页图为唯一真相，逐维度判断"
    "哪套更忠实还原源页。只输出 JSON，不要任何额外文字。"
)

_RUBRIC = """对系统A与系统B各打分（1-5 整数，5=完全忠实还原，1=严重错误/缺失）。
维度：
- formula：数学公式还原（LaTeX 正确性、上下标、分式、符号）。源页无公式则填 null。
- text：正文文字准确度（错字/漏字/多字/乱码）。
- table：表格结构与单元格内容。源页无表格则填 null。
- reading_order：阅读顺序与版面还原（多栏顺序、段落连续性、图表位置）。
- structure：markdown 结构（标题层级、列表、代码块、强调）。
- overall：综合可用性。
另给：
- winner：'A' / 'B' / 'tie'（综合更优者）。
- key_diffs：1-3 条最关键差异（中文，指出谁在哪一维度明显更好/更差，尤其公式）。

严格按此 JSON 结构输出（分数为整数或 null）：
{"formula":{"A":int|null,"B":int|null},
 "text":{"A":int,"B":int},
 "table":{"A":int|null,"B":int|null},
 "reading_order":{"A":int,"B":int},
 "structure":{"A":int,"B":int},
 "overall":{"A":int,"B":int},
 "winner":"A|B|tie",
 "key_diffs":[str]}"""


def _b64_image(png_path: Path) -> str:
    """读图、EXIF 转正、限长边后编码为 base64 data 适配 image_url。"""
    with Image.open(png_path) as src:
        im = ImageOps.exif_transpose(src) or src
        if im.mode != "RGB":
            im = im.convert("RGB")
        long_side = max(im.size)
        if long_side > JUDGE_IMG_MAX_SIDE:
            ratio = JUDGE_IMG_MAX_SIDE / long_side
            im = im.resize(
                (round(im.size[0] * ratio), round(im.size[1] * ratio)),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _assign_ab(stem: str) -> bool:
    """按 stem 哈希决定 paddle 是否当系统A（稳定、消除位置偏置）。"""
    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 2 == 0


def _parse_json(raw: str) -> dict[str, Any]:
    """从模型输出剥 code fence 后 json.loads。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _judge_page(
    *, model: str, api_base: str, api_key: str,
    png: Path, paddle_md: str, mineru_md: str, stem: str,
) -> dict[str, Any]:
    """对单页发起一次盲评，返回带 A/B↔引擎 映射的结果。"""
    paddle_is_a = _assign_ab(stem)
    md_a = paddle_md if paddle_is_a else mineru_md
    md_b = mineru_md if paddle_is_a else paddle_md

    user_text = (
        f"{_RUBRIC}\n\n=== 系统A 的 markdown ===\n{md_a}\n\n"
        f"=== 系统B 的 markdown ===\n{md_b}"
    )
    messages = [
        {"role": "system", "content": _SYS_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {
                "url": f"data:image/jpeg;base64,{_b64_image(png)}",
            }},
        ]},
    ]

    last_err = ""
    for attempt in range(2):
        try:
            resp = litellm.completion(
                model=model, api_base=api_base, api_key=api_key,
                messages=messages, temperature=0.0, timeout=120,
            )
            raw = resp.choices[0].message.content or ""
            verdict = _parse_json(raw)
            break
        except Exception as exc:  # noqa: BLE001 — 单页失败不中断整批
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(2)
    else:
        return {"stem": stem, "error": last_err,
                "ab_map": {"A": "paddle" if paddle_is_a else "mineru",
                           "B": "mineru" if paddle_is_a else "paddle"}}

    return {
        "stem": stem,
        "ab_map": {"A": "paddle" if paddle_is_a else "mineru",
                   "B": "mineru" if paddle_is_a else "paddle"},
        "verdict": verdict,
    }


def _to_engine_scores(
    rec: dict[str, Any],
) -> dict[str, Any] | None:
    """把盲评 A/B 分数还原成 paddle/mineru 分数。"""
    if "verdict" not in rec:
        return None
    verdict = rec["verdict"]
    ab = rec["ab_map"]
    dims = ["formula", "text", "table", "reading_order", "structure", "overall"]
    out: dict[str, Any] = {"stem": rec["stem"]}
    for d in dims:
        cell = verdict.get(d) or {}
        out[d] = {ab["A"]: cell.get("A"), ab["B"]: cell.get("B")}
    win = verdict.get("winner", "tie")
    out["winner"] = ab.get(win, "tie") if win in ("A", "B") else "tie"
    out["key_diffs"] = verdict.get("key_diffs", [])
    return out


def main() -> None:
    """对所有页跑盲评，落每页 JSON 与合并结果。"""
    parser = argparse.ArgumentParser(description="LLM-as-judge OCR 盲评")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--api-key-env", required=True,
                        help="持有 API key 的环境变量名")
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        print(f"环境变量 {args.api_key_env} 为空", file=sys.stderr)
        sys.exit(1)

    paddle_root = ROOT / "paddle"
    mineru_root = ROOT / "mineru"
    judge_root = ROOT / "judge"

    merged: list[dict[str, Any]] = []
    for set_name in SETS:
        out_dir = judge_root / set_name
        out_dir.mkdir(parents=True, exist_ok=True)
        for png in sorted((INPUTS / set_name).glob("*.png")):
            stem = png.stem
            p_md = paddle_root / set_name / f"{stem}.md"
            m_md = mineru_root / set_name / f"{stem}.md"
            if not p_md.is_file() or not m_md.is_file():
                print(f"  跳过 {set_name}/{stem}（缺 md）", flush=True)
                continue

            t0 = time.time()
            rec = _judge_page(
                model=args.model, api_base=args.api_base, api_key=api_key,
                png=png, paddle_md=p_md.read_text(encoding="utf-8"),
                mineru_md=m_md.read_text(encoding="utf-8"), stem=stem,
            )
            (out_dir / f"{stem}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            scores = _to_engine_scores(rec)
            if scores is not None:
                scores["set"] = set_name
                merged.append(scores)
                print(
                    f"  [{set_name}] {stem}: winner={scores['winner']} "
                    f"({time.time() - t0:.1f}s)", flush=True,
                )
            else:
                print(f"  [{set_name}] {stem}: ERROR {rec.get('error')}",
                      flush=True)

    (judge_root / "merged.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\n✔ 评审完成 {len(merged)} 页，输出 {judge_root}")


if __name__ == "__main__":
    main()
