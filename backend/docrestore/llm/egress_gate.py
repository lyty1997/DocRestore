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

"""出云闸口（#67）：把 PII fail-closed 与实体脱敏从「每个云端调用点自觉执行」
下沉为「所有出云必经的单点强制」。

设计见 ``docs/zh/backend/pii-cloud-egress-gate.md``。机制：任务级
``CloudEgressPolicy`` 经 ``ContextVar`` 携带（task-local，``process_tree`` 并发
子目录互不串味），``BaseLLMRefiner._call_llm`` 入口调 ``enforce_egress``：

1. ``provider == "local"`` / 未安装策略 → 放行（本地不出云 / 测试直构旧行为）。
2. ``block_cloud`` 为真 → 抛 ``CloudEgressBlockedError``（在熔断/限流之前，
   不占 semaphore、不计熔断；由各精修路径既有 ``except`` 回退为「该步退原文」）。
3. 否则对 ``messages``（非 ``system``）与 ``prediction.content`` 施**仅实体替换**
   （人名/机构精确串替换，不跑结构化正则——对代码标识符/import 路径安全）。
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from docrestore.privacy.guard import PIIGuard
    from docrestore.privacy.redactor import EntityLexicon


logger = logging.getLogger(__name__)


class CloudEgressBlockedError(RuntimeError):
    """出云被 fail-closed 闸口拒绝（实体检测失败 + ``block_cloud_on_detect_failure``）。

    继承 ``RuntimeError``，与 ``LLMCircuitOpenError`` 同源——被各精修路径既有的
    ``except`` 回退接住，等价于「这一步退原文/reassembled」，不外发未脱敏内容。
    """


@dataclass
class CloudEgressPolicy:
    """任务级出云策略。

    在所属 leaf 任务内创建并 ``update_egress_policy`` 原地更新——``ContextVar``
    task-local，每个 ``process_tree`` 子目录是独立 ``asyncio`` task，互不串味。

    - ``block_cloud``：fail-closed 是否拒发（实体检测失败 + 配置要求阻断）。
    - ``lexicon``：当前任务实体词表（人名/机构），闸口据此做实体兜底。
    - ``guard``：请求级 ``PIIGuard``（未开 PII 时为 None，闸口不脱敏）。
    """

    block_cloud: bool = False
    lexicon: EntityLexicon | None = None
    guard: PIIGuard | None = None


_egress_policy: ContextVar[CloudEgressPolicy | None] = ContextVar(
    "docrestore_cloud_egress_policy", default=None,
)


@contextlib.contextmanager
def egress_scope(policy: CloudEgressPolicy) -> Iterator[CloudEgressPolicy]:
    """在当前任务作用域安装出云策略（**必须在 leaf 协程内调用**以保证隔离）。

    退出时 ``reset``，避免在 ``process_tree`` warmup→gather 同一父 task 内泄漏到
    后续子任务的 context 复制。
    """
    token = _egress_policy.set(policy)
    try:
        yield policy
    finally:
        _egress_policy.reset(token)


def current_egress_policy() -> CloudEgressPolicy | None:
    """读当前任务的出云策略；未安装返回 None。"""
    return _egress_policy.get()


def update_egress_policy(
    *, block_cloud: bool, lexicon: EntityLexicon | None,
) -> None:
    """更新当前任务策略的 ``block_cloud`` / ``lexicon``（二者就绪后调用）。

    未安装策略时 no-op（本地 provider / 测试直构 refiner）。原地更新当前 task 的
    策略对象，task-local 不串味。
    """
    policy = _egress_policy.get()
    if policy is None:
        return
    policy.block_cloud = block_cloud
    policy.lexicon = lexicon


def enforce_egress(kwargs: dict[str, object], provider: str) -> None:
    """出云闸口：fail-closed 拒发 + 仅实体兜底。在 ``_call_llm`` 入口（熔断前）调。

    ``kwargs`` 为 ``litellm.acompletion`` 入参，**原地**脱敏其 ``messages`` /
    ``prediction``（均为本次调用新建，可安全 mutate）。
    """
    if provider == "local":
        return
    policy = _egress_policy.get()
    if policy is None:
        # 云端 provider 却没装出云策略：生产路径 process_tree 必在 leaf 协程内
        # egress_scope 安装策略（PII 关也会装 guard=None 的空策略），故此分支只应
        # 出现在"测试直构 refiner"或"新增了绕过 pipeline 的云端调用"——后者会让
        # 未脱敏内容静默出云，是接线 bug，必须可见而非静默 fail-open。
        logger.warning(
            "出云闸口未安装策略，跳过 PII 脱敏直接出云（provider=%s）；"
            "若非测试直构，请确认该云端调用位于 pipeline 的 egress_scope 内",
            provider,
        )
        return
    if policy.block_cloud:
        msg = "PII fail-closed：实体检测失败，已拒绝出云调用（block_cloud）"
        raise CloudEgressBlockedError(msg)

    guard = policy.guard
    lexicon = policy.lexicon
    if guard is None or lexicon is None:
        return
    _redact_egress_kwargs(kwargs, guard, lexicon)


def _redact_egress_kwargs(
    kwargs: dict[str, object],
    guard: PIIGuard,
    lexicon: EntityLexicon,
) -> None:
    """对出云 ``kwargs`` 的 ``messages``（非 ``system``）与 ``prediction.content``
    施仅实体替换（原地）。"""
    messages = kwargs.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "system":
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = guard.redact_entities_only(
                    content, lexicon,
                )

    prediction = kwargs.get("prediction")
    if isinstance(prediction, dict):
        pred_content = prediction.get("content")
        if isinstance(pred_content, str):
            prediction["content"] = guard.redact_entities_only(
                pred_content, lexicon,
            )
