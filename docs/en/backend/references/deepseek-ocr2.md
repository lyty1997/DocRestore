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

# DeepSeek-OCR-2 Deployment & Invocation Reference

Based on source code analysis of `third_party/DeepSeek-OCR-2/`.

## 1. Environment Requirements

```
Python 3.12.9
CUDA 11.8
PyTorch 2.6.0
vLLM 0.8.5 (prebuilt whl)
flash-attn 2.7.3
transformers 4.46.3
Model: deepseek-ai/DeepSeek-OCR-2 (HuggingFace)
```

Installation steps:
```bash
conda create -n deepseek-ocr2 python=3.12.9 -y
conda activate deepseek-ocr2
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
pip install vllm-0.8.5+cu118-cp38-abi3-manylinux1_x86_64.whl
pip install -r requirements.txt
pip install flash-attn==2.7.3 --no-build-isolation
```

## 2. Two Inference Modes

### 2.1 AsyncLLMEngine (Streaming, per-image processing)

Suitable for the docrestore scenario -- the engine stays resident on the GPU, processes images one by one via OCR, and intermediate results can be used for rolling merge.

```python
import os
os.environ['VLLM_USE_V1'] = '0'   # Must disable v1 pipeline
os.environ["CUDA_VISIBLE_DEVICES"] = '0'

from vllm import AsyncLLMEngine, SamplingParams
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.model_executor.models.registry import ModelRegistry
from deepseek_ocr2 import DeepseekOCR2ForCausalLM
from process.image_process import DeepseekOCR2Processor
from process.ngram_norepeat import NoRepeatNGramLogitsProcessor
from PIL import Image, ImageOps

# Register custom model
ModelRegistry.register_model("DeepseekOCR2ForCausalLM", DeepseekOCR2ForCausalLM)

# Create engine (GPU-resident)
engine = AsyncLLMEngine.from_engine_args(AsyncEngineArgs(
    model="deepseek-ai/DeepSeek-OCR-2",
    hf_overrides={"architectures": ["DeepseekOCR2ForCausalLM"]},
    dtype="bfloat16",
    max_model_len=8192,
    gpu_memory_utilization=0.75,
    trust_remote_code=True,
    tensor_parallel_size=1,
))

# Sampling parameters
logits_processors = [NoRepeatNGramLogitsProcessor(
    ngram_size=20, window_size=90, whitelist_token_ids={128821, 128822}
)]
sampling_params = SamplingParams(
    temperature=0.0,
    max_tokens=8192,
    logits_processors=logits_processors,
    skip_special_tokens=False,  # Must be False, otherwise grounding tags are stripped
)

# Image preprocessing (key: not passing PIL.Image directly, but a preprocessed packed structure)
image = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
image_features = DeepseekOCR2Processor().tokenize_with_images(
    images=[image], bos=True, eos=True, cropping=True
)

# Construct request
request = {
    "prompt": "<image>\n<|grounding|>Convert the document to markdown.",
    "multi_modal_data": {"image": image_features}
}

# Streaming generation
async for output in engine.generate(request, sampling_params, request_id):
    text = output.outputs[0].text
```

### 2.2 LLM.generate (Batch concurrent)

Suitable for processing multiple images at once with higher throughput, but not ideal for pipeline scenarios that need intermediate results.

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

# Multi-threaded preprocessing + batch inference
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

## 3. Key Configuration Parameters

| Parameter | Value | Description |
|---|---|---|
| `BASE_SIZE` | 1024 | Global view size (padded to square) |
| `IMAGE_SIZE` | 768 | Local tile size |
| `MIN_CROPS` | 2 | Minimum number of tiles |
| `MAX_CROPS` | 6 | Maximum number of tiles |
| `max_model_len` | 8192 | Maximum sequence length |
| `max_tokens` | 8192 | Maximum generated token count |
| `temperature` | 0.0 | Greedy decoding |
| `ngram_size` | 20 | Repetition suppression n-gram size |
| `window_size` | 50 (batch) / 90 (streaming) | Repetition suppression sliding window |
| `whitelist_token_ids` | {128821, 128822} | `<td>` `</td>` are not suppressed |

## 4. Image Preprocessing Pipeline

Processing steps of `DeepseekOCR2Processor.tokenize_with_images()`:

### 4.1 Global View
```
Original image → ImageOps.pad(image, (1024, 1024), color=mean_color) → ToTensor → Normalize(0.5, 0.5)
```
Produces 256 image tokens (`ceil(1024/16/4)^2 = 16^2 = 256`).

### 4.2 Local Tiles
- If the image is <= 768x768: no local cropping, `crop_ratio = [1, 1]`
- If the image is > 768x768:
  1. `count_tiles()` selects the optimal tile layout based on aspect ratio (choosing from 2-6 tiles)
  2. `dynamic_preprocess()` resizes the image to `(w_tiles x 768, h_tiles x 768)` then splits into 768-grid blocks
  3. Each tile produces 144 image tokens (`ceil(768/16/4)^2 = 12^2 = 144`)

### 4.3 Token Sequence Construction
```
[text_tokens] + [global: 256 image_tokens] + [1 separator] + [local: 144 x tile_count image_tokens] + [text_tokens]
```

### 4.4 Return Value Structure
```python
[[input_ids, pixel_values, images_crop, images_seq_mask, images_spatial_crop, num_image_tokens, image_shapes]]
```
- `input_ids`: LongTensor, complete sequence of text + image tokens
- `pixel_values`: Global view tensor [n_images, 3, 1024, 1024]
- `images_crop`: Local tile tensor [1, n_tiles, 3, 768, 768]
- `images_seq_mask`: Bool tensor marking which positions are image tokens
- `images_spatial_crop`: Tile layout [n_images, 2] (width tile count, height tile count)
- `num_image_tokens`: Total image token count per image
- `image_shapes`: List of original image dimensions

### 4.5 EXIF Orientation Correction
```python
image = ImageOps.exif_transpose(Image.open(path))  # Automatically corrects phone camera orientation
```

## 5. Prompt Format

Two modes:
```python
# With layout grounding -- output includes ref/det tags
PROMPT = '<image>\n<|grounding|>Convert the document to markdown.'

# Plain text OCR -- no positional information in output
PROMPT = '<image>\nFree OCR.'

# docrestore uses a combined mode (optimal approach determined in design.md)
PROMPT = '<image>\nFree OCR.\n<|grounding|>Convert the document to markdown.'
```

## 6. Grounding Tag Parsing

### 6.1 Output Format
```
Plain text content...
<|ref|>image<|/ref|><|det|>[[x1,y1,x2,y2]]<|/det|>
More text...
<|ref|>title<|/ref|><|det|>[[x1,y1,x2,y2]]<|/det|>
```

### 6.2 Parsing Regex
```python
pattern = r'(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>)'
matches = re.findall(pattern, text, re.DOTALL)
# matches[i] = (full_match, label, det_payload)
```

### 6.3 Coordinate Conversion
Coordinates are normalized to 0-999; conversion to pixel coordinates:
```python
x_px = int(x / 999 * image_width)
y_px = int(y / 999 * image_height)
```

### 6.4 Post-processing
- `label == 'image'`: Crop and save as image file, replace with `![](images/N.jpg)`
- Other labels (title, etc.): Remove the entire ref/det tag from the text
- Additional cleanup: `\\coloneqq` -> `:=`, `\\eqqcolon` -> `=:`, compress excess blank lines

## 7. NoRepeatNGramLogitsProcessor

A custom repetition suppression processor that differs from the standard transformers version:
- Only detects repetitions within a sliding window of the most recent `window_size` tokens (avoids overly aggressive global suppression)
- Supports a token whitelist (`<td>` `</td>` are allowed to repeat, preserving table structure)

```python
class NoRepeatNGramLogitsProcessor(LogitsProcessor):
    def __init__(self, ngram_size: int, window_size: int = 100, whitelist_token_ids: set = None):
        ...
    def __call__(self, input_ids: List[int], scores: torch.FloatTensor) -> torch.FloatTensor:
        # Find n-grams within input_ids[-window_size:] that share the same (ngram_size-1) prefix
        # Set the next token of those n-grams to -inf (except whitelisted tokens)
```

## 8. vLLM Adaptation Layer (deepseek_ocr2.py)

Core responsibilities:
1. Register `DeepseekOCR2ForCausalLM` with the vLLM ModelRegistry
2. Implement a multimodal processor that unpacks the structure from `tokenize_with_images()` into the fields required by vLLM
3. Handle `<image>` token expansion: 1 `<image>` -> N image_token_ids (count determined by tile layout)
4. Visual encoding: SAM-ViT -> Qwen2 Decoder-as-Encoder -> MLP Projector -> merge into language model embeddings

## 9. Integration Notes for DocRestore

1. **Hardcoded prompt issue**: `tokenize_with_images()` uses `config.PROMPT` internally and does not accept external input -- requires modification or monkey-patching
2. **`VLLM_USE_V1='0'`**: Must be set before importing vllm, otherwise the custom multimodal model is incompatible
3. **`skip_special_tokens=False`**: Must be set, otherwise grounding tags are stripped during decoding
4. **Coordinate parsing safety**: The reference code uses `eval()` to parse det coordinates, which poses an injection risk -- should be replaced with `ast.literal_eval()` or a custom parser
5. **Inference mode selection**: AsyncLLMEngine (streaming) is better suited for docrestore's per-image OCR + rolling merge scenario
6. **config.py global state**: `TOKENIZER` is initialized at import time, `PROMPT` is referenced in multiple places -- these global variables need to be decoupled during encapsulation
