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

# OCR 层（ocr/）

## 1. 职责

将文档照片转换为含 grounding 标签的 markdown 文本，同时裁剪插图区域。模型常驻 GPU，支持连续处理多张照片。

## 2. 文件清单

| 文件 | 职责 |
|---|---|
| `ocr/base.py` | `OCREngine` Protocol 定义 |
| `ocr/deepseek_ocr2.py` | DeepSeek-OCR-2 实现 |
| `ocr/preprocessor.py` | 图片预处理（动态分辨率 + tile 切分） |
| `ocr/ngram_filter.py` | NoRepeatNGram 循环抑制 |

## 3. 对外接口

### 3.1 OCREngine Protocol（ocr/base.py）

其他模块（Pipeline）通过此接口调用 OCR 层。

```python
class OCREngine(Protocol):
    async def initialize(self) -> None:
        """加载模型到 GPU"""
        ...

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        """单张 OCR，结果写入 output_dir/{image_stem}_OCR/，返回 PageOCR"""
        ...

    async def ocr_batch(
        self,
        image_paths: list[Path],
        output_dir: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[PageOCR]:
        """逐张调用 ocr()，每完成一张回调 on_progress(current, total)"""
        ...

    async def shutdown(self) -> None:
        """释放 GPU 资源"""
        ...

    @property
    def is_ready(self) -> bool: ...
```

**调用约定**：
- 必须先 `initialize()` 再调用 `ocr()` / `ocr_batch()`
- `output_dir` 由 Pipeline 传入，`ocr()` 在其下创建 `{image_stem}_OCR/` 子目录
- `ocr()` 返回的 `PageOCR.raw_text` 含 grounding 标签，`cleaned_text` 为空
- `ocr()` 内部完成 grounding 解析 + 图片裁剪，结果写入 `PageOCR.output_dir`
- `ocr_batch()` 逐张调用，不做批量推理（需要中间结果做滚动合并）

## 4. 依赖的接口

| 来源 | 使用 |
|---|---|
| `models.py` | `PageOCR`, `Region` |
| `pipeline/config.py` | `OCRConfig` |

不依赖其他处理层模块。

## 5. 内部实现

### 5.1 DeepSeekOCR2Engine（ocr/deepseek_ocr2.py）

```python
class DeepSeekOCR2Engine:
    def __init__(self, config: OCRConfig) -> None:
        self._config = config
        self._engine: AsyncLLMEngine | None = None
        self._sampling_params: SamplingParams | None = None

    async def initialize(self) -> None:
        """
        1. 设置 VLLM_USE_V1=0（必须在 import vllm 之前）
        2. 注册模型到 vLLM
        3. 创建 AsyncLLMEngine
        4. 创建 SamplingParams + NoRepeatNGram logits processor
        5. 创建 ImagePreprocessor
        """

    async def ocr(self, image_path: Path, output_dir: Path) -> PageOCR:
        """
        完整 OCR 流程：
        1. 创建 output_dir/{image_stem}_OCR/ 子目录
        2. load_image() + EXIF 修正
        3. preprocessor.preprocess() → vLLM engine.generate()
        4. 检查 eos token → has_eos
        5. 保存 result_ori.mmd（原始输出含 grounding 标签）
        6. re_match() 解析 grounding → 裁剪 image 区域保存到 images/
        7. 替换标签为 ![](images/N.jpg)，删除非 image 标签
        8. 保存 result.mmd + result_with_boxes.jpg
        9. 构造 PageOCR 返回（output_dir 指向 {output_dir}/{image_stem}_OCR/）
        """

    async def shutdown(self) -> None:
        """关闭 AsyncLLMEngine，释放 GPU"""
```

**关键注意事项**：
- `VLLM_USE_V1='0'` 必须在 `import vllm` 之前设置，通过延迟导入解决
- `tokenize_with_images()` 硬编码使用 `config.PROMPT`，需 fork 该函数接受 prompt 参数
- 无 eos 的输出标记为 `has_eos=False`，清洗层特殊处理

### 5.2 ImagePreprocessor（ocr/preprocessor.py）

```python
class ImagePreprocessor:
    """从 third_party 提取并封装，去除全局变量依赖"""

    def __init__(
        self,
        base_size: int = 1024,
        crop_size: int = 768,
        min_crops: int = 2,
        max_crops: int = 6,
    ) -> None: ...

    def load_image(self, image_path: Path) -> Image.Image:
        """加载 + EXIF 修正"""

    def preprocess(self, image: Image.Image, prompt: str) -> dict:
        """
        全局视图 pad → 动态裁切 tiles → 构造 image token 序列
        → 返回 vLLM multi_modal_data
        """
```

- prompt 作为参数传入，不再从全局 `config.PROMPT` 读取
- tokenizer 在 `__init__` 时加载一次，复用

### 5.3 NoRepeatNGramLogitsProcessor（ocr/ngram_filter.py）

```python
class NoRepeatNGramLogitsProcessor(LogitsProcessor):
    """滑动窗口 ngram 循环抑制"""

    def __init__(
        self,
        ngram_size: int,
        window_size: int = 100,
        whitelist_token_ids: set[int] | None = None,
    ) -> None: ...

    def __call__(
        self, input_ids: list[int], scores: torch.FloatTensor
    ) -> torch.FloatTensor: ...
```

直接复用 `third_party` 的实现，补全类型注解。

## 6. 输出目录结构

每张照片产出独立目录：

```
{task_output}/{image_stem}_OCR/
├── result_ori.mmd          # 原始输出（含 grounding 标签）
├── result.mmd              # grounding 已解析、图片已裁剪替换的 markdown
├── result_with_boxes.jpg   # 布局可视化
└── images/                 # 裁剪的插图（0.jpg, 1.jpg, ...）
```