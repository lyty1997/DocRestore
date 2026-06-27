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

"""export_to_cache 原子写测试：临时文件 + os.replace，并发下载不暴露半成品。

不依赖外部导出工具（pandoc/weasyprint/openpyxl），用桩导出器制造「先写半成品、
再写完整」的窗口。断言均从测试自造的输入/产物字节派生，不写死数据集标识。
"""

from __future__ import annotations

import threading
from pathlib import Path

from docrestore.output.exporters.base import (
    EXPORT_CACHE_DIRNAME,
    clear_export_caches,
    export_to_cache,
)


class _ProbeExporter:
    """桩导出器：写出时探测「最终 cache 路径是否已可见」+「写的是否非 cache」。"""

    suffix = "bin"
    tool = "stub"

    def __init__(self, complete: bytes, cache: Path) -> None:
        """记录期望的完整产物字节与最终 cache 路径，供 export 时断言。"""
        self._complete = complete
        self._cache = cache
        self.cache_visible_mid_write = True
        self.wrote_to_cache_directly = True

    def ensure_available(self) -> None:
        """桩：依赖恒可用。"""

    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None:  # noqa: ARG002
        """先写半成品再写完整内容；记录写入期间最终 cache 是否可见。"""
        out_path.write_bytes(b"PARTIAL")
        # 写入进行中：最终 cache 不应存在（半成品落在临时文件，不可见）
        self.cache_visible_mid_write = self._cache.exists()
        self.wrote_to_cache_directly = out_path == self._cache
        out_path.write_bytes(self._complete)


def _leftover_names(cache: Path) -> list[str]:
    """.exports 目录下除最终 cache 外的残留文件名（应为空=临时文件已清理）。"""
    return sorted(p.name for p in cache.parent.iterdir() if p != cache)


def test_export_to_cache_no_partial_visible(tmp_path: Path) -> None:
    """导出落位是原子的：写入期间最终 cache 不可见，结束后是完整产物、无临时残留。"""
    doc_dir = tmp_path / "out"
    doc_dir.mkdir()
    doc_md = doc_dir / "document.md"
    doc_md.write_text("# t\n", encoding="utf-8")
    cache = doc_dir / EXPORT_CACHE_DIRNAME / "deadbeef.bin"
    complete = b"COMPLETE-PRODUCT-" + b"X" * 64
    exporter = _ProbeExporter(complete, cache)

    export_to_cache(exporter, doc_md, doc_dir / "images", cache)

    # 写入期间最终 cache 不可见（写的是临时文件，非 cache 本身）
    assert exporter.cache_visible_mid_write is False
    assert exporter.wrote_to_cache_directly is False
    # 结束后 cache 是完整产物
    assert cache.read_bytes() == complete
    # 临时文件已清理，.exports 下只剩最终 cache
    assert _leftover_names(cache) == []


class _BarrierExporter:
    """桩导出器：两线程在 barrier 处会合后同时写，制造并发写竞态。"""

    suffix = "bin"
    tool = "stub"

    def __init__(self, complete: bytes, barrier: threading.Barrier) -> None:
        """记录完整产物字节与同步栅栏。"""
        self._complete = complete
        self._barrier = barrier

    def ensure_available(self) -> None:
        """桩：依赖恒可用。"""

    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None:  # noqa: ARG002
        """会合后写出完整产物到（各自的）临时文件。"""
        self._barrier.wait(timeout=5.0)
        out_path.write_bytes(self._complete)


def test_export_to_cache_concurrent_both_complete(tmp_path: Path) -> None:
    """两并发导出命中同一 cache：最后写者胜且仍是完整产物，无临时残留。"""
    doc_dir = tmp_path / "out"
    doc_dir.mkdir()
    doc_md = doc_dir / "document.md"
    doc_md.write_text("# t\n", encoding="utf-8")
    cache = doc_dir / EXPORT_CACHE_DIRNAME / "cafef00d.bin"
    complete = b"Z" * 4096
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            export_to_cache(
                _BarrierExporter(complete, barrier), doc_md, doc_dir / "images", cache,
            )
        except BaseException as exc:  # noqa: BLE001 — 收集线程内异常供主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # 最后写者胜：cache 仍是完整产物（绝不交错/截断）
    assert cache.read_bytes() == complete
    assert _leftover_names(cache) == []


def test_clear_export_caches_removes_nested_only(tmp_path: Path) -> None:
    """#3：清空 root 子树下所有 .exports/（单 doc + 多 doc 子目录），非缓存内容保留。"""
    # 根级 .exports（单 doc 任务）
    (tmp_path / EXPORT_CACHE_DIRNAME).mkdir()
    (tmp_path / EXPORT_CACHE_DIRNAME / "a.docx").write_bytes(b"x")
    # 子目录 .exports（多 doc 任务）
    sub = tmp_path / "doc1"
    (sub / EXPORT_CACHE_DIRNAME).mkdir(parents=True)
    (sub / EXPORT_CACHE_DIRNAME / "b.pdf").write_bytes(b"y")
    # 非缓存内容（不应被删）
    (tmp_path / "document.md").write_text("# t\n", encoding="utf-8")
    (sub / "images").mkdir()

    removed = clear_export_caches(tmp_path)

    assert removed == 2
    assert not (tmp_path / EXPORT_CACHE_DIRNAME).exists()
    assert not (sub / EXPORT_CACHE_DIRNAME).exists()
    # 产物与图片等非缓存内容原样保留
    assert (tmp_path / "document.md").is_file()
    assert (sub / "images").is_dir()


def test_clear_export_caches_no_caches_is_noop(tmp_path: Path) -> None:
    """无任何 .exports/ 时返回 0、不报错（首次运行/已清空场景）。"""
    (tmp_path / "document.md").write_text("# t\n", encoding="utf-8")
    assert clear_export_caches(tmp_path) == 0
