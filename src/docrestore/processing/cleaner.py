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

"""OCR 输出清洗器

对单页 OCR 结果做页内去重、乱码移除、空行规范化。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import aiofiles

from docrestore.models import PageOCR


class OCRCleaner:
    """OCR 输出清洗器"""

    async def clean(self, page: PageOCR) -> PageOCR:
        """读取 result.mmd 并清洗，填充 cleaned_text。

        步骤：remove_repetitions → remove_garbage → normalize_whitespace
        返回同一个 PageOCR 对象。
        """
        if page.output_dir is not None:
            mmd_path = page.output_dir / "result.mmd"
            if not mmd_path.exists():
                msg = f"OCR 输出文件不存在: {mmd_path}"
                raise FileNotFoundError(msg)
            async with aiofiles.open(
                mmd_path, encoding="utf-8"
            ) as f:
                text = await f.read()
        else:
            text = page.raw_text

        text = self.remove_repetitions(text)
        text = self.remove_garbage(text)
        text = self.normalize_whitespace(text)
        page.cleaned_text = text
        return page

    def remove_repetitions(
        self, text: str, threshold: float = 0.9
    ) -> str:
        """按空行分段，相邻段落相似度 > threshold 的只保留第一个。"""
        paragraphs = re.split(r"\n\s*\n", text)
        if len(paragraphs) <= 1:
            return text

        result: list[str] = [paragraphs[0]]
        for para in paragraphs[1:]:
            prev = result[-1].strip()
            curr = para.strip()
            if not prev or not curr:
                result.append(para)
                continue
            similarity = SequenceMatcher(
                None, prev, curr
            ).ratio()
            if similarity <= threshold:
                result.append(para)
        return "\n\n".join(result)

    def remove_garbage(
        self, text: str, threshold: int = 20
    ) -> str:
        """移除连续非 CJK/ASCII 可读字符超过 threshold 的片段。"""
        # 匹配连续非 CJK、非 ASCII 可打印、非常见标点的字符
        pattern = re.compile(
            r"[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
            r"a-zA-Z0-9\s\-_.,;:!?()（）【】「」"
            r"《》、。，；：！？·…—\-\[\]{}#*+=/\\|@&^~`'\""
            r"$%<>]{"
            + str(threshold)
            + r",}"
        )
        return pattern.sub("", text)

    def normalize_whitespace(self, text: str) -> str:
        """压缩连续 3+ 空行为 2 个。"""
        return re.sub(r"\n{3,}", "\n\n", text)
