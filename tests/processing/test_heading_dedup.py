# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""H2 章节去重测试。"""

from __future__ import annotations

from docrestore.processing.heading_dedup import dedup_h2_sections


class TestNoDedup:
    def test_no_h2_unchanged(self) -> None:
        md = "# 标题\n正文内容\n更多正文\n"
        out, removed = dedup_h2_sections(md)
        assert out == md
        assert removed == []

    def test_single_h2_unchanged(self) -> None:
        md = "## 唯一章节\n正文\n"
        out, removed = dedup_h2_sections(md)
        assert out == md
        assert removed == []

    def test_different_h2_titles_unchanged(self) -> None:
        md = "## A\nA 内容\n## B\nB 内容\n## C\nC 内容\n"
        out, removed = dedup_h2_sections(md)
        assert out == md
        assert removed == []


class TestDedupSameContent:
    def test_two_identical_sections_keeps_first(self) -> None:
        body = "完全相同的章节内容\n第二行\n第三行\n"
        md = f"## 编译\n{body}## 调试\n调试内容\n## 编译\n{body}"
        out, removed = dedup_h2_sections(md)
        # 两次 ## 编译 → 删一个
        assert out.count("## 编译") == 1
        assert "## 调试" in out
        assert len(removed) == 1
        rec = removed[0]
        assert rec["title"] == "编译"

    def test_three_identical_keeps_one(self) -> None:
        body = "正文长内容很长很长。\n更多内容。\n"
        md = (
            f"## A\n{body}## A\n{body}## A\n{body}"
            "## B\nB 不同内容\n"
        )
        out, removed = dedup_h2_sections(md)
        assert out.count("## A") == 1
        assert out.count("## B") == 1
        assert len(removed) == 2


class TestKeepsLongerVersion:
    def test_truncated_then_complete_keeps_complete(self) -> None:
        """半截 + 完整 → 保完整版（asymmetric prefix path）。"""
        truncated = (
            "完成 DDR 配置后，重新编译完整镜像或单独编译"
            " u-boot image 和 Linux, theod jn"
        )
        complete = (
            "完成 DDR 配置后，重新编译完整镜像或单独编译"
            " u-boot image 和 linux-thead image"
            "（编译方式参考 SDK 使用说明）。"
            "更多详细的内容若干字符填充让 complete"
            "明显比 truncated 长，触发 length_ratio ≤ 0.7 路径。"
            "再加一段填充内容，确保长度差距足够大。"
        )
        md = (
            f"## 编译方式\n{truncated}\n\n"
            "<!-- page: DSC04727.JPG -->\n\n"
            f"## 编译方式\n{complete}\n"
        )
        out, removed = dedup_h2_sections(md)
        assert out.count("## 编译方式") == 1
        # 必须保留完整版
        assert "linux-thead image" in out
        assert "theod jn" not in out
        # 删除记录：reason 应为 truncated_prefix
        assert removed[0]["reason"] == "truncated_prefix"
        kept_chars = removed[0]["kept_body_chars"]
        removed_chars = removed[0]["removed_body_chars"]
        assert isinstance(kept_chars, int)
        assert isinstance(removed_chars, int)
        assert kept_chars > removed_chars


class TestDifferentSectionsKept:
    def test_same_title_different_content_both_kept(self) -> None:
        """同名 H2 但内容差异极大（真不同章节）→ 全部保留。"""
        md = (
            "## 调试\n用 GDB 调试 ARM 平台。\n"
            "GDB 启动命令是 gdb-multiarch。\n"
            "## 其他\n其他正文\n"
            "## 调试\n用 OpenOCD 调试 RISC-V 平台。\n"
            "OpenOCD 配置完全不同的硬件。\n"
        )
        out, removed = dedup_h2_sections(md)
        # 两个 ## 调试 都应保留
        assert out.count("## 调试") == 2
        assert removed == []

    def test_similar_but_not_truncated_kept(self) -> None:
        """同名 H2 + body 共享前缀 + 各自有独有尾部（不是 truncated）→ 保全。

        这是关键安全用例：避免误删"两节内容相似但都有独有尾部"的场景。
        例如 H2 段尾 + 正文继续到下个 H2 之前的"邻居正文"。
        """
        shared = "公共前缀正文很长很长。" * 5  # ~70 chars
        body_a = shared + "中间正文 A 独有尾部"
        body_b = shared + "后续正文 B 独有尾部"
        md = f"## 重复\n{body_a}\n## 别的\n别的内容\n## 重复\n{body_b}\n"
        out, removed = dedup_h2_sections(md)
        # 两节长度接近、ratio 高（共享 70/77）但 < 0.95，不该合并
        assert out.count("## 重复") == 2
        assert removed == []
        assert "中间正文 A 独有尾部" in out
        assert "后续正文 B 独有尾部" in out


class TestSpacing:
    def test_paragraph_separator_after_removal(self) -> None:
        body = "相同的章节体内容很长。\n第二行。\n第三行。\n"
        md = (
            "前章节内容\n\n"
            f"## 重复\n{body}\n"
            "中间正文。\n\n"
            f"## 重复\n{body}\n"
            "后章节内容\n"
        )
        out, _ = dedup_h2_sections(md)
        # 不会粘连
        assert "中间正文。后章节内容" not in out
        # 没有 4 连续换行
        assert "\n\n\n\n" not in out
        assert "前章节内容" in out
        assert "中间正文" in out
        assert "后章节内容" in out


class TestRealWorldRetryRemainder:
    """复现实测：signal 4 retry 后还残留的重复 H2。"""

    def test_bootrom_duplicate(self) -> None:
        """启动流程介绍：BootROM 章节重复 1 次。"""
        body = (
            "BootROM 是固化在 ROM 里的一段代码，"
            "上电启动后，它会初始化外部存储设备。\n"
            "其主要功能如下：\n"
            "- 初始化 CPU\n"
            "- 加载 SPL 到 SRAM\n"
        )
        md = (
            "## 概述\n概述内容\n"
            f"## BootROM\n{body}\n"
            "## SPL\nSPL 内容\n"
            f"## BootROM\n{body}\n"
        )
        out, removed = dedup_h2_sections(md)
        assert out.count("## BootROM") == 1
        assert len(removed) == 1
        assert removed[0]["title"] == "BootROM"
