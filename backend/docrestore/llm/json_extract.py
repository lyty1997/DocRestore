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

r"""从 LLM 输出剥出 JSON 文本的统一 helper（#66：合并 code_refine / code_repair
两份近乎逐字相同的实现）。

兼容三种形态：```json 围栏 / 纯 JSON / 前后带自然语言说明。
"""

from __future__ import annotations


def extract_json(raw: str) -> str:
    r"""从 LLM 输出中剥出 JSON 文本。

    依次：去 ``\`\`\`...\`\`\``` 围栏 → 取首个 ``{`` 到末个 ``}`` 之间的子串。
    无法定位成对花括号时原样返回 ``raw.strip()``（由调用方 ``json.loads`` 报错）。
    """
    text = raw.strip()
    if text.startswith("```"):
        # 剥围栏：丢掉首行（可能是 ```json）与末尾 ```
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text
