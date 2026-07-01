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

"""版面 sidecar 公共基元：整型序列解析 + JSON 磁盘读写。

``layout_sidecar`` / ``code_layout_sidecar`` / ``ppt_layout`` 三个 sidecar 模块共用的
坐标解析（``as_int_pair`` / ``as_int_quad``）与磁盘 I/O（``write_json_sidecar`` /
``load_json_sidecar``）抽到这里，避免三份逐字节重复——任一处加固（拒 bool 坐标 /
限制读入体积 / 补异常类型）只改一处，三个 reader 不再漂移。
"""

from __future__ import annotations

import json
from pathlib import Path


def as_int_pair(raw: object) -> tuple[int, int] | None:
    """长度 2 的整型序列；非法（非 list / 长度错 / 元素非整）返回 None。"""
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    try:
        vals = [int(v) for v in raw]
    except (TypeError, ValueError):
        return None
    return (vals[0], vals[1])


def as_int_quad(raw: object) -> tuple[int, int, int, int] | None:
    """长度 4 的整型序列；非法返回 None。"""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        vals = [int(v) for v in raw]
    except (TypeError, ValueError):
        return None
    return (vals[0], vals[1], vals[2], vals[3])


def write_json_sidecar(path: Path, data: dict[str, object]) -> Path:
    """把 ``data`` 以 UTF-8 + 两空格缩进写到 ``path``（sidecar 统一格式）→ path。"""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_json_sidecar(path: Path) -> object | None:
    """读 ``path`` 的 JSON；缺失 / 损坏（OSError / JSONDecodeError）→ None（容损）。"""
    if not path.exists():
        return None
    try:
        # 显式标注 object：json.loads 返回 Any，收窄避免 no-any-return。
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data
