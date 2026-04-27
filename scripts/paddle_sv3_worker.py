#!/usr/bin/env python3
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

"""PaddleSV3 worker — PicoDet 主区域检测 + PP-StructureV3 light 流水线。

设计目标：
  - 替代 paddle_ocr_worker.py（PaddleOCRVL 慢/重）
  - 与现有 paddle_ocr_worker 完全兼容的 JSON Lines 协议
    (initialize / ocr / shutdown) → PaddleOCREngine 无需改动

流程（每张图）：
  1. （可选）PicoDet-S 检测"主文档区域" → 拿最高 score 的 bbox
  2. （可选）按 bbox 裁剪原图 → 写入 ocr_dir/{stem}_main.jpg
  3. PP-StructureV3 light 在裁剪图（或原图）上做 OCR → markdown
  4. 整理输出（imgs/ → images/，*.md → result.mmd，过滤小图标）

优雅降级：
  - main_doc_model_dir 未提供或路径不存在 → 跳过 detect/crop，直接对原图跑 sv3-light
    （此时表现退化为"sv3-light 直跑原图"，质量随训练模型成熟度而提升）

通信协议（与 paddle_ocr_worker.py 完全一致，便于 PaddleOCREngine 复用）：
  请求:
    {"cmd": "initialize",
     "main_doc_model_dir": "...",          # 可选；为空时跳过 detect
     "main_doc_score_threshold": 0.3,      # 可选
     "main_doc_pad_ratio": 0.01,           # 可选；裁剪边距 = 短边 × 该比例
     "ocr_det_max_side": 1600}             # 可选；sv3-light text_det_limit_side_len
    {"cmd": "ocr", "image_path": "...", "output_dir": "...",
     "min_image_size": 0}                   # 与原 worker 一致
    {"cmd": "shutdown"}
  响应（与原 worker 同字段，多 2 个新字段方便 debug）:
    {"ok": true,
     "raw_text": "<markdown>",
     "image_size": [W, H],                 # 原图尺寸（不是裁剪图）
     "image_count": N,
     "ocr_dir": "...",
     "coordinates": [...],                  # bbox 已平移回原图坐标系，
                                            # 与 image_size 同一参考系

     "main_region_bbox": [x1,y1,x2,y2]|null,
     "main_region_score": 0.xx}            # 0 表示无 detect
    {"ok": false, "error": "..."}

注意：所有日志输出到 stderr，stdout 专用于 JSON 协议通信。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# 禁用 PaddleOCR 的模型源连接检查，加速启动
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def _send(data: dict[str, object]) -> None:
    """向 stdout 写一行 JSON。"""
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _recv() -> dict[str, object] | None:
    """从 stdin 读一行 JSON，EOF 时返回 None。"""
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)  # type: ignore[no-any-return]


class Worker:
    """PaddleSV3 worker 主类。"""

    def __init__(self) -> None:
        from typing import Any

        self._pipeline: Any = None             # PPStructureV3
        self._detector: Any = None             # PicoDet 主区域检测器（可选）
        self._predict_kwargs: dict[str, object] = {}
        self._score_threshold: float = 0.3
        self._pad_ratio: float = 0.01

    # ── 初始化 ─────────────────────────────────────────────

    def handle_initialize(
        self,
        main_doc_model_dir: str = "",
        main_doc_model_name: str = "PicoDet-S",
        main_doc_score_threshold: float = 0.3,
        main_doc_pad_ratio: float = 0.01,
        ocr_det_max_side: int = 1600,
    ) -> dict[str, object]:
        """初始化 PP-StructureV3 light + 可选 PicoDet 主区域检测器。"""
        try:
            from paddleocr import PPStructureV3  # type: ignore[import-not-found]

            self._pipeline = PPStructureV3(
                use_table_recognition=False,
                use_formula_recognition=False,
                use_chart_recognition=False,
                use_seal_recognition=False,
                use_region_detection=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
            )
            self._predict_kwargs = {
                "text_det_limit_side_len": int(ocr_det_max_side),
                "text_det_limit_type": "max",
            }
            print(
                "PP-StructureV3 light 初始化完成 "
                f"(text_det_limit_side_len={ocr_det_max_side})",
                file=sys.stderr,
            )

            self._score_threshold = float(main_doc_score_threshold)
            self._pad_ratio = float(main_doc_pad_ratio)

            md_dir = (main_doc_model_dir or "").strip()
            if md_dir and Path(md_dir).exists():
                from paddlex import create_model  # type: ignore[import-not-found]

                self._detector = create_model(
                    model_name=main_doc_model_name, model_dir=md_dir,
                )
                print(
                    f"主区域检测器已加载: {md_dir} "
                    f"(model={main_doc_model_name}, "
                    f"score_threshold={self._score_threshold}, "
                    f"pad_ratio={self._pad_ratio})",
                    file=sys.stderr,
                )
            else:
                if md_dir:
                    print(
                        f"⚠ 主区域检测器目录不存在: {md_dir}，跳过 detect 阶段",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "未配置主区域检测器，直接对原图跑 sv3-light",
                        file=sys.stderr,
                    )
                self._detector = None
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── 主区域检测 ─────────────────────────────────────────

    def _detect_main_region(
        self, img_path: Path,
    ) -> tuple[tuple[int, int, int, int] | None, float]:
        """返回 (bbox, score)。bbox=None 表示无检测或低于门槛。"""
        if self._detector is None:
            return (None, 0.0)
        try:
            iter_out = self._detector.predict(str(img_path), batch_size=1)
            res = next(iter(iter_out))
        except Exception as exc:
            print(f"主区域检测失败: {exc}", file=sys.stderr)
            return (None, 0.0)

        data = res.json if hasattr(res, "json") else res
        if isinstance(data, dict) and "res" in data:
            data = data["res"]
        boxes = (data or {}).get("boxes") or []
        if not boxes:
            return (None, 0.0)
        best = max(boxes, key=lambda b: float(b.get("score", 0.0)))
        score = float(best.get("score", 0.0))
        if score < self._score_threshold:
            return (None, score)
        coord = best.get("coordinate") or best.get("bbox")
        if not coord or len(coord) != 4:
            return (None, score)
        bbox = (
            int(coord[0]), int(coord[1]),
            int(coord[2]), int(coord[3]),
        )
        return (bbox, score)

    # ── OCR 主流程 ────────────────────────────────────────

    def handle_ocr(
        self,
        image_path: str,
        output_dir: str,
        min_image_size: int = 0,
    ) -> dict[str, object]:
        """单张 OCR：detect → crop → sv3-light → 整理输出。"""
        if self._pipeline is None:
            return {"ok": False, "error": "引擎未初始化"}

        img_path = Path(image_path)
        out_dir = Path(output_dir)
        stem = img_path.stem
        ocr_dir = out_dir / f"{stem}_OCR"
        ocr_dir.mkdir(parents=True, exist_ok=True)

        try:
            from PIL import Image

            # ── 1) 主区域检测
            bbox, score = self._detect_main_region(img_path)

            # ── 2) 裁剪（如果检测有效）
            input_for_ocr = img_path
            # 裁剪偏移量；OCR 跑在裁剪图上时，需要把坐标平移回原图坐标系，
            # 否则下游 PaddleOCREngine._normalize_coordinates 用 image_size=原图尺寸
            # 去归一化裁剪图坐标，会把侧栏检测压到错误的位置。
            crop_offset = (0, 0)
            with Image.open(img_path) as im:
                W, H = im.size
                full_image_size = (W, H)
                if bbox is not None:
                    pad = int(min(W, H) * self._pad_ratio)
                    x1 = max(0, bbox[0] - pad)
                    y1 = max(0, bbox[1] - pad)
                    x2 = min(W, bbox[2] + pad)
                    y2 = min(H, bbox[3] + pad)
                    crop_path = ocr_dir / f"{stem}_main.jpg"
                    cropped = im.crop((x1, y1, x2, y2))
                    # RGBA / palette / LA 等模式 PIL 不能直接存 JPEG，
                    # 截图工具产物常见 RGBA PNG，必须先转 RGB。
                    if cropped.mode != "RGB":
                        cropped = cropped.convert("RGB")
                    cropped.save(crop_path, "JPEG", quality=92)
                    input_for_ocr = crop_path
                    crop_offset = (x1, y1)

            # ── 3) PP-StructureV3 light
            output = self._pipeline.predict(
                str(input_for_ocr), **self._predict_kwargs,
            )
            raw_text = ""
            coordinates: list[dict[str, object]] = []
            for res in output:
                res.save_to_markdown(save_path=str(ocr_dir))
                if hasattr(res, "text"):
                    raw_text = str(res.text)
                if hasattr(res, "json"):
                    try:
                        d = res.json
                        if isinstance(d, dict) and "res" in d:
                            d = d["res"]
                        coordinates = self._extract_coordinates(d)
                        if crop_offset != (0, 0):
                            coordinates = self._shift_coordinates(
                                coordinates, crop_offset,
                            )
                    except Exception as e:
                        print(f"坐标提取失败: {e}", file=sys.stderr)

            # ── 4) 整理输出（与原 worker 兼容的目录结构）
            md_path, image_count = self._reorganize_output(
                ocr_dir, input_for_ocr.stem, min_image_size,
            )

            markdown_content = (
                md_path.read_text(encoding="utf-8")
                if md_path.exists()
                else raw_text
            )

            self._clear_gpu_cache()

            return {
                "ok": True,
                "raw_text": markdown_content,
                "image_size": list(full_image_size),
                "image_count": image_count,
                "ocr_dir": str(ocr_dir),
                "coordinates": coordinates,
                "main_region_bbox": list(bbox) if bbox else None,
                "main_region_score": score,
            }
        except Exception as exc:
            print(f"OCR 失败: {exc}", file=sys.stderr)
            return {"ok": False, "error": str(exc)}

    # ── shutdown ──────────────────────────────────────────

    def handle_shutdown(self) -> dict[str, object]:
        if self._pipeline is not None and hasattr(self._pipeline, "close"):
            try:
                self._pipeline.close()
            except Exception:
                pass
        self._pipeline = None
        self._detector = None
        return {"ok": True}

    # ── 工具方法（与 paddle_ocr_worker 保持一致以确保协议兼容） ──

    @staticmethod
    def _clear_gpu_cache() -> None:
        import gc

        gc.collect()
        try:
            import torch  # type: ignore[import-not-found]

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        # PaddlePaddle 自身的显存缓存
        try:
            import paddle  # type: ignore[import-not-found]

            if paddle.is_compiled_with_cuda():
                paddle.device.cuda.empty_cache()
        except Exception:
            pass

    @staticmethod
    def _extract_coordinates(
        json_data: dict[str, object],
    ) -> list[dict[str, object]]:
        """从 sv3 res.json 拿 parsing_res_list（同原 worker 协议）。"""
        coordinates: list[dict[str, object]] = []
        parsing = json_data.get("parsing_res_list")
        if not isinstance(parsing, list):
            return coordinates
        for item in parsing:
            if not isinstance(item, dict):
                continue
            label = str(item.get("block_label", "text"))
            bbox_data = item.get("block_bbox")
            if not bbox_data:
                continue
            bbox = Worker._parse_bbox(bbox_data)
            if not bbox:
                continue
            coordinates.append({
                "label": label,
                "bbox": bbox,
                "text": str(item.get("block_content", "")),
            })
        return coordinates

    @staticmethod
    def _shift_coordinates(
        coordinates: list[dict[str, object]],
        offset: tuple[int, int],
    ) -> list[dict[str, object]]:
        """把裁剪图坐标平移回原图坐标系（image_size 字段始终是原图尺寸）。"""
        dx, dy = offset
        shifted: list[dict[str, object]] = []
        for item in coordinates:
            bbox = item.get("bbox")
            if (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(isinstance(v, int) for v in bbox)
            ):
                new_item = dict(item)
                new_item["bbox"] = [
                    bbox[0] + dx, bbox[1] + dy,
                    bbox[2] + dx, bbox[3] + dy,
                ]
                shifted.append(new_item)
            else:
                shifted.append(item)
        return shifted

    @staticmethod
    def _parse_bbox(bbox_data: object) -> list[int] | None:
        if not isinstance(bbox_data, list):
            return None
        if (
            len(bbox_data) == 4
            and all(isinstance(x, (int, float)) for x in bbox_data)
        ):
            return [
                int(bbox_data[0]), int(bbox_data[1]),
                int(bbox_data[2]), int(bbox_data[3]),
            ]
        if all(isinstance(p, list) and len(p) == 2 for p in bbox_data):
            xs = [int(p[0]) for p in bbox_data]
            ys = [int(p[1]) for p in bbox_data]
            return [min(xs), min(ys), max(xs), max(ys)]
        return None

    @staticmethod
    def _reorganize_output(
        ocr_dir: Path,
        stem: str,
        min_image_size: int = 0,
    ) -> tuple[Path, int]:
        """与 paddle_ocr_worker._reorganize_output 同协议。

        sv3 输出: {ocr_dir}/{stem}.md + {ocr_dir}/imgs/*.jpg
        本项目协议: {ocr_dir}/result.mmd + {ocr_dir}/images/*.jpg
        """
        src_md = ocr_dir / f"{stem}.md"
        dst_md = ocr_dir / "result.mmd"
        if src_md.exists() and src_md != dst_md:
            src_md.rename(dst_md)

        src_imgs = ocr_dir / "imgs"
        dst_imgs = ocr_dir / "images"
        image_count = 0

        if src_imgs.exists():
            if dst_imgs.exists():
                shutil.rmtree(dst_imgs)
            src_imgs.rename(dst_imgs)
            img_files, removed_names, old_to_new = (
                Worker._filter_and_rename_images(dst_imgs, min_image_size)
            )
            image_count = len(img_files)
            if dst_md.exists():
                md_text = dst_md.read_text(encoding="utf-8")
                md_text = Worker._update_image_refs_in_markdown(
                    md_text, removed_names, old_to_new,
                )
                dst_md.write_text(md_text, encoding="utf-8")
        elif dst_md.exists():
            md_text = dst_md.read_text(encoding="utf-8")
            md_text = md_text.replace("imgs/", "images/")
            dst_md.write_text(md_text, encoding="utf-8")
            dst_imgs.mkdir(exist_ok=True)

        return dst_md, image_count

    @staticmethod
    def _filter_and_rename_images(
        images_dir: Path, min_image_size: int,
    ) -> tuple[list[Path], set[str], dict[str, str]]:
        all_files = sorted(
            p for p in images_dir.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
        kept: list[Path] = []
        removed: set[str] = set()
        for f in all_files:
            if min_image_size > 0 and Worker._is_image_too_small(
                f, min_image_size,
            ):
                print(f"过滤小图标: {f.name}", file=sys.stderr)
                removed.add(f.name)
                f.unlink()
                continue
            kept.append(f)
        old_to_new: dict[str, str] = {}
        for idx, f in enumerate(kept):
            new_name = f"{idx}.jpg"
            old_to_new[f.name] = new_name
            if f.name != new_name:
                f.rename(images_dir / new_name)
        return kept, removed, old_to_new

    @staticmethod
    def _update_image_refs_in_markdown(
        md_text: str,
        removed_names: set[str],
        old_to_new: dict[str, str],
    ) -> str:
        import re

        md_text = md_text.replace("imgs/", "images/")
        for name in removed_names:
            md_text = Worker._remove_image_ref(md_text, f"imgs/{name}")
            md_text = Worker._remove_image_ref(md_text, f"images/{name}")
        for old_name, new_name in old_to_new.items():
            md_text = md_text.replace(
                f"images/{old_name}", f"images/{new_name}",
            )
        return md_text

    @staticmethod
    def _is_image_too_small(img_path: Path, min_size: int) -> bool:
        try:
            from PIL import Image

            with Image.open(img_path) as im:
                w, h = im.size
                return w < min_size or h < min_size
        except Exception:
            return False

    @staticmethod
    def _remove_image_ref(md_text: str, image_ref: str) -> str:
        import re

        escaped = re.escape(image_ref)
        md_text = re.sub(
            r"\n*<div[^>]*><img[^>]*"
            + escaped
            + r"[^>]*/>\s*</div>\s*\n*",
            "\n",
            md_text,
        )
        return re.sub(
            r"\n*!\[[^\]]*\]\(" + escaped + r"\)\s*\n*",
            "\n",
            md_text,
        )


def main() -> None:
    worker = Worker()
    while True:
        request = _recv()
        if request is None:
            break
        cmd = request.get("cmd", "")
        if cmd == "initialize":
            _send(worker.handle_initialize(
                main_doc_model_dir=str(
                    request.get("main_doc_model_dir", ""),
                ),
                main_doc_model_name=str(
                    request.get("main_doc_model_name", "PicoDet-S"),
                ),
                main_doc_score_threshold=float(
                    request.get("main_doc_score_threshold", 0.3),
                ),
                main_doc_pad_ratio=float(
                    request.get("main_doc_pad_ratio", 0.01),
                ),
                ocr_det_max_side=int(
                    request.get("ocr_det_max_side", 1600),
                ),
            ))
        elif cmd == "ocr":
            min_img_raw = request.get("min_image_size", 0)
            min_img = (
                int(min_img_raw)
                if isinstance(min_img_raw, (int, float))
                else 0
            )
            _send(worker.handle_ocr(
                str(request.get("image_path", "")),
                str(request.get("output_dir", "")),
                min_image_size=min_img,
            ))
        elif cmd == "shutdown":
            _send(worker.handle_shutdown())
            break
        else:
            _send({"ok": False, "error": f"未知命令: {cmd}"})


if __name__ == "__main__":
    main()
