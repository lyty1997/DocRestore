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

"""本地实体检测（人名/机构名），spaCy CNN 模型实现。

把人名/机构名检测从云端 LLM 挪进本地——兑现「名字不出本机」。设计见
``docs/zh/backend/pii-local-ner.md``。spaCy CNN 模型（``zh_core_web_md`` /
``en_core_web_md`` 等，**禁 ``*_trf``**）零 torch/transformers 依赖，可与 OCR
venv 共存。模型加载昂贵，故 ``get_detector`` 做进程级单例、跨任务复用。
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

logger = logging.getLogger(__name__)

#: spaCy OntoNotes 实体标签 → 本项目两类。
_PERSON_LABEL = "PERSON"
_ORG_LABEL = "ORG"


class NERUnavailableError(RuntimeError):
    """本地 NER 后端不可用（spaCy 未装 / 配置模型全部缺失）。

    上层应把它语义化为「检测失败」(返回 None lexicon)，再按
    ``block_cloud_on_detect_failure`` fail-closed，绝不放未脱敏文本上云。
    """


# --- spaCy 对象的最小结构化类型边界（mypy --strict 下不写 Any）---------------


class _SpacySpan(Protocol):
    """spaCy 实体 span 的最小接口（只读 text/label_）。"""

    @property
    def text(self) -> str:
        """实体原文。"""
        ...

    @property
    def label_(self) -> str:
        """实体标签（如 PERSON / ORG）。"""
        ...


class _SpacyDoc(Protocol):
    """spaCy Doc 的最小接口（只用 ents）。"""

    @property
    def ents(self) -> Iterable[_SpacySpan]:
        """识别出的实体序列。"""
        ...


class _SpacyNLP(Protocol):
    """spaCy Language pipeline 的最小接口（可调用，返回 Doc）。"""

    def __call__(self, text: str, /) -> _SpacyDoc:
        """对文本跑 pipeline，返回 Doc。"""
        ...


class LocalEntityDetector(Protocol):
    """本地实体检测器接缝。当前唯一实现 ``SpacyEntityDetector``（GLiNER 已弃用）。"""

    @property
    def available(self) -> bool:
        """是否就绪（至少一个配置模型加载成功）。"""
        ...

    def detect(self, text: str) -> tuple[list[str], list[str]]:
        """检测文本，返回 ``(person_names, org_names)``（按出现顺序去重）。"""
        ...


# --- 廉价可用性探测（不导入 spacy / 不加载模型）-----------------------------


def _spec_exists(name: str) -> bool:
    """模块/包是否可被找到（``find_spec``，不导入、不执行）。

    spaCy 模型本身是顶层包（``zh_core_web_md`` 即包名），故可用 ``find_spec``
    判断其是否已安装而无需导入 spacy。对非法名容错返回 False。
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def probe_availability(
    model_names: Sequence[str],
) -> tuple[bool, list[str], list[str]]:
    """廉价探测（**不加载模型**）：返回 ``(spacy_installed, installed, missing)``。

    供请求级 fail-fast 校验（§5）与 ``GET /ner/status`` 使用。spaCy 未装时
    所有模型计为 missing。
    """
    if not _spec_exists("spacy"):
        return False, [], list(model_names)
    installed = [m for m in model_names if _spec_exists(m)]
    missing = [m for m in model_names if m not in installed]
    return True, installed, missing


def detector_available(model_names: Sequence[str]) -> bool:
    """≥1 个配置模型就绪（廉价探测，不加载）。供 fail-fast 与 status 复用。"""
    spacy_installed, installed, _ = probe_availability(model_names)
    return spacy_installed and len(installed) > 0


# --- 加载与检测 -------------------------------------------------------------


def _load_models(model_names: Sequence[str]) -> list[_SpacyNLP]:
    """加载模型列表，返回成功加载的 nlp（单个失败跳过 + 告警，不抛）。

    spaCy 未装 → 空列表。模型缺失/损坏 → 跳过该模型。设计为模块级函数便于
    单测 monkeypatch（注入 fake nlp，无需下载真实模型）。
    """
    if not _spec_exists("spacy"):
        logger.warning("spaCy 未安装，本地 NER 不可用")
        return []
    import spacy  # 惰性导入（可选依赖，未装时上面已返回）

    nlps: list[_SpacyNLP] = []
    for name in model_names:
        try:
            nlp: _SpacyNLP = spacy.load(name)
        except Exception as exc:  # 模型缺失/损坏/版本不兼容 → 跳过该模型
            logger.warning("spaCy 模型加载失败，跳过：%s（%s）", name, exc)
            continue
        nlps.append(nlp)
    if not nlps:
        logger.warning("无任何 spaCy 模型加载成功，本地 NER 不可用")
    return nlps


def _collect_entities(
    nlps: Sequence[_SpacyNLP], text: str,
) -> tuple[list[str], list[str]]:
    """对每个 nlp 跑 ``text``，并集 PERSON→persons / ORG→orgs，按出现顺序去重。

    ``detect`` 的纯逻辑（无加载副作用），便于单测直接喂 fake nlp。用 dict
    保插入序去重。
    """
    persons: dict[str, None] = {}
    orgs: dict[str, None] = {}
    for nlp in nlps:
        for ent in nlp(text).ents:
            name = ent.text.strip()
            if not name:
                continue
            if ent.label_ == _PERSON_LABEL:
                persons.setdefault(name, None)
            elif ent.label_ == _ORG_LABEL:
                orgs.setdefault(name, None)
    return list(persons), list(orgs)


class SpacyEntityDetector:
    """spaCy CNN 模型实体检测。惰性加载、进程级单例复用（``get_detector``）。

    ``available`` 触发加载并报告是否 ≥1 模型就绪；``detect`` 在不可用时抛
    ``NERUnavailableError``。线程安全（首次加载受锁保护）。
    """

    def __init__(self, model_names: Sequence[str]) -> None:
        """记录待加载模型名（**不**在此加载，首次 detect/available 时才加载）。"""
        self._model_names: tuple[str, ...] = tuple(model_names)
        self._nlps: list[_SpacyNLP] = []
        self._loaded = False
        self._load_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        """首次调用时加载所有配置模型（双检锁，进程内只加载一次）。"""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._nlps = _load_models(self._model_names)
            self._loaded = True

    @property
    def available(self) -> bool:
        """是否就绪（触发惰性加载；≥1 模型加载成功为 True）。"""
        self._ensure_loaded()
        return len(self._nlps) > 0

    def detect(self, text: str) -> tuple[list[str], list[str]]:
        """检测人名/机构名，返回 ``(persons, orgs)``（跨模型并集、按出现序去重）。

        不可用（无模型加载成功）→ 抛 ``NERUnavailableError``。空白文本直接返回
        空。检测本身不抛业务异常（坏实体由下游 ``_is_safe_entity`` 兜底）。
        """
        self._ensure_loaded()
        if not self._nlps:
            raise NERUnavailableError(
                "本地 NER 不可用：spaCy 未安装或配置模型全部缺失",
            )
        if not text.strip():
            return [], []
        return _collect_entities(self._nlps, text)


#: 进程级 detector 缓存（键=排序后的模型名元组），跨任务复用避免重复加载。
_DETECTOR_CACHE: dict[tuple[str, ...], SpacyEntityDetector] = {}
_CACHE_LOCK = threading.Lock()


def get_detector(model_names: Sequence[str]) -> SpacyEntityDetector:
    """按模型集惰性构造并缓存 detector（进程内一次加载、跨任务复用），线程安全。

    键为排序后的模型名元组——同一组模型复用同一实例（含其已加载的 nlp）。
    ``PIIGuard.detect_entities`` 传 ``cfg.ner_models`` 调用本函数。
    """
    key = tuple(sorted(model_names))
    cached = _DETECTOR_CACHE.get(key)
    if cached is not None:
        return cached
    with _CACHE_LOCK:
        cached = _DETECTOR_CACHE.get(key)
        if cached is not None:
            return cached
        detector = SpacyEntityDetector(model_names)
        _DETECTOR_CACHE[key] = detector
        return detector
