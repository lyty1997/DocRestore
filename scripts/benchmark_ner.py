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

"""本地 NER benchmark — spaCy detector vs 自建金标（PER/ORG P/R/F1）+ 真实样本测速。

S3 切换本地 NER 前的留证脚本（详见 docs/zh/backend/pii-local-ner.md §7）。三件套：
  ① 自建金标 tests/privacy/fixtures/ner_eval.jsonl（中英文短句，**非用户数据集**，
     覆盖中文名/英文名/公司/机构/干扰项）→ 算 PER/ORG 严格 P/R/F1 + 宽松召回。
  ② 真实样本测速：test_images 下 OCR 输出（result.mmd）文本，仅测吞吐不做内容断言。
  ③ 可选 --cloud：有 GLM_API_KEY 时跑云端 detect_pii_entities，算「本地∩云端 / 云端」
     一致率（银标参考），失败则跳过、不阻断主流程。

用法：
    conda activate docrestore   # 需先装 spaCy + 模型：bash scripts/setup_ner.sh
    python scripts/benchmark_ner.py
    python scripts/benchmark_ner.py --models zh_core_web_md en_core_web_md \\
        --gold tests/privacy/fixtures/ner_eval.jsonl --samples-dir test_images \\
        --out docs/zh/backend/ner-benchmark.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

if TYPE_CHECKING:
    from docrestore.privacy.ner import SpacyEntityDetector

DEFAULT_MODELS: tuple[str, ...] = ("zh_core_web_md", "en_core_web_md")
DEFAULT_GOLD = PROJECT_ROOT / "tests" / "privacy" / "fixtures" / "ner_eval.jsonl"


@dataclass(frozen=True)
class GoldItem:
    """金标单条：原文 + 人名/机构名真值。"""

    text: str
    persons: tuple[str, ...]
    orgs: tuple[str, ...]


@dataclass
class Tally:
    """单类别（PER 或 ORG）的混淆计数 + 宽松召回累计。"""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    lenient_tp: int = 0
    gold_total: int = 0

    def precision(self) -> float:
        """严格精确率（无预测且无金标视为 1.0）。"""
        denom = self.tp + self.fp
        return self.tp / denom if denom else 1.0

    def recall(self) -> float:
        """严格召回率（无金标视为 1.0）。"""
        denom = self.tp + self.fn
        return self.tp / denom if denom else 1.0

    def f1(self) -> float:
        """严格 F1。"""
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def lenient_recall(self) -> float:
        """宽松召回：金标实体被任一预测「包含或被包含」即算命中。"""
        return self.lenient_tp / self.gold_total if self.gold_total else 1.0


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """把 JSON 数组收窄为字符串元组（非字符串元素丢弃）。"""
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str))


def load_gold(path: Path) -> list[GoldItem]:
    """读金标 jsonl（每行 {text, persons, orgs}）。"""
    items: list[GoldItem] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        obj: object = json.loads(line)
        if not isinstance(obj, dict):
            continue
        text = obj.get("text")
        if not isinstance(text, str):
            continue
        items.append(
            GoldItem(
                text=text,
                persons=_as_str_tuple(obj.get("persons")),
                orgs=_as_str_tuple(obj.get("orgs")),
            )
        )
    return items


def _norm(s: str) -> str:
    """归一：去空白 + 转小写（中文不受影响），用于匹配。"""
    return s.strip().lower()


def _score(gold: tuple[str, ...], pred: list[str], tally: Tally) -> None:
    """把一条样本的金标 vs 预测累加进 tally（严格集合 + 宽松包含）。"""
    gold_norm = {_norm(g) for g in gold}
    pred_norm = {_norm(p) for p in pred}
    tally.tp += len(gold_norm & pred_norm)
    tally.fp += len(pred_norm - gold_norm)
    tally.fn += len(gold_norm - pred_norm)
    tally.gold_total += len(gold_norm)
    for g in gold_norm:
        if any(g == p or g in p or p in g for p in pred_norm):
            tally.lenient_tp += 1


def evaluate(
    detector: SpacyEntityDetector, gold: list[GoldItem],
) -> tuple[Tally, Tally]:
    """跑本地 detector 过全部金标，返回 (PER tally, ORG tally)。"""
    per, org = Tally(), Tally()
    for item in gold:
        persons, orgs = detector.detect(item.text)
        _score(item.persons, persons, per)
        _score(item.orgs, orgs, org)
    return per, org


@dataclass(frozen=True)
class SpeedStat:
    """测速结果。"""

    n: int
    total_chars: int
    mean_ms: float
    p95_ms: float
    chars_per_s: float


def gather_sample_texts(
    samples_dir: Path, gold: list[GoldItem], result_name: str,
) -> tuple[list[str], str]:
    """收集测速文本：优先 OCR result 文件，缺则回退金标语料（仅测速不做内容断言）。"""
    if samples_dir.is_dir():
        texts = [
            p.read_text(encoding="utf-8", errors="ignore")
            for p in sorted(samples_dir.rglob(result_name))
        ]
        texts = [t for t in texts if t.strip()]
        if texts:
            return texts, f"OCR 真实样本（{samples_dir}/**/{result_name}）"
    return [item.text for item in gold], "金标语料（无 OCR 样本回退）"


def speed_test(detector: SpacyEntityDetector, texts: list[str]) -> SpeedStat:
    """逐条计时 detector.detect，返回均值/尾延迟/吞吐。"""
    durations: list[float] = []
    total_chars = 0
    for text in texts:
        start = time.perf_counter()
        detector.detect(text)
        durations.append(time.perf_counter() - start)
        total_chars += len(text)
    total_s = sum(durations) or 1e-9
    ordered = sorted(durations)
    idx = min(len(ordered) - 1, int(len(ordered) * 0.95)) if ordered else 0
    return SpeedStat(
        n=len(texts),
        total_chars=total_chars,
        mean_ms=(statistics.fmean(durations) * 1000) if durations else 0.0,
        p95_ms=(ordered[idx] * 1000) if ordered else 0.0,
        chars_per_s=total_chars / total_s,
    )


def cloud_agreement(
    gold: list[GoldItem], model: str, api_base: str,
) -> tuple[Tally, Tally] | None:
    """可选银标：跑云端 detect_pii_entities，对同一金标打分便于同表对比。

    无 GLM_API_KEY / 无 model / 任何异常 → 返回 None（跳过，不阻断主流程）。
    """
    api_key = os.environ.get("GLM_API_KEY", "").strip()
    if not api_key or not model:
        print("[cloud] 跳过：缺 GLM_API_KEY 或 model", file=sys.stderr)
        return None
    try:
        from docrestore.llm.cloud import CloudLLMRefiner
        from docrestore.pipeline.config import LLMConfig

        cfg = LLMConfig(
            provider="cloud", model=model, api_base=api_base, api_key=api_key,
        )

        async def _run() -> tuple[Tally, Tally]:
            refiner = CloudLLMRefiner(cfg, semaphore=asyncio.Semaphore(4))
            per, org = Tally(), Tally()
            for item in gold:
                persons, orgs = await refiner.detect_pii_entities(item.text)
                _score(item.persons, persons, per)
                _score(item.orgs, orgs, org)
            return per, org

        return asyncio.run(_run())
    except Exception as exc:
        print(f"[cloud] 跳过云端对照：{exc}", file=sys.stderr)
        return None


def _fmt_tally(name: str, t: Tally) -> str:
    """一行 markdown 表格行。"""
    return (
        f"| {name} | {t.gold_total} | {t.tp} | {t.fp} | {t.fn} | "
        f"{t.precision():.2f} | {t.recall():.2f} | {t.f1():.2f} | "
        f"{t.lenient_recall():.2f} |"
    )


def build_report(
    models: tuple[str, ...],
    gold_path: Path,
    n_gold: int,
    local: tuple[Tally, Tally],
    speed: SpeedStat,
    speed_source: str,
    cloud: tuple[Tally, Tally] | None,
) -> str:
    """拼装 markdown 报告（表格 + 测速 + 可选云端对照）。"""
    per, org = local
    header = "| 类别 | 金标 | TP | FP | FN | 精确 | 召回 | F1 | 宽松召回 |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    lines: list[str] = [
        "## 本地 NER benchmark 结果",
        "",
        f"- 模型：`{', '.join(models)}`",
        f"- 金标：`{gold_path}`（{n_gold} 条自建中英文短句，非用户数据集）",
        "- 严格匹配＝归一后集合相等；宽松召回＝金标实体被任一预测包含/被包含。",
        "",
        "### 本地 spaCy 抽取质量（对自建金标）",
        "",
        header,
        sep,
        _fmt_tally("人名 PER", per),
        _fmt_tally("机构 ORG", org),
        "",
        "### 速度（主进程 CPU）",
        "",
        f"- 样本来源：{speed_source}",
        (
            f"- {speed.n} 段 / 共 {speed.total_chars} 字符："
            f"单段均值 {speed.mean_ms:.1f} ms，p95 {speed.p95_ms:.1f} ms，"
            f"吞吐 {speed.chars_per_s:,.0f} 字符/秒。"
        ),
        "",
    ]
    if cloud is not None:
        c_per, c_org = cloud
        lines += [
            "### 云端 LLM 对照（银标参考，同一金标）",
            "",
            header,
            sep,
            _fmt_tally("人名 PER", c_per),
            _fmt_tally("机构 ORG", c_org),
            "",
        ]
    else:
        lines += ["> 云端对照未跑（无 GLM_API_KEY 或未加 --cloud）。", ""]
    return "\n".join(lines)


def main() -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="本地 NER benchmark")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument(
        "--samples-dir", type=Path, default=PROJECT_ROOT / "test_images",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--cloud-model", default=os.environ.get("LLM_MODEL", ""))
    parser.add_argument(
        "--cloud-api-base", default=os.environ.get("LLM_API_BASE", ""),
    )
    args = parser.parse_args()

    models: tuple[str, ...] = tuple(args.models)
    gold_path: Path = args.gold
    gold = load_gold(gold_path)
    if not gold:
        print(f"金标为空或不存在：{gold_path}", file=sys.stderr)
        return 2

    from docrestore.ocr.base import OCR_RESULT_FILENAME
    from docrestore.privacy.ner import detector_available, get_detector

    if not detector_available(models):
        print(
            "本地 NER 不可用：请先装 spaCy + 模型（bash scripts/setup_ner.sh）。",
            file=sys.stderr,
        )
        return 3

    detector = get_detector(models)
    local = evaluate(detector, gold)
    texts, source = gather_sample_texts(
        args.samples_dir, gold, OCR_RESULT_FILENAME,
    )
    speed = speed_test(detector, texts)
    cloud = (
        cloud_agreement(gold, args.cloud_model, args.cloud_api_base)
        if args.cloud
        else None
    )

    report = build_report(models, gold_path, len(gold), local, speed, source, cloud)
    print(report)
    out: Path | None = args.out
    if out is not None:
        out.write_text(report + "\n", encoding="utf-8")
        print(f"\n[written] {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
