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

"""输出目录边界守卫：把请求级可控的 ``output_dir`` 锁在受信工作根下，防任意删除。

背景（#34）：``output_dir`` 由前端请求体原样带入，任务删除时
``shutil.rmtree(output_dir)`` 递归删该目录。构造一个非法 ``image_dir`` 让任务
快速进终态（FAILED），再 ``DELETE /tasks/{id}``，即可让服务进程 rmtree 任意
目录（如 ``/home/user/work``）——``ignore_errors=True`` 静默且不可逆。

策略：``output_dir`` 必须**严格落在受信工作根下**（``resolve()`` 折叠 ``..`` /
符号链接后用 ``is_relative_to`` 判定，且不能等于工作根本身，否则等于授权删整个
工作根）。工作根默认取系统临时目录（正是 ``create_task`` 默认输出落点
``{tempdir}/docrestore_{id}`` 的父目录），可经 env ``DOCRESTORE_WORK_ROOT``
拓宽——给"想把产物落到持久化目录"的部署留显式逃生口（镜像 #33 的
``DOCRESTORE_LLM_API_BASE_ALLOWLIST`` 白名单 env）。

两道防线（与 #32/#33 同构）：
1. 建任务时（``routes.create_task``）校验用户 ``output_dir``，越界 fail-fast 400。
2. 删除 sink（``task_manager.delete_task``）rmtree 前**再次**校验（TOCTOU 防御：
   覆盖历史越界任务、DB 篡改、未来漏接的建任务路径），越界则拒删不触碰目录。

注：``image_dir`` 是只读输入、从不被删（且合法指向 NAS 等外部只读路径），故不施加
同约束——加边界反而误杀正常用法。详见 known-issues #34。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: 工作根 override 环境变量名（空 / 未设 = 用系统临时目录）。
_ENV_WORK_ROOT = "DOCRESTORE_WORK_ROOT"


class OutputDirRejected(ValueError):
    """``output_dir`` 越界（不在受信工作根下）。

    定义在 pipeline 层、继承 ``ValueError`` 而非 ``ApiBusinessError``：守卫被
    pipeline（``task_manager``）与 api（``routes``）两层共用，不能反向依赖 api
    错误类型；由 ``routes`` 捕获后映射成 ``OUTPUT_DIR_REJECTED`` 400。
    """


def resolve_work_root() -> Path:
    """返回受信工作根的**已解析绝对路径**。

    env ``DOCRESTORE_WORK_ROOT`` 非空则用它，否则用系统临时目录。两条路径都过
    ``resolve()``，保证后续与候选 ``output_dir`` 在同一解析口径下比较（避免
    macOS ``/tmp`` → ``/private/tmp`` 这类符号链接造成的误判）。
    """
    raw = os.environ.get(_ENV_WORK_ROOT, "").strip()
    base = raw if raw else tempfile.gettempdir()
    return Path(base).resolve()


def _resolve_candidate(output_dir: str) -> Path:
    """把候选 ``output_dir`` 解析成绝对路径（折叠 ``..`` 与符号链接）。

    ``resolve()`` 在路径尚不存在时（建任务阶段 output_dir 还没创建）仍会按词法
    折叠 ``..``，已存在的符号链接段会被真实跟随——这正是堵 ``/tmp/evil →
    /home/user/work`` 这类符号链接逃逸所需。
    """
    return Path(output_dir).resolve()


def validate_output_dir(
    output_dir: str, *, work_root: Path | None = None,
) -> Path:
    """校验 ``output_dir`` 严格落在工作根下；越界抛 ``OutputDirRejected``。

    返回解析后的绝对路径（调用方可据此落库 / 创建）。``work_root`` 缺省取
    ``resolve_work_root()``；测试可显式传入临时根以摆脱真实 env / tempdir。
    """
    value = output_dir.strip()
    if not value:
        raise OutputDirRejected("输出目录为空")

    # 显式传入的 work_root 也过 resolve：与候选同一解析口径，避免根处于符号链接
    # 路径下（如 macOS /tmp → /private/tmp、测试 tmp_path）造成 is_relative_to 误判。
    root = work_root.resolve() if work_root is not None else resolve_work_root()
    resolved = _resolve_candidate(value)

    # 必须是工作根的**严格**子路径：等于工作根本身 = 授权删整个工作根，拒。
    if resolved == root or not resolved.is_relative_to(root):
        logger.warning(
            "output_dir 越界，拒绝: dir=%s resolved=%s root=%s",
            output_dir, resolved, root,
        )
        raise OutputDirRejected(
            f"输出目录必须落在工作根 {root} 之下（当前解析为 {resolved}）",
        )
    return resolved


def output_dir_within_root(
    output_dir: str, *, work_root: Path | None = None,
) -> bool:
    """非抛版边界判定，供删除 sink 在 rmtree 前二次校验（TOCTOU 防御）。

    任何异常（无法解析、空值、越界）一律判为**不在**根下 → 返回 ``False``，让
    sink 走"拒删"安全分支，宁可漏删不误删。
    """
    try:
        validate_output_dir(output_dir, work_root=work_root)
    except (OutputDirRejected, OSError, ValueError):
        return False
    return True
