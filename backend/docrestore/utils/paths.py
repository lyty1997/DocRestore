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

"""路径安全化工具函数

将用户/LLM 产生的标题转为安全目录名，防止路径穿越。
"""

from __future__ import annotations

import re

# 路径分隔符和危险字符
_UNSAFE_CHARS_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

# 目录名最大长度
_MAX_DIRNAME_LEN = 64


def sanitize_dirname(title: str) -> str:
    """将标题转为安全目录名。

    - 去除首尾空白
    - 替换路径分隔符和危险字符为下划线
    - 折叠连续下划线
    - 截断到 64 字符
    - 禁止 . 和 .. 开头（防路径穿越）
    - 空标题返回空字符串
    """
    name = title.strip()
    if not name:
        return ""

    # 替换危险字符
    name = _UNSAFE_CHARS_RE.sub("_", name)

    # 移除 .. 序列（路径穿越残留）
    name = name.replace("..", "")

    # 折叠连续下划线
    name = re.sub(r"_+", "_", name)

    # 去除首尾下划线
    name = name.strip("_")

    # 防止 . 或 .. 开头
    name = name.lstrip(".")

    # 截断
    if len(name) > _MAX_DIRNAME_LEN:
        name = name[:_MAX_DIRNAME_LEN].rstrip("_")

    return name
