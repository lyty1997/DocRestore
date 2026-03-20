<!--
Copyright 2026 @lyty1997

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# 输出层（output/）

## 1. 职责

将精修后的文档渲染为最终输出文件：汇总各页插图、更新图片引用路径、写入最终 markdown。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `output/renderer.py` | Markdown 渲染输出 |

## 3. 对外接口

### 3.1 Renderer（output/renderer.py）

```python
class Renderer:
    """将精修后的文档渲染为最终输出文件"""

    def __init__(self, config: OutputConfig) -> None: ...

    async def render(
        self, document: MergedDocument, output_dir: Path
    ) -> Path:
        """
        渲染流程：
        1. 扫描 markdown 中的图片引用 ![](DSC04654_OCR/images/0.jpg)
           （去重合并阶段已将引用重写为相对于 output_dir 的路径）
        2. 从各页 {stem}_OCR/images/ 复制插图到 output_dir/images/
           重命名为 {stem}_{region_index}.jpg 避免冲突
           例：DSC04654_OCR/images/0.jpg → images/DSC04654_0.jpg
        3. 同步更新 markdown 中的图片引用
           ![](DSC04654_OCR/images/0.jpg) → ![](images/DSC04654_0.jpg)
        4. 移除页边界标记 <!-- page: ... -->（最终输出不需要）
        5. 写入 output_dir/document.md
        6. 返回 document.md 的路径
        """
```

**调用约定**：
- 输入：`MergedDocument`（LLM 精修后的完整文档）+ 输出目录
- 输出：最终 `document.md` 的 `Path`
- 图片裁剪已在 OCR 阶段完成，本层只做汇总和路径重写
- 无需 `initialize()` / `shutdown()`

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `MergedDocument`, `Region` |
| `pipeline/config.py` | `OutputConfig` |

不依赖 OCR 层、处理层或 LLM 层。

## 5. 输出目录结构

```
{output_dir}/
├── document.md              # 最终 markdown
└── images/                  # 汇总的插图
    ├── DSC04654_0.jpg       # {image_stem}_{region_index}.jpg
    ├── DSC04654_1.jpg
    ├── DSC04657_0.jpg
    └── ...
```