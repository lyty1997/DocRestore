---
name: view-gel-image
description: 查看超大、16-bit 或非常规格式图片时使用，例如凝胶电泳胶图、显微镜 TIFF、扫描大图、伪装成 .jpg 的 TIFF，避免直接读取原图造成上下文或位深问题。
---

# View Gel Image

## 必用场景

- 用户要求查看 `.tif` / `.tiff` / 实为 TIFF 的 `.jpg`。
- PNG/JPG 大于 2 MB，或长边很大。
- 已知图片为 16-bit 或非 uint8 位深。
- 直接读取图片失败，提示文件过大或位深不支持。
- 需要批量查看图像质量、ROI、lane、band、扫描结果。

## 原则

不要直接读取原图。先把原图压缩为 uint8 PNG 预览图，再查看预览图。预览图只用于人工检查，不得作为算法输入。

## 命令

```bash
python3 .codex/skills/view-gel-image/scripts/compress_for_preview.py path/to/image.jpg
python3 .codex/skills/view-gel-image/scripts/compress_for_preview.py path/to/dir
LONG_EDGE=1536 python3 .codex/skills/view-gel-image/scripts/compress_for_preview.py path/to/dir
```

输出默认写入：

```text
outputs/preview/<stem>.png
```

## 工作流

1. 用户指定单张图片时只压缩该图。
2. 用户指定目录时批量压缩目录中的图像文件。
3. 优先查看已生成且比原图更新的预览图，避免重复压缩。
4. 一次最多查看少量预览图，避免上下文膨胀。
5. 如果预览太暗或看不清细节，调高 `LONG_EDGE` 或改用分位数归一化。

## 常见坑点

- `.jpg` 可能实际是 TIFF；用 PIL 按魔数读取，不要按扩展名假设。
- RGBA 图片要丢弃 alpha 后再取灰度，避免 alpha 污染。
- 全局 min-max 归一化可能被极亮离群值压暗；必要时改为 P1/P99 分位数。
- 压缩图只用于查看，不能用于 ROI 检测或其他算法流程。
