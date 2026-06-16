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

"""privacy/ner.py 单测：注入 fake nlp，不下载真实 spaCy 模型。

覆盖：实体标签映射（PERSON/ORG）、跨模型并集去重、惰性加载只一次、不可用
降级抛错、廉价探测分桶、进程级单例缓存。断言从构造输入派生（不写死数据集
标识符），fake nlp 结构化满足 ner 内部 _SpacyNLP/_SpacyDoc/_SpacySpan 协议。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from docrestore.privacy import ner


@dataclass(frozen=True)
class _FakeSpan:
    """假实体 span（结构满足 ner._SpacySpan）。"""

    text: str
    label_: str


@dataclass(frozen=True)
class _FakeDoc:
    """假 Doc（结构满足 ner._SpacyDoc）。"""

    ents: tuple[_FakeSpan, ...]


class _FakeNLP:
    """假 spaCy pipeline：忽略输入文本，返回预置实体（结构满足 ner._SpacyNLP）。"""

    def __init__(self, ents: Sequence[_FakeSpan]) -> None:
        """记录预置实体。"""
        self._ents = tuple(ents)

    def __call__(self, text: str) -> _FakeDoc:
        """返回含预置实体的假 Doc。"""
        return _FakeDoc(ents=self._ents)


def _nlp(*ents: tuple[str, str]) -> _FakeNLP:
    """便捷构造：若干 (text, label) 对 → _FakeNLP。"""
    return _FakeNLP([_FakeSpan(text=t, label_=lbl) for t, lbl in ents])


def test_collect_maps_person_and_org() -> None:
    """PERSON→persons、ORG→orgs，其它标签（GPE 等）忽略。"""
    nlp = _nlp(("张三", "PERSON"), ("示例集团", "ORG"), ("北京", "GPE"))
    persons, orgs = ner._collect_entities([nlp], "任意")
    assert persons == ["张三"]
    assert orgs == ["示例集团"]


def test_collect_dedups_strips_and_skips_blank() -> None:
    """重复去重、首尾空白 strip、空名跳过，保插入序。"""
    nlp = _nlp(
        ("张三", "PERSON"), (" 张三 ", "PERSON"), ("", "PERSON"), ("李四", "PERSON"),
    )
    persons, _ = ner._collect_entities([nlp], "x")
    assert persons == ["张三", "李四"]


def test_collect_unions_across_models() -> None:
    """多模型结果并集（中文模型出中文名、英文模型出英文名/机构）。"""
    zh = _nlp(("张三", "PERSON"))
    en = _nlp(("John Smith", "PERSON"), ("Acme Inc", "ORG"))
    persons, orgs = ner._collect_entities([zh, en], "x")
    assert persons == ["张三", "John Smith"]
    assert orgs == ["Acme Inc"]


def test_detector_available_and_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    """≥1 模型加载成功 → available True，detect 返回映射结果。"""
    nlp = _nlp(("张三", "PERSON"), ("示例集团", "ORG"))
    monkeypatch.setattr(ner, "_load_models", lambda names: [nlp])
    det = ner.SpacyEntityDetector(["zh_core_web_md"])
    assert det.available is True
    assert det.detect("任意文本") == (["张三"], ["示例集团"])


def test_detector_empty_text_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """空白文本直接返回空，不调用模型。"""
    nlp = _nlp(("张三", "PERSON"))
    monkeypatch.setattr(ner, "_load_models", lambda names: [nlp])
    det = ner.SpacyEntityDetector(["zh_core_web_md"])
    assert det.detect("   ") == ([], [])


def test_detector_unavailable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """无模型加载成功 → available False，detect 抛 NERUnavailableError。"""
    monkeypatch.setattr(ner, "_load_models", lambda names: [])
    det = ner.SpacyEntityDetector(["zh_core_web_md"])
    assert det.available is False
    with pytest.raises(ner.NERUnavailableError):
        det.detect("张三")


def test_detector_lazy_loads_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型只加载一次（多次 available/detect 不重复 _load_models）。"""
    calls = {"n": 0}
    nlp = _nlp(("张三", "PERSON"))

    def _fake_load(names: Sequence[str]) -> list[_FakeNLP]:
        """计数版假加载。"""
        calls["n"] += 1
        return [nlp]

    monkeypatch.setattr(ner, "_load_models", _fake_load)
    det = ner.SpacyEntityDetector(["zh_core_web_md"])
    assert det.available is True
    det.detect("x")
    det.detect("y")
    assert calls["n"] == 1


def test_get_detector_caches_by_model_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """同模型集复用同一实例（顺序无关）；不同集不同实例。"""
    monkeypatch.setattr(ner, "_DETECTOR_CACHE", {})
    a = ner.get_detector(["zh_core_web_md", "en_core_web_md"])
    b = ner.get_detector(["en_core_web_md", "zh_core_web_md"])
    c = ner.get_detector(["zh_core_web_md"])
    assert a is b
    assert a is not c


def test_reset_detector_cache_enables_after_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟「装后免重启生效」（#61）：缺模型时 detector 钉死不可用；
    reset_detector_cache 后同进程重新构造的 detector 加载新装模型即可用。"""
    monkeypatch.setattr(ner, "_DETECTOR_CACHE", {})
    # 阶段一：模型缺失 → detector 加载空、不可用，且被缓存钉死
    monkeypatch.setattr(ner, "_load_models", lambda names: [])
    stale = ner.get_detector(["zh_core_web_md"])
    assert stale.available is False
    assert ner.get_detector(["zh_core_web_md"]) is stale  # 命中同一钉死实例

    # 阶段二：模型「装好」+ reset → 缓存失效，重建 detector 可用并能检出实体
    monkeypatch.setattr(
        ner, "_load_models", lambda names: [_nlp(("张三", "PERSON"))],
    )
    ner.reset_detector_cache()
    fresh = ner.get_detector(["zh_core_web_md"])
    assert fresh is not stale
    assert fresh.available is True
    persons, _orgs = fresh.detect("任意文本")
    assert "张三" in persons


def test_probe_spacy_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """spaCy 未装 → spacy_installed False，全部模型计 missing，detector 不可用。"""
    monkeypatch.setattr(ner, "_spec_exists", lambda name: False)
    spacy_installed, installed, missing = ner.probe_availability(
        ["zh_core_web_md", "en_core_web_md"],
    )
    assert spacy_installed is False
    assert installed == []
    assert missing == ["zh_core_web_md", "en_core_web_md"]
    assert ner.detector_available(["zh_core_web_md"]) is False


def test_probe_partial_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """spaCy 装了、仅部分模型在 → 正确分桶 installed/missing，available True。"""
    present = {"spacy", "zh_core_web_md"}
    monkeypatch.setattr(ner, "_spec_exists", lambda name: name in present)
    spacy_installed, installed, missing = ner.probe_availability(
        ["zh_core_web_md", "en_core_web_md"],
    )
    assert spacy_installed is True
    assert installed == ["zh_core_web_md"]
    assert missing == ["en_core_web_md"]
    assert ner.detector_available(["zh_core_web_md", "en_core_web_md"]) is True
