# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""任务级排除图清单（OCRConfig.exclude_images）解析与扫描过滤单元测试。

排除只在任务扫描时生效，绝不删除 / 移动磁盘源文件；key 与裁剪框同空间
（相对根 image_dir 的路径）。
"""

from __future__ import annotations

from pathlib import Path

from docrestore.pipeline.config import OCRConfig
from docrestore.pipeline.pipeline import (
    ImageOverrides,
    resolve_crop_boxes,
    resolve_excluded_paths,
    scan_images,
)


class TestResolveExcludedPaths:
    """排除清单 → 绝对路径集合。"""

    def test_plain_and_subdir_keys(self) -> None:
        """普通文件名与子目录相对路径都解析为 image_dir 拼接。"""
        root = Path("/data/imgs")
        out = resolve_excluded_paths(root, ["a.jpg", "sub/b.png"])
        assert out == frozenset({root / "a.jpg", root / "sub/b.png"})

    def test_rejects_traversal_and_absolute(self) -> None:
        """``..`` 越界与绝对路径 key 被静默忽略（路径穿越防护）。"""
        root = Path("/data/imgs")
        out = resolve_excluded_paths(
            root, ["../secret.jpg", "/etc/passwd", "ok.jpg", "a/../b.jpg"],
        )
        assert out == frozenset({root / "ok.jpg"})

    def test_empty(self) -> None:
        assert resolve_excluded_paths(Path("/x"), []) == frozenset()


class TestResolveCropBoxes:
    """用户裁剪框清单 → 绝对路径键（净化规则与排除清单一致）。"""

    def test_plain_and_subdir_keys(self) -> None:
        root = Path("/data/imgs")
        out = resolve_crop_boxes(
            root,
            {"a.jpg": (1, 2, 3, 4), "sub/b.png": (5, 6, 7, 8)},
        )
        assert out == {
            root / "a.jpg": (1, 2, 3, 4),
            root / "sub/b.png": (5, 6, 7, 8),
        }

    def test_rejects_traversal_and_absolute(self) -> None:
        root = Path("/data/imgs")
        out = resolve_crop_boxes(
            root,
            {
                "../evil.jpg": (0, 0, 1, 1),
                "/etc/passwd": (0, 0, 1, 1),
                "ok.jpg": (1, 2, 3, 4),
            },
        )
        assert out == {root / "ok.jpg": (1, 2, 3, 4)}


class TestImageOverrides:
    """任务级覆盖（排除 + 用户框）从 OCR 配置一次解析。"""

    def test_resolve_from_ocr_config(self) -> None:
        root = Path("/data/imgs")
        ocr = OCRConfig(
            exclude_images=["drop.jpg"],
            crop_boxes={"keep.jpg": (10, 0, 90, 100)},
        )
        ov = ImageOverrides.resolve(root, ocr)
        assert ov.exclude == frozenset({root / "drop.jpg"})
        assert ov.crop_boxes == {root / "keep.jpg": (10, 0, 90, 100)}

    def test_default_config_is_empty(self) -> None:
        ov = ImageOverrides.resolve(Path("/x"), OCRConfig())
        assert not ov.exclude
        assert not ov.crop_boxes


class TestScanFilter:
    """scan_images 结果按排除集过滤（与 _scan_task_images 同构逻辑）。"""

    def test_filter_keeps_symlink_identity(self, tmp_path: Path) -> None:
        """软链源图按未 resolve 路径比对——排除 stage 目录里的软链生效。

        stage 目录的图是指向外部真实文件的软链；resolve 会落到目录外导致
        比对失败，故解析与扫描都必须用未 resolve 路径。
        """
        real = tmp_path / "real.jpg"
        real.write_bytes(b"x")
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "keep.jpg").write_bytes(b"x")
        (stage / "drop.jpg").symlink_to(real)

        exclude = resolve_excluded_paths(stage, ["drop.jpg"])
        remaining = [
            p for p in scan_images(stage) if p not in exclude
        ]
        assert [p.name for p in remaining] == ["keep.jpg"]

    def test_filter_subdir_keys_against_leaf_paths(
        self, tmp_path: Path,
    ) -> None:
        """多文档树：根相对 key（sub/x.jpg）与叶子扫描路径精确比对。"""
        sub = tmp_path / "docA"
        sub.mkdir()
        (sub / "p1.jpg").write_bytes(b"x")
        (sub / "p2.jpg").write_bytes(b"x")

        exclude = resolve_excluded_paths(tmp_path, ["docA/p1.jpg"])
        remaining = [p for p in scan_images(sub) if p not in exclude]
        assert [p.name for p in remaining] == ["p2.jpg"]
