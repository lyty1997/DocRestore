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

import logging
import re
from difflib import SequenceMatcher

import aiofiles

from docrestore.models import PageOCR
from docrestore.ocr.base import OCR_RESULT_FILENAME

logger = logging.getLogger(__name__)


#: 网页代码框 UI 噪音模式。单行独占（首尾无其他字符）才匹配，防误伤正文。
#: 原图是飞书/Confluence 风格在线文档的拍照，代码框顶部的「语言标签 +
#: 复制按钮」会被 OCR 识别成 `Plain Text 复制代码` / `Bash 复制代码` 这种文本；
#: 还有前缀可能带 ▶/▼/☐ 等视觉符号。这些全是噪音，整行删除。
#: 导出给 markdown_polish 在 final_refine 之后兜底再扫一遍（LLM 偶有漏）。
UI_NOISE_LINE_RE = _UI_NOISE_LINE_RE = re.compile(
    r"^\s*(?:[▶▼☐◆✦◇□■●○▪▫]\s*)?"
    r"(?:"
    r"(?:Plain\s+Text|Bash|Shell|Python|Java|JavaScript|TypeScript|"
    r"C\+\+|C#|Go|Rust|Ruby|PHP|SQL|JSON|YAML|XML|HTML|CSS|Markdown|"
    r"Dockerfile|Makefile|Kotlin|Swift|C)\s*复制代码"
    r"|复制代码"
    r")\s*$",
    re.IGNORECASE,
)


#: 退化重复折叠的最小触发长度（字符）。低于此的重复段（短 markdown 分隔线、
#: ``...`` 省略号、``0x0000`` 等）一律不动，避免误伤正文。60 字符 ≈ 2 字符单元
#: 重复 30 次，自然正文 / 代码几乎不可能出现。
DEGENERATE_RUN_MIN_CHARS = 60

#: 纯分隔符单元集合：单元仅由这些字符构成的重复不折叠，保护 markdown 下划线
#: （``====``）、代码 banner（``####``）、分隔线（``----``）、表格边框等结构。
DEGENERATE_DIVIDER_CHARS = frozenset("-=*#_~|+. \t")

#: 短单元（1–4 字符）重复 ≥9 次（含首项）的游程。真正阈值由
#: ``DEGENERATE_RUN_MIN_CHARS`` 在回调里按字符数把关；此处量词压低以统一覆盖
#: 各单元长度。``.`` 不跨行（无 DOTALL），游程天然限定单行内。反向引用游程为
#: 线性匹配（单元长度仅 4 种选择），无灾难性回溯。
DEGENERATE_RUN_RE = re.compile(r"(.{1,4}?)\1{8,}")


class OCRCleaner:
    """OCR 输出清洗器"""

    async def clean(self, page: PageOCR) -> PageOCR:
        """读取 OCR 结果文件并清洗，填充 cleaned_text。

        步骤：collapse_degenerate_runs → remove_ui_noise → remove_repetitions
        → remove_garbage → normalize_whitespace。返回同一个 PageOCR 对象。

        折叠放最前：先把退化超长行收缩掉，既避免它撑爆下游 LLM 精修 / 吞尾页，
        也让段落级 remove_repetitions 的 SequenceMatcher 不必在 8000+ 字符的
        单行上做 O(n²) 比对。
        """
        if page.output_dir is not None:
            mmd_path = page.output_dir / OCR_RESULT_FILENAME
            if mmd_path.exists():
                async with aiofiles.open(
                    mmd_path, encoding="utf-8"
                ) as f:
                    text = await f.read()
            elif page.raw_text:
                # 防御性回退：目录存在但结果文件缺失（可能因取消中断）
                logger.warning(
                    "%s 不存在，回退使用 raw_text: %s",
                    OCR_RESULT_FILENAME, mmd_path,
                )
                text = page.raw_text
            else:
                msg = f"OCR 输出文件不存在: {mmd_path}"
                raise FileNotFoundError(msg)
        else:
            text = page.raw_text

        text = self.collapse_degenerate_runs(text)
        text = self.remove_ui_noise(text)
        text = self.remove_repetitions(text)
        text = self.remove_garbage(text)
        text = self.normalize_whitespace(text)
        page.cleaned_text = text
        return page

    def collapse_degenerate_runs(
        self, text: str, min_run_chars: int = DEGENERATE_RUN_MIN_CHARS,
    ) -> str:
        """折叠"短单元超长重复"的退化行（OCR 撞二进制 / 内存 dump 的产物）。

        典型来源：GDB 内存 dump（``pui8Src=0x... "..."``）里不可读的字节串被
        OCR 识别成 ``wm``/``nt``/``mu``/``00`` 这类 1–4 字符单元重复成百上千次的
        超长单行（实测单行 8000+ 字符）。这类行：

          - 不是正文、无信息量；
          - 喂给云端 LLM 精修会让模型陷入同款重复生成直到 finish_reason=length
            截断，触发二分递归重试 → token 爆炸（实测单任务 28M+）；
          - 截断会吞掉段内后续 page marker，导致文档尾页整段消失。

        ``remove_repetitions`` 只比对段落级相似度、``remove_garbage`` 只删非可读
        字符（字母/数字属可读，不删），都漏掉这种"全 ASCII 短单元重复"。本方法
        就地把超长重复段折叠为"样本 + 标记"，保留行首上下文（dump 的变量名 /
        地址仍可读）。

        保护：单元仅由 markdown / 代码分隔符（``-=*#_~|+.`` 与空白）构成的重复
        （``####`` banner、``====`` 下划线、``----`` 分隔线）不折叠。

        ``min_run_chars``：游程总长 < 此值不折叠（默认 60）。
        """
        collapsed = 0

        def _collapse(m: re.Match[str]) -> str:
            nonlocal collapsed
            run = m.group(0)
            unit = m.group(1)
            if len(run) < min_run_chars:
                return run
            if all(ch in DEGENERATE_DIVIDER_CHARS for ch in unit):
                return run
            collapsed += 1
            reps = len(run) // len(unit)
            sample = unit * min(3, reps)
            return f"{sample}…[退化重复折叠 {len(run)}字符]"

        result = DEGENERATE_RUN_RE.sub(_collapse, text)
        if collapsed:
            logger.debug("退化重复折叠: 处理 %d 处超长重复", collapsed)
        return result

    def remove_ui_noise(self, text: str) -> str:
        """移除拍照网页文档产生的稳定字面 UI 噪音行。

        目前覆盖：
          - 代码框顶部的「{语言标签} 复制代码」行（约 20 种常见语言）
          - 独立的「复制代码」一行
          - 行首以 ▶/▼/☐/◆/✦ 等视觉符号开头的上述模式

        只按整行匹配，不会误伤含这些词的正文。
        """
        kept: list[str] = []
        removed = 0
        for line in text.splitlines():
            if _UI_NOISE_LINE_RE.match(line):
                removed += 1
                continue
            kept.append(line)
        if removed:
            logger.debug("UI 噪音清理: 移除 %d 行", removed)
        return "\n".join(kept)

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
