# mypy: ignore-errors
"""把 16-bit TIFF（.tif/.jpg 壳）转 8-bit 归一化 PNG，长边默认 1280。

Claude 多模态读原始 16-bit TIFF 尺寸过大/位深不支持，这里只做预览压缩。
不做任何算法上的 ROI/增强，只做线性归一化 + 下采样。

用法：
    python scripts/compress_for_preview.py              # 默认 test_images/batch5
    python scripts/compress_for_preview.py <dir>        # 指定目录
    python scripts/compress_for_preview.py <file>       # 指定单张
    LONG_EDGE=1536 python scripts/compress_for_preview.py ...
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.transform import resize as sk_resize

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "test_images" / "batch5"
OUT_DIR = PROJECT_ROOT / "outputs" / "preview"
IMG_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
LONG_EDGE = int(os.environ.get("LONG_EDGE", "1280"))


def _to_gray_u8(raw: np.ndarray) -> np.ndarray:
    """支持 16-bit / 8-bit / RGB；线性归一化到 uint8。"""
    src = raw[..., :3].mean(axis=2) if raw.ndim == 3 else raw
    arr = np.asarray(src, dtype=np.float32)
    lo = float(arr.min())
    hi = float(arr.max())
    if hi - lo < 1e-6:
        return np.zeros(arr.shape, dtype=np.uint8)
    norm = (arr - lo) / (hi - lo)
    return np.asarray((norm * 255.0).clip(0, 255), dtype=np.uint8)


def _downsample(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape
    scale = long_edge / max(h, w)
    if scale >= 1.0:
        return img
    new_h, new_w = int(round(h * scale)), int(round(w * scale))
    small = sk_resize(img, (new_h, new_w), anti_aliasing=True, preserve_range=True)
    clipped = np.clip(small, 0, 255)
    return np.asarray(clipped, dtype=np.uint8)


def _collect_inputs(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.suffix.lower() in IMG_EXTS)
    raise FileNotFoundError(f"输入路径不存在：{target}")


def process(src: Path, out_dir: Path) -> Path:
    raw = np.array(Image.open(src))
    gray = _to_gray_u8(raw)
    small = _downsample(gray, LONG_EDGE)
    out_path = out_dir / f"{src.stem}.png"
    Image.fromarray(small).save(out_path, optimize=True)
    print(
        f"  {src.name}: {raw.shape} {raw.dtype} → {small.shape} u8 "
        f"({out_path.stat().st_size / 1024:.0f} KB)"
    )
    return out_path


def main() -> None:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1]).resolve()
    elif os.environ.get("SPIKE_INPUT"):
        target = Path(os.environ["SPIKE_INPUT"]).resolve()
    else:
        target = DEFAULT_INPUT
    inputs = _collect_inputs(target)
    print(f"输入：{target}  ({len(inputs)} 张, long_edge={LONG_EDGE})")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in inputs:
        process(p, OUT_DIR)
    print(f"\n产物 → {OUT_DIR.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
