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

"""Pipeline.process_tree() 多子目录分支端到端测试（纯 mock，CI 友好）

覆盖：
- 叶子目录即输入目录 → 单次 process_many 调用
- 多子目录（根目录不直接含图片）→ 逐子目录分别 process_many
- 每个子文档的 doc_dir 被正确拼接子目录相对路径
- 进度回调被包装（message 含子目录标签）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docrestore.models import PageOCR, TaskProgress
from docrestore.pipeline.config import (
    LLMConfig,
    PIIConfig,
    PipelineConfig,
)
from docrestore.pipeline.pipeline import Pipeline
from docrestore.pipeline.rate_controller import RateController


@pytest.fixture(autouse=True)
def _fast_cold_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 RateController 冷启动超时从 60s 缩到 0.5s，避免样本不足时测试轮空等待。

    模块的 mock refiner.refine 极快，通常无法凑够 3 个 LLM 样本触发正常完成，
    所有测试都会走超时 fallback — 默认 60s 会让 test suite 变龟速。
    """
    monkeypatch.setattr(RateController, "COLD_START_TIMEOUT_S", 0.5)


def _build_pipeline() -> Pipeline:
    """带 mock OCR 引擎和 refiner 的 Pipeline（禁用 gap fill / PII）。"""
    cfg = PipelineConfig(
        llm=LLMConfig(
            model="test-model",
            enable_gap_fill=False,
            enable_final_refine=False,
        ),
        pii=PIIConfig(enable=False),
    )
    pipeline = Pipeline(cfg)

    # OCR：每张图返回包含文件名的极简 PageOCR
    mock_engine = MagicMock()

    async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
        return PageOCR(
            image_path=image_path,
            image_size=(100, 100),
            raw_text=f"正文 {image_path.name}",
            cleaned_text=f"正文 {image_path.name}",
        )

    mock_engine.ocr = AsyncMock(side_effect=_ocr)
    mock_engine.shutdown = AsyncMock(return_value=None)
    pipeline.set_ocr_engine(mock_engine)

    # refiner：refine 原样返回，detect_doc_boundaries 返回空（单文档）
    mock_refiner = MagicMock()

    async def _refine(text: str, _ctx: object) -> object:
        return MagicMock(
            markdown=text, gaps=[], truncated=False,
        )

    mock_refiner.refine = AsyncMock(side_effect=_refine)
    mock_refiner.final_refine = AsyncMock(
        side_effect=lambda md: MagicMock(
            markdown=md, gaps=[], truncated=False,
        ),
    )
    mock_refiner.detect_doc_boundaries = AsyncMock(return_value=[])
    mock_refiner.detect_pii_entities = AsyncMock(
        return_value=([], []),
    )
    mock_refiner.fill_gap = AsyncMock(return_value="")
    pipeline.set_refiner(mock_refiner)

    return pipeline


def _make_image_dir(root: Path, name: str, files: list[str]) -> Path:
    """在 root 下创建子目录 name，写入若干假图片文件。"""
    sub = root / name
    sub.mkdir(parents=True, exist_ok=True)
    for f in files:
        (sub / f).write_bytes(b"fake")
    return sub


class TestProcessTreeSingleLeaf:
    """输入目录本身就是叶子（含图片）→ 等价于 process_many"""

    @pytest.mark.asyncio
    async def test_single_leaf_returns_flat_results(
        self, tmp_path: Path,
    ) -> None:
        pipeline = _build_pipeline()

        image_dir = tmp_path / "imgs"
        image_dir.mkdir()
        (image_dir / "a.jpg").write_bytes(b"fake")
        (image_dir / "b.jpg").write_bytes(b"fake")

        output_dir = tmp_path / "out"
        results = await pipeline.process_tree(image_dir, output_dir)

        assert len(results) == 1
        # 单文档时 doc_dir 为空（直接写到 output_dir）
        assert results[0].doc_dir == ""
        assert results[0].output_path.exists()


class TestProcessTreeMultiSubdir:
    """根目录只含子目录，每个子目录独立处理"""

    @pytest.mark.asyncio
    async def test_two_subdirs_produce_two_results(
        self, tmp_path: Path,
    ) -> None:
        pipeline = _build_pipeline()

        root = tmp_path / "root"
        root.mkdir()
        _make_image_dir(root, "doc1", ["a.jpg", "b.jpg"])
        _make_image_dir(root, "doc2", ["c.jpg"])

        output_dir = tmp_path / "out"
        results = await pipeline.process_tree(root, output_dir)

        assert len(results) == 2
        # 每个结果 doc_dir 指向对应子目录
        dirs = sorted(r.doc_dir for r in results)
        assert dirs == ["doc1", "doc2"]
        # 每篇文档文件都真实落盘
        for r in results:
            assert r.output_path.exists()
            assert r.output_path.parent.name in {"doc1", "doc2"}

    @pytest.mark.asyncio
    async def test_progress_wrapped_with_subdir_label(
        self, tmp_path: Path,
    ) -> None:
        """进度回调的 message 应被包装上子目录标签。"""
        pipeline = _build_pipeline()

        root = tmp_path / "root"
        root.mkdir()
        _make_image_dir(root, "chapter_a", ["p1.jpg"])
        _make_image_dir(root, "chapter_b", ["p1.jpg"])

        progress_messages: list[str] = []

        def _on_progress(p: TaskProgress) -> None:
            progress_messages.append(p.message)

        output_dir = tmp_path / "out"
        await pipeline.process_tree(
            root, output_dir, on_progress=_on_progress,
        )

        # 至少包含两个子目录的标签
        assert any("chapter_a" in m for m in progress_messages)
        assert any("chapter_b" in m for m in progress_messages)
        # 消息格式形如 "[1/2 chapter_a] ..."
        assert any(
            m.startswith("[1/2 ") or m.startswith("[2/2 ")
            for m in progress_messages
        )

    @pytest.mark.asyncio
    async def test_nested_subdirs_discovered_as_leaves(
        self, tmp_path: Path,
    ) -> None:
        """嵌套：root/category/chapter/*.jpg 仍应被发现为叶子。"""
        pipeline = _build_pipeline()

        root = tmp_path / "root"
        root.mkdir()
        leaf = root / "categoryA" / "chapter1"
        leaf.mkdir(parents=True)
        (leaf / "a.jpg").write_bytes(b"fake")

        output_dir = tmp_path / "out"
        results = await pipeline.process_tree(root, output_dir)

        assert len(results) == 1
        # doc_dir 保留嵌套相对路径
        assert Path(results[0].doc_dir) == Path("categoryA/chapter1")

    @pytest.mark.asyncio
    async def test_empty_root_raises(
        self, tmp_path: Path,
    ) -> None:
        """根目录下既无图片又无叶子子目录 → FileNotFoundError。"""
        pipeline = _build_pipeline()
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            await pipeline.process_tree(empty, tmp_path / "out")


class TestProcessTreePartialFailure:
    """某个子目录失败不拖垮其他子目录（2026-04-21）

    注意：process_tree 的多子目录路径是 "最大子目录 warmup + rest 并行"，
    warmup leaf 失败会让 RateController 冷启动超时（60s）— 这是另一个
    话题。此处测试让失败子目录成为 rest 之一，覆盖"并行分支某个子目录
    失败，其他正常完成"这个主要使用场景。
    """

    @pytest.mark.asyncio
    async def test_one_subdir_fails_others_succeed(
        self, tmp_path: Path,
    ) -> None:
        pipeline = _build_pipeline()

        # 重写 OCR 逻辑：子目录名含 "bad" 时抛异常，其他正常
        mock_engine = MagicMock()

        async def _ocr(image_path: Path, _out_dir: Path) -> PageOCR:
            if image_path.parent.name == "bad":
                raise RuntimeError("ocr broke on this subdir")
            return PageOCR(
                image_path=image_path,
                image_size=(100, 100),
                raw_text=f"正文 {image_path.name}",
                cleaned_text=f"正文 {image_path.name}",
            )

        mock_engine.ocr = AsyncMock(side_effect=_ocr)
        mock_engine.shutdown = AsyncMock(return_value=None)
        pipeline.set_ocr_engine(mock_engine)

        root = tmp_path / "root"
        root.mkdir()
        # warmup 选页数最多的子目录：good1 用 3 张图保证成为 warmup，
        # bad / good2 作为 rest 并行，bad 的 OCR 抛异常不影响 good2
        _make_image_dir(root, "good1", ["a.jpg", "b.jpg", "c.jpg"])
        _make_image_dir(root, "bad", ["p.jpg"])
        _make_image_dir(root, "good2", ["q.jpg"])

        results = await pipeline.process_tree(root, tmp_path / "out")

        assert len(results) == 3
        by_dir = {r.doc_dir: r for r in results}

        # 失败子目录 error 非空、markdown 空
        assert "bad" in by_dir
        assert by_dir["bad"].error != ""
        assert "ocr broke" in by_dir["bad"].error
        assert by_dir["bad"].markdown == ""

        # 成功的 good1 / good2 正常产出
        assert by_dir["good1"].error == ""
        assert by_dir["good1"].markdown != ""
        assert by_dir["good2"].error == ""
        assert by_dir["good2"].markdown != ""


@pytest.mark.skip(
    reason="流式 Pipeline 停用 DOC_BOUNDARY 聚合（streaming-pipeline §10）；"
    "下一版代码照片还原恢复后再启用",
)
class TestProcessTreeDocTitleDir:
    """多子目录 + 子目录内多文档 → doc_dir 叠加子目录路径 + 标题"""

    @pytest.mark.asyncio
    async def test_doc_dir_prefix_with_subdir(
        self, tmp_path: Path,
    ) -> None:
        """单子目录下出现两篇文档时，doc_dir = subdir/<title>。"""
        # 构造一个 refiner 让它返回两个 doc boundary
        cfg = PipelineConfig(
            llm=LLMConfig(
                model="test-model",
                enable_gap_fill=False,
                enable_final_refine=False,
            ),
            pii=PIIConfig(enable=False),
        )
        pipeline = Pipeline(cfg)

        mock_engine = MagicMock()

        async def _ocr(image_path: Path, _out: Path) -> PageOCR:
            # 每页返回一个标题，让 renderer 能区分
            return PageOCR(
                image_path=image_path,
                image_size=(100, 100),
                raw_text=f"# 文档 {image_path.stem}\n正文",
                cleaned_text=f"# 文档 {image_path.stem}\n正文",
            )

        mock_engine.ocr = AsyncMock(side_effect=_ocr)
        mock_engine.shutdown = AsyncMock(return_value=None)
        pipeline.set_ocr_engine(mock_engine)

        mock_refiner = MagicMock()
        mock_refiner.refine = AsyncMock(
            side_effect=lambda text, _ctx: MagicMock(
                markdown=text, gaps=[], truncated=False,
            ),
        )
        mock_refiner.final_refine = AsyncMock(
            side_effect=lambda md: MagicMock(
                markdown=md, gaps=[], truncated=False,
            ),
        )
        # 在第一页之后切分为两篇
        from docrestore.models import DocBoundary
        mock_refiner.detect_doc_boundaries = AsyncMock(
            return_value=[
                DocBoundary(after_page="p1.jpg", new_title="第二篇"),
            ],
        )
        mock_refiner.detect_pii_entities = AsyncMock(
            return_value=([], []),
        )
        mock_refiner.fill_gap = AsyncMock(return_value="")
        pipeline.set_refiner(mock_refiner)

        root = tmp_path / "root"
        root.mkdir()
        _make_image_dir(root, "section", ["p1.jpg", "p2.jpg"])

        output_dir = tmp_path / "out"
        results = await pipeline.process_tree(root, output_dir)

        # 两个子文档，doc_dir 都以 "section/" 开头
        assert len(results) == 2
        for r in results:
            assert r.doc_dir.startswith("section")
            assert r.output_path.exists()
