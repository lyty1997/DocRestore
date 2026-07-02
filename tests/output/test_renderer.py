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

"""Renderer 单元测试"""

from __future__ import annotations

from pathlib import Path

import pytest

from docrestore.models import MergedDocument
from docrestore.output.renderer import (
    ANCHORED_DOCUMENT_FILENAME,
    Renderer,
    strip_page_markers,
)
from docrestore.pipeline.config import OutputConfig


def test_strip_page_markers_removes_only_markers() -> None:
    """strip_page_markers 只剥 page marker，正文与其它留白保留。"""
    md = (
        "<!-- page: a.jpg -->\n# 标题\n\n正文段\n"
        "<!-- page: b.jpg -->\n结尾段"
    )
    out = strip_page_markers(md)
    assert "<!-- page:" not in out
    assert "# 标题" in out
    assert "正文段" in out
    assert "结尾段" in out


@pytest.fixture
def renderer() -> Renderer:
    """创建 Renderer 实例"""
    return Renderer(OutputConfig())


class TestRenderer:
    """Renderer 测试"""

    @pytest.mark.asyncio
    async def test_removes_page_markers(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """移除页边界标记"""
        doc = MergedDocument(
            markdown=(
                "<!-- page: page1.jpg -->\n"
                "# 标题\n\n"
                "正文内容\n"
                "<!-- page: page2.jpg -->\n"
                "更多内容"
            )
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")
        assert "<!-- page:" not in content
        assert "# 标题" in content
        assert "正文内容" in content
        assert "更多内容" in content

    @pytest.mark.asyncio
    async def test_writes_anchored_sidecar_with_markers(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """落带 marker 的 sidecar 供水合：document.md 剥除版、sidecar 保留版。

        前端文档/PPT 左右同步滚动靠 markdown 里的 <!-- page --> 锚点；任务从 DB
        水合后会从磁盘重读 markdown，若只有剥除版 document.md 则锚点全失。
        """
        doc = MergedDocument(
            markdown=(
                "<!-- page: page1.jpg -->\n"
                "# 标题\n\n正文A\n"
                "<!-- page: page2.jpg -->\n正文B"
            )
        )
        doc_path, returned = await renderer.render(doc, tmp_path)

        # document.md：剥除版（下载/交付）
        assert "<!-- page:" not in doc_path.read_text(encoding="utf-8")

        # sidecar：带 marker（水合锚点）
        sidecar = tmp_path / ANCHORED_DOCUMENT_FILENAME
        assert sidecar.is_file()
        anchored = sidecar.read_text(encoding="utf-8")
        assert "<!-- page: page1.jpg -->" in anchored
        assert "<!-- page: page2.jpg -->" in anchored

        # 返回的内存版同样带 marker（新任务即时预览用）
        assert "<!-- page: page1.jpg -->" in returned

    @pytest.mark.asyncio
    async def test_renders_plain_content(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """普通内容正常渲染"""
        doc = MergedDocument(
            markdown=(
                "内容A\n"
                "重叠内容\n"
                "内容B"
            )
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")
        assert "内容A" in content
        assert "重叠内容" in content
        assert "内容B" in content

    @pytest.mark.asyncio
    async def test_rewrites_image_references(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """重写图片引用路径"""
        # 创建模拟的 OCR 输出目录和图片
        ocr_dir = tmp_path / "page1_OCR" / "images"
        ocr_dir.mkdir(parents=True)
        (ocr_dir / "0.jpg").write_bytes(b"fake image data")

        doc = MergedDocument(
            markdown="文本\n![](page1_OCR/images/0.jpg)\n更多文本"
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")

        # 引用已重写
        assert "![](images/page1_0.jpg)" in content
        assert "page1_OCR" not in content

        # 图片已复制
        assert (tmp_path / "images" / "page1_0.jpg").exists()

    @pytest.mark.asyncio
    async def test_rewrites_image_reference_with_dotted_stem(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """回归：文件名含点（Path.stem 只剥最后一级后缀）时图片引用仍正确重写。

        例如 my.photo.jpg → stem=my.photo → OCR 目录 my.photo_OCR；
        正则字符类若漏掉 '.' 会错配 stem，导致源图找不到、引用断裂。
        """
        ocr_dir = tmp_path / "my.photo_OCR" / "images"
        ocr_dir.mkdir(parents=True)
        (ocr_dir / "0.jpg").write_bytes(b"fake image data")

        doc = MergedDocument(
            markdown="文本\n![](my.photo_OCR/images/0.jpg)\n更多文本"
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")

        assert "![](images/my.photo_0.jpg)" in content
        assert "my.photo_OCR" not in content
        assert (tmp_path / "images" / "my.photo_0.jpg").exists()

    @pytest.mark.asyncio
    async def test_multiple_images(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """多张图片正确处理"""
        for stem in ["page1", "page2"]:
            ocr_dir = tmp_path / f"{stem}_OCR" / "images"
            ocr_dir.mkdir(parents=True)
            (ocr_dir / "0.jpg").write_bytes(b"img")

        doc = MergedDocument(
            markdown=(
                "![](page1_OCR/images/0.jpg)\n"
                "中间文本\n"
                "![](page2_OCR/images/0.jpg)"
            )
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")

        assert "![](images/page1_0.jpg)" in content
        assert "![](images/page2_0.jpg)" in content
        assert (tmp_path / "images" / "page1_0.jpg").exists()
        assert (tmp_path / "images" / "page2_0.jpg").exists()

    @pytest.mark.asyncio
    async def test_output_path(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """返回 document.md 路径"""
        doc = MergedDocument(markdown="# 文档")
        result_path, _ = await renderer.render(doc, tmp_path)
        assert result_path == tmp_path / "document.md"
        assert result_path.exists()

    @pytest.mark.asyncio
    async def test_normalizes_blank_lines(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """多余空行被压缩"""
        doc = MergedDocument(
            markdown="段落A\n\n\n\n\n段落B"
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")
        assert "\n\n\n" not in content
        assert "段落A" in content
        assert "段落B" in content

    @pytest.mark.asyncio
    async def test_missing_source_image(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """源图片不存在时不报错，引用仍被重写"""
        doc = MergedDocument(
            markdown="![](page1_OCR/images/0.jpg)"
        )
        result_path, _ = await renderer.render(doc, tmp_path)
        content = result_path.read_text(encoding="utf-8")
        assert "![](images/page1_0.jpg)" in content
        # 图片未复制（源不存在）
        assert not (
            tmp_path / "images" / "page1_0.jpg"
        ).exists()

    @pytest.mark.asyncio
    async def test_ocr_root_dir_for_subdoc(
        self, renderer: Renderer, tmp_path: Path
    ) -> None:
        """多文档场景：OCR 目录在根目录，渲染在子目录"""
        # OCR 输出在根目录
        root = tmp_path / "output_root"
        ocr_dir = root / "page1_OCR" / "images"
        ocr_dir.mkdir(parents=True)
        (ocr_dir / "0.jpg").write_bytes(b"fake image")

        # 子文档输出在子目录
        sub_dir = root / "子文档标题"
        sub_dir.mkdir(parents=True)

        doc = MergedDocument(
            markdown="![](page1_OCR/images/0.jpg)\n正文",
        )
        result_path, _ = await renderer.render(
            doc, sub_dir, ocr_root_dir=root,
        )
        content = result_path.read_text(encoding="utf-8")
        assert "![](images/page1_0.jpg)" in content
        # 图片已复制到子目录的 images/
        assert (sub_dir / "images" / "page1_0.jpg").exists()

    @pytest.mark.asyncio
    async def test_missing_source_image_logs_warning(
        self,
        renderer: Renderer,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """源图缺失不再静默：记 warning，让 document.md 死引用可被发现/排查。"""
        doc = MergedDocument(markdown="![](page1_OCR/images/0.jpg)")
        with caplog.at_level("WARNING", logger="docrestore.output.renderer"):
            await renderer.render(doc, tmp_path)
        assert any("图片源缺失" in m for m in caplog.messages)

