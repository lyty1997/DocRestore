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

# DeepSeek-OCR-2 部署与调用参考

基于 `third_party/DeepSeek-OCR-2/` 源码分析整理。

## 1. 环境要求

```
Python 3.12.9
CUDA 11.8
PyTorch 2.6.0
vLLM 0.8.5（预编译 whl）
flash-attn 2.7.3
transformers 4.46.3
模型：deepseek-ai/DeepSeek-OCR-2（HuggingFace）
```

安装步骤：
```bash
conda create -n deepseek-ocr2 python=3.12.9 -y
conda activate deepseek-ocr2
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
pip install vllm-0.8.5+cu118-cp38-abi3-manylinux1_x86_64.whl
pip install -r requirements.txt
pip install flash-attn==2.7.3 --no-build-isolation
```

## 2. 两种推理方式

### 2.1 AsyncLLMEngine（流式，逐张处理）

适合 docrestore 的场景——引擎常驻 GPU，逐张 OCR，可拿到中间结果做滚动合并。

```python
import os
os.environ['VLLM_USE_V1'] = '0'   # 必须关闭 v1 pipeline
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

from vllm import AsyncLLMEngine, SamplingParams
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.model_executor.models.registry import ModelRegistry
from deepseek_ocr2 import DeepseekOCR2ForCausalLM
from process.image_process import DeepseekOCR2Processor
from process.ngram_norepeat import NoRepeatNGramLogitsProcessor
from PIL import Image, ImageOps

# 注册自定义模型
ModelRegistry.register_model("DeepseekOCR2ForCausalLM", DeepseekOCR2ForCausalLM)

# 创建引擎（常驻 GPU）
engine = AsyncLLMEngine.from_engine_args(AsyncEngineArgs(
    model="deepseek-ai/DeepSeek-OCR-2",
    hf_overrides={"architectures": ["DeepseekOCR2ForCausalLM"]},
    dtype="bfloat16",
    max_model_len=8192,
    gpu_memory_utilization=0.75,
    trust_remote_code=True,
    tensor_parallel_size=1,
))

# 采样参数
logits_processors = [NoRepeatNGramLogitsProcessor(
    ngram_size=20, window_size=90, whitelist_token_ids={128821, 128822}
)]
sampling_params = SamplingParams(
    temperature=0.0,
    max_tokens=8192,
    logits_processors=logits_processors,
    skip_special_tokens=False,  # 必须 False，否则 grounding 标签被吞
)

# 图像预处理（关键：不是直接传 PIL.Image，而是传预处理后的打包结构）
image = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
image_features = DeepseekOCR2Processor().tokenize_with_images(
    images=[image], bos=True, eos=True, cropping=True
)

# 构造请求
request = {
    "prompt": "<image>\n<|grounding|>Convert the document to markdown.",
    "multi_modal_data": {"image": image_features}
}

# 流式生成
async for output in engine.generate(request, sampling_params, request_id):
    text = output.outputs[0].text
```

### 2.2 LLM.generate（批量并发）

适合一次性处理多张图片，吞吐更高，但不适合需要中间结果的流水线场景。

```python
from vllm import LLM

llm = LLM(
    model="deepseek-ai/DeepSeek-OCR-2",
    hf_overrides={"architectures": ["DeepseekOCR2ForCausalLM"]},
    max_model_len=8192,
    max_num_seqs=100,
    gpu_memory_utilization=0.9,
    trust_remote_code=True,
    block_size=256,
    swap_space=0,
    disable_mm_preprocessor_cache=True,
)

# 多线程预处理 + 批量推理
batch_inputs = [
    {
        "prompt": PROMPT,
        "multi_modal_data": {"image": DeepseekOCR2Processor().tokenize_with_images(
            images=[img], bos=True, eos=True, cropping=True
        )}
    }
    for img in images
]
outputs_list = llm.generate(batch_inputs, sampling_params=sampling_params)
```

## 3. 关键配置参数

| 参数 | 值 | 说明 |
|---|---|---|
| `BASE_SIZE` | 1024 | 全局视图尺寸（pad 到正方形） |
| `IMAGE_SIZE` | 768 | 局部 tile 尺寸 |
| `MIN_CROPS` | 2 | 最少 tile 数 |
| `MAX_CROPS` | 6 | 最多 tile 数 |
| `max_model_len` | 8192 | 最大序列长度 |
| `max_tokens` | 8192 | 最大生成 token 数 |
| `temperature` | 0.0 | 贪心解码 |
| `ngram_size` | 20 | 循环抑制 ngram 大小 |
| `window_size` | 50（批量）/ 90（流式） | 循环抑制滑动窗口 |
| `whitelist_token_ids` | {128821, 128822} | `<td>` `</td>` 不被禁止重复 |

## 4. 图像预处理流程

`DeepseekOCR2Processor.tokenize_with_images()` 的处理步骤：

### 4.1 全局视图
```
原图 → ImageOps.pad(image, (1024, 1024), color=mean_color) → ToTensor → Normalize(0.5, 0.5)
```
产出 256 个 image token（`ceil(1024/16/4)² = 16² = 256`）。

### 4.2 局部 tile
- 如果图片 ≤ 768x768：不做局部裁切，`crop_ratio = [1, 1]`
- 如果图片 > 768x768：
  1. `count_tiles()` 根据宽高比选最佳 tile 布局（从 2~6 个 tile 中选）
  2. `dynamic_preprocess()` 将图片 resize 到 `(w_tiles×768, h_tiles×768)` 后按 768 网格切块
  3. 每个 tile 产出 144 个 image token（`ceil(768/16/4)² = 12² = 144`）

### 4.3 token 序列构造
```
[text_tokens] + [global: 256 个 image_token] + [1 个分隔符] + [local: 144 × tile_count 个 image_token] + [text_tokens]
```

### 4.4 返回值结构
```python
[[input_ids, pixel_values, images_crop, images_seq_mask, images_spatial_crop, num_image_tokens, image_shapes]]
```
- `input_ids`: LongTensor，文本 + image token 的完整序列
- `pixel_values`: 全局视图 tensor [n_images, 3, 1024, 1024]
- `images_crop`: 局部 tile tensor [1, n_tiles, 3, 768, 768]
- `images_seq_mask`: bool tensor，标记哪些位置是 image token
- `images_spatial_crop`: tile 布局 [n_images, 2]（宽 tile 数, 高 tile 数）
- `num_image_tokens`: 每张图的 image token 总数
- `image_shapes`: 原图尺寸列表

### 4.5 EXIF 方向修正
```python
image = ImageOps.exif_transpose(Image.open(path))  # 自动修正手机拍摄方向
```

## 5. Prompt 格式

两种模式：
```python
# 带布局定位（grounding）——输出包含 ref/det 标签
PROMPT = '<image>\n<|grounding|>Convert the document to markdown.'

# 纯文本 OCR——不输出定位信息
PROMPT = '<image>\nFree OCR.'

# docrestore 使用组合模式（design.md 中确定的最佳方案）
PROMPT = '<image>\nFree OCR.\n<|grounding|>Convert the document to markdown.'
```

## 6. grounding 标签解析

### 6.1 输出格式
```
普通文本内容...
<|ref|>image<|/ref|><|det|>[[x1,y1,x2,y2]]<|/det|>
更多文本...
<|ref|>title<|/ref|><|det|>[[x1,y1,x2,y2]]<|/det|>
```

### 6.2 解析正则
```python
pattern = r'(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>)'
matches = re.findall(pattern, text, re.DOTALL)
# matches[i] = (完整匹配, label, det_payload)
```

### 6.3 坐标转换
坐标归一化到 0~999，转像素坐标：
```python
x_px = int(x / 999 * image_width)
y_px = int(y / 999 * image_height)
```

### 6.4 后处理
- `label == 'image'`：裁剪保存为图片文件，替换为 `![](images/N.jpg)`
- 其他 label（title 等）：从文本中删除整个 ref/det 标签
- 额外清理：`\\coloneqq` → `:=`，`\\eqqcolon` → `=:`，多余空行压缩

## 7. NoRepeatNGramLogitsProcessor

自定义的循环抑制处理器，与 transformers 标准版的区别：
- 只在最近 `window_size` 个 token 的滑动窗口内检测重复（避免全局禁止过强）
- 支持白名单 token（`<td>` `</td>` 允许重复，保护表格结构）

```python
class NoRepeatNGramLogitsProcessor(LogitsProcessor):
    def __init__(self, ngram_size: int, window_size: int = 100, whitelist_token_ids: set = None):
        ...
    def __call__(self, input_ids: List[int], scores: torch.FloatTensor) -> torch.FloatTensor:
        # 在 input_ids[-window_size:] 范围内找与当前 (ngram_size-1) 前缀相同的 ngram
        # 将这些 ngram 的 next token 设为 -inf（白名单除外）
```

## 8. vLLM 适配层（deepseek_ocr2.py）

核心职责：
1. 注册 `DeepseekOCR2ForCausalLM` 到 vLLM ModelRegistry
2. 实现 multimodal processor，将 `tokenize_with_images()` 的打包结构拆解为 vLLM 需要的字段
3. 处理 `<image>` token 展开：1 个 `<image>` → N 个 image_token_id（数量由 tile 布局决定）
4. 视觉编码：SAM-ViT → Qwen2 Decoder-as-Encoder → MLP Projector → merge 到语言模型 embedding

## 9. docrestore 接入注意事项

1. **prompt 硬编码问题**：`tokenize_with_images()` 内部使用 `config.PROMPT`，不接受外部传入——需要修改或 monkey-patch
2. **`VLLM_USE_V1='0'`**：必须在 import vllm 之前设置，否则自定义 multimodal 模型不兼容
3. **`skip_special_tokens=False`**：必须设置，否则 grounding 标签在解码时被吞掉
4. **坐标解析安全**：参考代码用 `eval()` 解析 det 坐标，有注入风险，应改用 `ast.literal_eval()` 或自定义 parser
5. **推理方式选择**：AsyncLLMEngine（流式）更适合 docrestore 的逐张 OCR + 滚动合并场景
6. **config.py 全局状态**：`TOKENIZER` 在 import 时就初始化，`PROMPT` 被多处引用——封装时需要解耦这些全局变量