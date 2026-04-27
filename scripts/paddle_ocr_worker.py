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

"""PaddleOCR worker — 在 PaddleOCR conda 环境中运行

通信协议（JSON Lines over stdin/stdout）：
  请求: {"cmd": "initialize"}
      | {"cmd": "ocr", "image_path": "...", "output_dir": "..."}
      | {"cmd": "shutdown"}
  响应: {"ok": true, ...}
      | {"ok": false, "error": "..."}

注意：所有日志输出到 stderr，stdout 专用于 JSON 协议通信。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# 禁用 PaddleOCR 的模型源连接检查，加速启动
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"


def _send(data: dict[str, object], *, seq: object = None) -> None:
    """向 stdout 写一行 JSON；seq 非空时注入 `seq` 字段以便主进程对齐响应。"""
    if seq is not None and "seq" not in data:
        data = {**data, "seq": seq}
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _recv() -> dict[str, object] | None:
    """从 stdin 读一行 JSON，EOF 时返回 None。"""
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)  # type: ignore[no-any-return]


class Worker:
    """PaddleOCR worker 主类。"""

    def __init__(self) -> None:
        # pipeline 类型在外部 conda 环境中定义，主项目 mypy 无法解析
        from typing import Any

        self._pipeline: Any = None

    def handle_initialize(
        self,
        server_url: str = "",
        server_model_name: str = "",
        pipeline: str = "vl",
    ) -> dict[str, object]:
        """初始化 PaddleOCR pipeline。

        Args:
            server_url: vllm-server URL（vl pipeline 启用 server 模式）
            server_model_name: server 端模型名称（vl 用）
            pipeline: ``vl``（PaddleOCR-VL，文档默认）或 ``basic``
                （PP-OCRv5 行级 bbox，IDE 代码场景）
        """
        try:
            self._pipeline_kind = pipeline
            if pipeline == "basic":
                # PP-OCRv5 基础 pipeline：DBNet text_det + CRNN text_rec
                # 输出行级 rec_boxes + texts + scores（填充 PageOCR.text_lines）
                # 不需要 vllm-server。参数借鉴 MinerU 的调优值。
                from paddleocr import PaddleOCR

                self._pipeline = PaddleOCR(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    text_det_box_thresh=0.3,
                    text_det_unclip_ratio=1.8,
                )
                print(
                    "PaddleOCR(basic) 初始化完成",
                    file=sys.stderr,
                )
                return {"ok": True}

            from paddleocr import PaddleOCRVL

            if server_url:
                self._pipeline = PaddleOCRVL(
                    vl_rec_backend="vllm-server",
                    vl_rec_server_url=server_url,
                    vl_rec_api_model_name=server_model_name,
                )
                print(
                    f"PaddleOCRVL 初始化完成（server 模式: {server_url}）",
                    file=sys.stderr,
                )
            else:
                self._pipeline = PaddleOCRVL()
                print(
                    "PaddleOCRVL 初始化完成（本地模式）",
                    file=sys.stderr,
                )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def handle_ocr(
        self,
        image_path: str,
        output_dir: str,
        min_image_size: int = 0,
    ) -> dict[str, object]:
        """对单张图片执行 OCR。

        1. 调用 pipeline.predict()
        2. save_to_markdown() 保存到临时目录
        3. 提取坐标信息（res.json()）
        4. 整理输出目录结构（重命名为 OCREngine 约定格式）
        5. 返回元数据 + 坐标

        Args:
            min_image_size: 过滤宽或高小于此值的小图标（px），0 禁用
        """
        if self._pipeline is None:
            return {"ok": False, "error": "引擎未初始化"}

        img_path = Path(image_path)
        out_dir = Path(output_dir)
        stem = img_path.stem

        # 目标目录：{output_dir}/{stem}_OCR/
        ocr_dir = out_dir / f"{stem}_OCR"
        ocr_dir.mkdir(parents=True, exist_ok=True)

        try:
            pipeline_kind = getattr(self, "_pipeline_kind", "vl")

            # PaddleOCR(VL).predict() 返回结果迭代器
            output = self._pipeline.predict(str(img_path))

            raw_text = ""
            coordinates: list[dict[str, object]] = []
            text_lines: list[dict[str, object]] = []

            for res in output:
                if pipeline_kind == "basic":
                    # PP-OCRv5 行级输出：res.json 含 rec_boxes + rec_texts +
                    # rec_scores 一一对应。无 markdown，写空 raw_text 由主进程
                    # 自行处理。
                    text_lines = self._extract_text_lines(res)
                    continue

                # vl 分支：写 markdown + 提取 layout 块级 coords（保留旧路径）
                res.save_to_markdown(save_path=str(ocr_dir))
                if hasattr(res, "text"):
                    raw_text = str(res.text)
                if hasattr(res, "json"):
                    try:
                        json_data = res.json
                        if "res" in json_data:
                            coordinates = self._extract_coordinates(
                                json_data["res"]
                            )
                        else:
                            coordinates = self._extract_coordinates(json_data)
                    except Exception as e:
                        print(f"坐标提取失败: {e}", file=sys.stderr)

            # vl: 整理 markdown + 图片；basic: 跳过（无 markdown 产物）
            if pipeline_kind == "basic":
                # 与 vl 分支统一用 "result.mmd"：paddle_ocr.py 的 OCR 缓存
                # 检查 (`if result_mmd.exists()`) 和 cleaner 的读取路径都靠
                # 这个固定文件名。曾经误写成 `{stem}.md` 导致 cache 永远 miss
                # + cleaner 刷 "result.mmd 不存在，回退使用 raw_text" 警告。
                md_path = ocr_dir / "result.mmd"
                image_count = 0
                # basic 模式 raw_text 用 lines 的 text 顺序拼接，给 OCR cache /
                # 旧路径兼容（PageOCR.raw_text 仍非空）
                raw_text = "\n".join(
                    str(ln.get("text", "")) for ln in text_lines
                )
                md_path.write_text(raw_text, encoding="utf-8")
                # 同时把行级数据 dump 出来供缓存命中时重建 PageOCR.text_lines
                lines_path = ocr_dir / "text_lines.jsonl"
                lines_path.write_text(
                    "\n".join(
                        json.dumps(ln, ensure_ascii=False)
                        for ln in text_lines
                    ),
                    encoding="utf-8",
                )
                markdown_content = raw_text
            else:
                md_path, image_count = self._reorganize_output(
                    ocr_dir, stem, min_image_size,
                )
                markdown_content = (
                    md_path.read_text(encoding="utf-8")
                    if md_path.exists() else raw_text
                )

            # 获取图片尺寸
            image_size = self._get_image_size(img_path)

            # 清理 GPU 显存（防止 KV cache 累积）
            self._clear_gpu_cache()

            return {
                "ok": True,
                "raw_text": markdown_content,
                "image_size": list(image_size),
                "image_count": image_count,
                "ocr_dir": str(ocr_dir),
                "coordinates": coordinates,
                "text_lines": text_lines,
            }
        except Exception as exc:
            print(
                f"OCR 失败: {exc}", file=sys.stderr
            )
            return {"ok": False, "error": str(exc)}

    def handle_shutdown(self) -> dict[str, object]:
        """关闭 pipeline。"""
        if self._pipeline is not None:
            if hasattr(self._pipeline, "close"):
                self._pipeline.close()
            self._pipeline = None
        return {"ok": True}

    @staticmethod
    def _clear_gpu_cache() -> None:
        """清理 GPU 显存缓存，防止 KV cache 累积。"""
        import gc

        gc.collect()

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                print("GPU 缓存已清理", file=sys.stderr)
        except ImportError:
            pass

    @staticmethod
    def _extract_from_layout(
        layout_data: object,
    ) -> list[dict[str, object]]:
        """从 layout_res 提取坐标信息。

        返回格式：[{"label": str, "bbox": [x1, y1, x2, y2], "text": str}, ...]
        """
        coordinates: list[dict[str, object]] = []

        if not isinstance(layout_data, list):
            return coordinates

        for item in layout_data:
            if not isinstance(item, dict):
                continue

            label = str(item.get("block_label", "text"))
            bbox_data = item.get("block_bbox")
            if not bbox_data:
                continue

            bbox = Worker._parse_bbox(bbox_data)
            if not bbox:
                continue

            text = str(item.get("block_content", ""))

            coordinates.append({
                "label": label,
                "bbox": bbox,
                "text": text,
            })

        return coordinates

    @staticmethod
    def _extract_text_lines(res: object) -> list[dict[str, object]]:
        """从 PP-OCRv5 basic 结果对象提取行级 (bbox, text, score)。

        ``res.json`` 结构：``{"res": {"rec_boxes": [[x1,y1,x2,y2], ...],
        "rec_texts": [...], "rec_scores": [...]}}``。
        """
        out: list[dict[str, object]] = []
        try:
            data = res.json  # type: ignore[attr-defined]
        except Exception:
            return out
        if not isinstance(data, dict):
            return out
        inner = data.get("res", data)
        if not isinstance(inner, dict):
            return out
        rec_boxes = inner.get("rec_boxes") or []
        rec_texts = inner.get("rec_texts") or []
        rec_scores = inner.get("rec_scores") or []
        if not isinstance(rec_boxes, list):
            return out
        for i, box in enumerate(rec_boxes):
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            try:
                x1, y1, x2, y2 = (int(v) for v in box[:4])
            except (TypeError, ValueError):
                continue
            text = (
                str(rec_texts[i])
                if i < len(rec_texts) and rec_texts[i] is not None
                else ""
            )
            score = (
                float(rec_scores[i])
                if i < len(rec_scores) and rec_scores[i] is not None
                else 0.0
            )
            out.append({
                "bbox": [x1, y1, x2, y2],
                "text": text,
                "score": score,
            })
        return out

    @staticmethod
    def _extract_coordinates(
        json_data: dict[str, object],
    ) -> list[dict[str, object]]:
        """从 PaddleOCR JSON 数据提取坐标信息。

        返回格式：[{"label": str, "bbox": [x1, y1, x2, y2], "text": str}, ...]
        坐标为像素坐标，需要在主进程中归一化。
        """
        coordinates: list[dict[str, object]] = []

        # PaddleOCR 输出在 parsing_res_list 中
        parsing_res = json_data.get("parsing_res_list")
        if not isinstance(parsing_res, list):
            return coordinates

        for item in parsing_res:
            if not isinstance(item, dict):
                continue

            label = str(item.get("block_label", "text"))
            bbox_data = item.get("block_bbox")
            if not bbox_data:
                continue

            bbox = Worker._parse_bbox(bbox_data)
            if not bbox:
                continue

            text = str(item.get("block_content", ""))

            coordinates.append({
                "label": label,
                "bbox": bbox,
                "text": text,
            })

        return coordinates

    @staticmethod
    def _parse_bbox(
        bbox_data: object,
    ) -> list[int] | None:
        """解析 bbox 数据为 [x1, y1, x2, y2] 格式。"""
        if not isinstance(bbox_data, list):
            return None

        # rect 格式：[x1, y1, x2, y2]
        if (
            len(bbox_data) == 4
            and all(isinstance(x, (int, float)) for x in bbox_data)
        ):
            return [int(bbox_data[0]), int(bbox_data[1]),
                    int(bbox_data[2]), int(bbox_data[3])]

        # quad/poly 格式：[[x,y], ...]
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
        """整理 PaddleOCR 输出为 OCREngine 约定格式。

        PaddleOCR 输出：{ocr_dir}/{stem}.md + {ocr_dir}/imgs/*.jpg
        OCREngine 约定：{ocr_dir}/result.mmd + {ocr_dir}/images/*.jpg

        Args:
            min_image_size: 过滤宽或高小于此值的小图标（px），0 禁用

        返回 (result.mmd 路径, 图片数量)。
        """
        # 重命名 markdown 文件
        src_md = ocr_dir / f"{stem}.md"
        dst_md = ocr_dir / "result.mmd"
        if src_md.exists() and src_md != dst_md:
            src_md.rename(dst_md)

        # 重命名图片目录 imgs/ → images/
        src_imgs = ocr_dir / "imgs"
        dst_imgs = ocr_dir / "images"
        image_count = 0

        if src_imgs.exists():
            if dst_imgs.exists():
                shutil.rmtree(dst_imgs)
            src_imgs.rename(dst_imgs)

            # 过滤小图标 + 重命名
            img_files, removed_names, old_to_new = (
                Worker._filter_and_rename_images(
                    dst_imgs, min_image_size,
                )
            )
            image_count = len(img_files)

            # 更新 markdown 中的图片引用
            if dst_md.exists():
                md_text = dst_md.read_text(encoding="utf-8")
                md_text = Worker._update_image_refs_in_markdown(
                    md_text, removed_names, old_to_new,
                )
                dst_md.write_text(md_text, encoding="utf-8")

        elif dst_md.exists():
            # 没有图片目录，只清理 markdown 中的引用路径
            md_text = dst_md.read_text(encoding="utf-8")
            md_text = md_text.replace("imgs/", "images/")
            dst_md.write_text(md_text, encoding="utf-8")
            dst_imgs.mkdir(exist_ok=True)

        return dst_md, image_count

    @staticmethod
    def _filter_and_rename_images(
        images_dir: Path,
        min_image_size: int,
    ) -> tuple[list[Path], set[str], dict[str, str]]:
        """过滤小图标并重命名为 0.jpg, 1.jpg, ...

        Returns:
            (保留的图片列表, 被移除的原始文件名集合, 旧名→新名映射)
        """
        all_img_files = sorted(
            p
            for p in images_dir.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
        img_files: list[Path] = []
        removed_names: set[str] = set()
        for img_file in all_img_files:
            if min_image_size > 0 and Worker._is_image_too_small(
                img_file, min_image_size,
            ):
                print(
                    f"过滤小图标: {img_file.name}",
                    file=sys.stderr,
                )
                removed_names.add(img_file.name)
                img_file.unlink()
                continue
            img_files.append(img_file)

        old_to_new: dict[str, str] = {}
        for idx, img_file in enumerate(img_files):
            new_name = f"{idx}.jpg"
            old_to_new[img_file.name] = new_name
            if img_file.name != new_name:
                img_file.rename(images_dir / new_name)

        return img_files, removed_names, old_to_new

    @staticmethod
    def _update_image_refs_in_markdown(
        md_text: str,
        removed_names: set[str],
        old_to_new: dict[str, str],
    ) -> str:
        """更新 markdown 中的图片引用：替换路径 + 移除被过滤的引用。"""
        # 替换 imgs/ → images/ 的引用
        md_text = md_text.replace("imgs/", "images/")
        # 移除被过滤图片的整行引用（含外层 div）
        for name in removed_names:
            md_text = Worker._remove_image_ref(
                md_text, f"imgs/{name}",
            )
            md_text = Worker._remove_image_ref(
                md_text, f"images/{name}",
            )
        # 替换旧文件名为新文件名
        for old_name, new_name in old_to_new.items():
            md_text = md_text.replace(
                f"images/{old_name}",
                f"images/{new_name}",
            )
        return md_text

    @staticmethod
    def _is_image_too_small(
        img_path: Path, min_size: int,
    ) -> bool:
        """检查图片的宽或高是否小于 min_size。"""
        try:
            from PIL import Image

            with Image.open(img_path) as img:
                w, h = img.size
                return w < min_size or h < min_size
        except Exception:
            return False

    @staticmethod
    def _remove_image_ref(
        md_text: str, image_ref: str,
    ) -> str:
        """从 markdown 中移除包含指定图片引用的整行（含外层 div 包裹）。

        匹配两种格式：
        - <div ...><img src="image_ref" ...></div> （含前后空行）
        - ![...](image_ref) （含前后空行）
        """
        import re

        # HTML div 包裹格式
        escaped = re.escape(image_ref)
        md_text = re.sub(
            r"\n*<div[^>]*><img[^>]*"
            + escaped
            + r"[^>]*/>\s*</div>\s*\n*",
            "\n",
            md_text,
        )
        # markdown 格式
        return re.sub(
            r"\n*!\[[^\]]*\]\(" + escaped + r"\)\s*\n*",
            "\n",
            md_text,
        )

    @staticmethod
    def _get_image_size(
        image_path: Path,
    ) -> tuple[int, int]:
        """获取图片尺寸（不依赖 PIL，用最简方式）。"""
        try:
            from PIL import Image

            with Image.open(image_path) as img:
                size: tuple[int, int] = img.size
                return size
        except ImportError:
            # PIL 不可用时返回占位值
            return (0, 0)


def main() -> None:
    """Worker 主循环。"""
    worker = Worker()

    while True:
        request = _recv()
        if request is None:
            # stdin 关闭，退出
            break

        cmd = request.get("cmd", "")
        seq = request.get("seq")

        if cmd == "initialize":
            _send(worker.handle_initialize(
                server_url=str(request.get("server_url", "")),
                server_model_name=str(
                    request.get("server_model_name", "")
                ),
                pipeline=str(request.get("pipeline", "vl")),
            ), seq=seq)
        elif cmd == "ocr":
            min_img_raw = request.get("min_image_size", 0)
            min_img = (
                int(min_img_raw)
                if isinstance(min_img_raw, (int, float))
                else 0
            )
            _send(
                worker.handle_ocr(
                    str(request.get("image_path", "")),
                    str(request.get("output_dir", "")),
                    min_image_size=min_img,
                ),
                seq=seq,
            )
        elif cmd == "shutdown":
            _send(worker.handle_shutdown(), seq=seq)
            break
        else:
            _send(
                {
                    "ok": False,
                    "error": f"未知命令: {cmd}",
                },
                seq=seq,
            )


if __name__ == "__main__":
    main()
