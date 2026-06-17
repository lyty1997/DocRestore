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

"""benchmark：汇总 LLM 盲评分数 + 耗时，产出对比报告。

读 judge/merged.json（逐页 paddle/mineru 六维分数 + 胜者）与两份 timing.json，
按 全部/formula_pdf/photos 三个切面算每维度均分、胜场、公式专项，落 report.md +
summary.json，并打印结论导向的摘要：回答「MinerU 公式增量值不值得引入」。

用法：
    conda run -n docrestore python scripts/bench_quality/report.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ROOT = PROJECT_ROOT / "output" / "bench" / "quality"
DIMS = ("formula", "text", "table", "reading_order", "structure", "overall")
DIM_ZH = {
    "formula": "公式", "text": "正文", "table": "表格",
    "reading_order": "阅读序", "structure": "结构", "overall": "综合",
}
SCOPES = ("全部", "formula_pdf", "photos")


def _mean(values: list[float]) -> float | None:
    """非空均值，空列表返回 None。"""
    return round(sum(values) / len(values), 2) if values else None


def _dim_means(
    rows: list[dict[str, Any]], dim: str,
) -> tuple[float | None, float | None, int]:
    """某维度下 paddle/mineru 均分与有效页数（两侧均非 null 才计入）。"""
    paddle_vals: list[float] = []
    mineru_vals: list[float] = []
    for r in rows:
        cell = r.get(dim) or {}
        p, m = cell.get("paddle"), cell.get("mineru")
        if isinstance(p, (int, float)) and isinstance(m, (int, float)):
            paddle_vals.append(float(p))
            mineru_vals.append(float(m))
    return _mean(paddle_vals), _mean(mineru_vals), len(paddle_vals)


def _wins(rows: list[dict[str, Any]]) -> dict[str, int]:
    """统计胜场。"""
    tally = {"paddle": 0, "mineru": 0, "tie": 0}
    for r in rows:
        w = r.get("winner", "tie")
        tally[w] = tally.get(w, 0) + 1
    return tally


def _scope_rows(
    merged: list[dict[str, Any]], scope: str,
) -> list[dict[str, Any]]:
    """按切面过滤页。"""
    if scope == "全部":
        return merged
    return [r for r in merged if r.get("set") == scope]


def _build_report(merged: list[dict[str, Any]], timing: dict[str, Any]) -> str:
    """组装 markdown 报告。"""
    lines: list[str] = ["# OCR 质量 benchmark：PaddleOCR-VL vs MinerU(pipeline)", ""]
    lines.append(f"评审页数：{len(merged)}（LLM-as-judge 盲评，源图为真相）")
    lines.append("")

    for scope in SCOPES:
        rows = _scope_rows(merged, scope)
        if not rows:
            continue
        lines.append(f"## 切面：{scope}（{len(rows)} 页）")
        lines.append("")
        lines.append("| 维度 | PaddleOCR-VL | MinerU | 有效页 | 差值(M-P) |")
        lines.append("|---|---|---|---|---|")
        for dim in DIMS:
            p, m, n = _dim_means(rows, dim)
            if n == 0:
                lines.append(f"| {DIM_ZH[dim]} | — | — | 0 | — |")
                continue
            diff = round((m or 0) - (p or 0), 2)
            lines.append(
                f"| {DIM_ZH[dim]} | {p} | {m} | {n} | {diff:+} |",
            )
        tally = _wins(rows)
        lines.append("")
        lines.append(
            f"胜场：PaddleOCR {tally['paddle']} / MinerU {tally['mineru']} "
            f"/ 平 {tally['tie']}",
        )
        lines.append("")

    lines.append("## 耗时")
    lines.append("")
    for eng, key in (("PaddleOCR-VL", "paddle"), ("MinerU", "mineru")):
        t = timing.get(key, {})
        init = t.get("init_elapsed_s", "—")
        lines.append(f"- {eng}: init={init}s（逐页耗时见 {key}/timing.json）")
    lines.append("")

    lines.append("## 关键差异样本（公式相关优先）")
    lines.append("")
    shown = 0
    for r in merged:
        diffs = r.get("key_diffs") or []
        if diffs and shown < 12:
            joined = "；".join(str(d) for d in diffs)
            lines.append(f"- [{r.get('set')}/{r['stem']}] {joined}")
            shown += 1
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """读评审结果 + 耗时 → 写 report.md / summary.json。"""
    merged_path = ROOT / "judge" / "merged.json"
    if not merged_path.is_file():
        msg = f"未找到 {merged_path}，请先跑 judge.py"
        raise FileNotFoundError(msg)
    merged: list[dict[str, Any]] = json.loads(
        merged_path.read_text(encoding="utf-8"),
    )

    timing: dict[str, Any] = {}
    for key in ("paddle", "mineru"):
        tp = ROOT / key / "timing.json"
        if tp.is_file():
            timing[key] = json.loads(tp.read_text(encoding="utf-8"))

    report = _build_report(merged, timing)
    (ROOT / "report.md").write_text(report, encoding="utf-8")

    summary: dict[str, Any] = {"pages": len(merged), "by_scope": {}}
    for scope in SCOPES:
        rows = _scope_rows(merged, scope)
        if not rows:
            continue
        scope_dims = {}
        for dim in DIMS:
            p, m, n = _dim_means(rows, dim)
            scope_dims[dim] = {"paddle": p, "mineru": m, "n": n}
        summary["by_scope"][scope] = {
            "dims": scope_dims, "wins": _wins(rows),
        }
    (ROOT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    print(report)
    print(f"\n✔ 报告写入 {ROOT / 'report.md'} 与 summary.json")


if __name__ == "__main__":
    main()
