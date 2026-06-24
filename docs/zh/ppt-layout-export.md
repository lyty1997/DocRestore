# PPT 版面位置导出设计（Epic D · Phase-2b / 关联 #74）

> 目标：导出 pptx 时，把每个区域（标题/正文/表格/图片）**按原屏摄图的版面位置**摆放，
> 而非现在的「逐页竖向堆叠」。方案 A（按 bbox 摆 + 原始内容）+ 区域级精修增强。
>
> 状态：**设计待确认**（2026-06-23）。确认后再写代码。真相源：本文件 + [export-mode.md](export-mode.md) §9。

## 1. 背景：位置数据在 OCR 源头就有，只是被丢了

之前判「延后」的理由（per-region 文本不干净存在）**被证伪**。实测数据流：

- PaddleOCR-VL-1.6 的 `doc_parser` 原生输出 `parsing_res_list`，每个版面块带
  `{block_label, block_bbox, block_content}`，worker `_extract_coordinates`
  归整成 `coordinates: [{label, bbox:[x1,y1,x2,y2], text}]`（像素坐标、阅读序）
  （`scripts/paddle_ocr_worker.py:351-387`）。
- 主进程 `paddle_ocr.py:202` 收到 `coordinates`，**但只拿去做侧栏检测，用完即弃**
  （`paddle_ocr.py:205-216`）；`PageOCR.regions` 的 bbox 被硬编码成 `(0,0,0,0)`
  （`paddle_ocr.py:52`，只从 markdown 正则扒图片路径）。
- 落盘产物（`document.md` + `images/` + `.document.anchored.md`）**无任何坐标**；
  导出器只拿得到 `document.md` + `images/`。

**结论**：bbox 数据在内存里转瞬即逝。要用就得在 pipeline 里**接住并持久化成 sidecar**，
再喂给导出器。这是架构级改动（动 OCR 结果落盘链路），不再是 Phase-2a 那种纯下载时函数。

## 2. 方案：A（bbox + 原始内容）+ 区域级精修，破解「内容对不齐」

| 表示 | 来源 | 位置 | 内容 |
|---|---|---|---|
| `coordinates` | VL 原始 | ✅ bbox | ⚠️ 未精修（`block_content`） |
| `document.md` | 精修后 | ❌ 无 | ✅ 精修 / 表格干净 HTML |

二者无干净 1:1 映射（页级精修可能合并/拆分/重排块）。**B 方案**（精修整页再回对齐区域）
对齐脆。**采用**：把精修下沉到**区域粒度**——精修时 bbox 全程挂着，精修后内容天然 1:1
对应 bbox，绕开对齐难点（用户拍板）。

- **关精修**：区域内容 = VL `block_content` 原文（位置精确、内容 raw，PPT 多为短标题/标签，
  矫正后 raw 通常够用；表格仍走 §9.1 原生渲染）。
- **开精修**：区域内容 = 区域级精修结果（见 §4.2，**按 index 重挂 bbox，不信 LLM 回吐坐标**）。

不选 C（启发式版面）：用户要真位置，C 给不了。不选 B：对齐脆、收益不一定压过复杂度。

## 3. 数据流总览

```text
VL worker coordinates[{label,bbox,text}]  ──capture──▶  PageOCR.layout_regions
                                                              │
                  精修前注入 <!-- ppt-region bbox=... --> 头锚点（bbox 自包含）
                                                              │
                        整页 SLIDE_REFINE（约束+「保留锚点」）  ▼  开精修
                                                              │
              按锚点切分 → (bbox ↔ 精修后内容) 配对；锚点数不符则该页退 raw
                                                              ▼
              _ppt_pipeline 落 .ppt_layout.json（位置真相源）+ document.md 剥锚点
                                                              │
                  下载导出 pptx.py：sidecar 存在 ──▶ 按 bbox 定位渲染
                                  sidecar 缺失 ──────▶ 回退 §9.2 竖排（现状）
```

每一步**fail-safe 退化**：任一环缺失/异常 → 退回当前竖排，绝不报错、绝不丢内容。

## 4. 模块设计

### 4.1 捕获：`PageOCR.layout_regions`（新字段）

新增模型（`models.py`，与 `text_lines` 同范式——引擎可选、缺则空）：

```python
@dataclass
class LayoutRegion:
    """VL 版面块：bbox（原图像素）+ 类型 + 内容 + 裁图。PPT 版面定位用。"""
    bbox: tuple[int, int, int, int]   # (x1, y1, x2, y2) 图像像素（落在 image_size 内）
    label: str                         # paragraph_title/text/figure_title/table/image/chart
    content: str                       # 文字类=文字/HTML 表；image/chart=空串（见 image_ref）
    image_ref: str = ""                # image/chart 专用：认领到的 images/N.jpg（相对引用）
```

`PageOCR` 加 `layout_regions: list[LayoutRegion] = field(default_factory=list)`。

捕获点 `paddle_ocr.py:225` 前后：把已在手的 `coordinates_raw`（`[{label,bbox,text}]`）转成
`LayoutRegion`（**与侧栏检测解耦**，不受 `self._column_filter` 门控；非 VL 引擎留空）：
- 文字类（`paragraph_title/text/figure_title/table`）：`content = text`（spike 证实干净可用）。
- `image/chart`（`text=""`）：按**阅读序**认领 `raw_text` 第 k 个 `<img src="images/N.jpg">` →
  `image_ref`（§6 已验证可行）；裁图实际落 `{stem}_OCR/images/N.jpg`，渲染期解析为绝对路径。
`image_size` 是图像像素尺寸（bbox 坐标空间），原样带下去。

> 不动 `Region`/`_parse_image_refs`（图片引用链路保持），`layout_regions` 是**新增并行轨**，
> 只 PPT 定位导出消费；文档模式忽略它，零影响。

### 4.2 锚点法：精修前注入区域头锚点，精修后按锚点匹配（用户拍板）

**思路（用户）**：进 LLM 精修**之前**，把每个区域的 bbox 作为**头部锚点字段**写在该区域
内容前；精修照常整页跑（锚点随内容一起走）；输出 PPTX 时按锚点把 **bbox ↔ 精修后内容** 配对。

**为何优于「单独区域级 prompt」**：复用现有**整页**精修（1 次 LLM/页，非 N 次），靠显式锚点
保持「位置 ↔ 内容」关联，绕开「按阅读序对齐」的脆点（精修合并/拆分块也不丢关联）。

实现（spike 后定稿——在**干净的 coords 区域单元**上做，不碰 `raw_text` 的 div 包装）：
- **可精修单元**：只取**文字类**区域（`paragraph_title/text/figure_title/table`）的 `content`
  送精修；`image/chart`（content 空）不进精修，bbox + 裁图直接落 sidecar。
- **注入头锚点**：把一页的文字区域拼成一份带 idx 头的 payload（"bbox 存为头部字段"）：
  ```text
  <!-- r:0 -->
  REME案例：DHB生物合成
  <!-- r:1 -->
  <table>...</table>
  ```
  idx 旁的 bbox/label **由我方按 idx 留底**（不进 payload，省得 LLM 改坐标）。
- **精修**：沿用 `SLIDE_REFINE_SYSTEM_PROMPT`，加一条约束「逐字保留 `<!-- r:N -->` 行、按相同
  idx 原数返回，表格保持 HTML」。整页**一次** LLM 调用。
- **匹配**：输出按 `<!-- r:N -->` 切 → 精修文本按 **idx 重挂**对应 bbox（坐标全程是我方原值，
  **不信 LLM 回吐**）→ 写 `.ppt_layout.json`。
- **fail-safe**：输出 idx 集 ≠ 注入 idx 集（漏/并/拆）→ 该页文字区域**整页退 raw `content`**
  （位置仍准、只是未精修），绝不错位。
- **清理**：锚点仅 PPT 定位用，不进 `document.md`（document.md 仍走现有页级精修，§9 决策 3）。

> 复用 `_get_refiner` 开关、`LLMCache`（新 cache key）、PII 出云闸口（payload 送云端前
> `guard.redact_for_cloud`）。**比"在 raw_text 里塞锚点"更稳**：coords 区域天然是切好的单元，
> 切分不依赖 LLM 对自由文本中锚点的保真，只依赖它保留几行 `<!-- r:N -->` 标记。

### 4.3 持久化：`.ppt_layout.json` sidecar

`_ppt_pipeline` 组装阶段（`pipeline.py:1381` 前后）按 §4.2 匹配结果（关精修=raw 内容、
开精修=按锚点切出的精修内容）写一份位置真相源，落 `output_dir`：

```json
{
  "version": 1,
  "slide_size_emu": [12192000, 6858000],
  "pages": [
    {
      "filename": "page_A.jpg",
      "image_size": [1920, 1080],
      "regions": [
        {"bbox": [80, 60, 1840, 200], "label": "title",  "content": "标题文字"},
        {"bbox": [80, 240, 900, 1020], "label": "text",   "content": "正文..."},
        {"bbox": [960, 240, 1840, 1020], "label": "figure", "content": "![](images/page_A_1.jpg)"}
      ]
    }
  ]
}
```

- 只 PPT 模式产出；与 `document.md` 同目录、`.` 前缀（不进 asset 白名单、不裸打进 zip）。
- 含坐标信息但**不含原始 PII**（区域内容已过精修+脱敏链路，与 document.md 同口径）。
- 缺失 = 老任务 / 非 PPT / 捕获失败 → 导出器自动退竖排。

### 4.4 导出：`pptx.py` 定位渲染（sidecar 存在时）

`PptxExporter.export` 增加分支（**导出器自行**读 `doc_md.parent/".ppt_layout.json"`，
路由零改）：

```text
存在 .ppt_layout.json 且 schema 合法
  └─▶ 逐页一 slide：对每个 region，bbox(像素) → slide EMU（§5），按 label 渲染：
        title/text/formula → 文本框；table → §9.1 原生 pptx 表格；figure → 定位图片
缺失 / 非法 / 任一页异常
  └─▶ 退回 §9.2 现有竖排块流（保留为 fallback，零回归）
```

复用已建件：表格走 `html_table.py`，图片解析走 `_resolve_image`（路径穿越守卫不变）。

## 5. 坐标变换：像素 bbox → slide EMU

pptx **一份演示文稿只有一个 slide 尺寸**，而各页 `image_size` 可能不同。策略：

1. **画布**：以**首页**长宽比定 slide 画布——宽固定 `_SLIDE_W=12192000`（13.333in），
   高 = `round(_SLIDE_W * 首页h / 首页w)`，让画布形状贴近原屏摄图（版面更忠实）。
2. **每页 letterbox 居中**：把该页 `image_size` 等比缩放铺进画布、居中（留黑边而非拉伸变形）：
   `scale = min(画布W/w, 画布H/h)`；`off_x=(画布W-scale*w)/2`，`off_y` 同理。
3. **区域**：`x_emu = off_x + bbox_x * scale`（y 同理），宽高同乘 `scale`。整数 EMU。

> 备选（§9 待确认）：画布固定 16:9（标准投影比）+ letterbox。差异仅画布形状，变换逻辑同。

## 6. Spike 证据（2026-06-23，已跑真 VL，风险已消解）

用 `test_images/PPT` 的 slide 508 / 503 真跑 PaddleOCR-VL-1.6（vllm-server）拿到 raw
`coordinates`，结论（数据存 `/tmp/spike_out/`）：

**每个区域都有可用 bbox**（像素，落在 `image_size` 内）。标签谱：`paragraph_title /
text / image / chart / table / figure_title`。各标签的 `content` 来源：

| 标签 | `content` 内容 | 定位用法 |
|---|---|---|
| `paragraph_title` / `text` / `figure_title` | **干净文字**（直接可用） | 文本框 @ bbox |
| `table` | **完整 HTML `<table>`**（直接可用，slide503 region[3]=534 字符） | §9.1 原生表格 @ bbox |
| `image` / `chart` | **空串 `""`** | 裁图文件 @ bbox（映射见下） |

**figure ↔ 裁图文件映射已验证可行**：image/chart 区域 `content` 为空，但裁图按
**阅读序**对应 `raw_text` 里的 `<img src="images/N.jpg">`——
- slide 508：3 个 `image` 区域 ↔ raw_text 3 个 `<img>` ↔ 盘上 3 张 crop，1:1 干净；
- slide 503：区域阅读序 image→chart，raw_text 阅读序 `images/1.jpg`→`images/0.jpg`，
  即 image 区域 ↔ 1.jpg、chart 区域 ↔ 0.jpg（**文件名编号非阅读序，但区域阅读序 ↔
  raw_text `<img>` 阅读序一致**）。

⇒ **`coordinates` 本身就是一份干净的「bbox + 类型 + 内容（文字/HTML 表）」分区列表**，
图片块再按阅读序认领 `raw_text` 的 `<img src>`。这比预想干净——**不需要把锚点对齐进
`raw_text` 的 div 包装里**（§4.2 锚点直接加在 coords 区域单元前即可）。

**残留兜底**：极端页若 image/chart 区域数 ≠ raw_text `<img>` 数 → 该页图片退竖排附页尾，
文字/表格仍按 bbox 定位（位置部分忠实，不阻塞主收益）。

> 第二件 spike（精修能否稳定保留锚点）改为：因 `coordinates` 已是干净分区单元，精修在
> **区域单元粒度**进行、按 idx 重挂 bbox（见 §4.2 更新），匹配可靠性不再依赖 LLM 对自由
> 文本里锚点的保真——更稳。

## 7. 边界与 fail-safe（全链路退化，绝不丢内容）

- sidecar 缺失/版本不符/JSON 非法 → 退 §9.2 竖排。
- 某页 `regions` 为空 / 全部 bbox 非法（越界、零面积、x2<x1）→ 该页退竖排（按 `document.md` 对应页）。
- 区域 EMU 越画布 → clamp 进画布。
- 区域重叠（VL 偶发嵌套块）→ 按阅读序后绘者压前者（与原图层级一致）；文本框透明底不挡图。
- 精修返回不合规 → §4.2 整页退 raw。

## 8. 过度 / 欠工程判定

**刚刚好**：
- 数据**本就存在**（VL 已出 bbox），只做「捕获→持久化→消费」三段管道 + 整页精修加一条锚点约束，
  没有重建富 IR、没动 OCR 引擎、没动文档/代码模式、不新增 LLM 调用次数。
- 全链路 fail-safe 退竖排/退 raw，零回归风险；positioned 与 flow 并存。
- 锚点法用显式头锚点保持「位置 ↔ 内容」关联，规避 B 方案的按序对齐脆点——**用锚点换掉脆逻辑**。

**没有欠工程**：figure 映射的不确定性用 spike 先验证、不可靠就降级，不蒙头硬上。

**没有过度**：不引入逐页可变 slide 尺寸、不做像素级版式还原（字号/行距自适应留 lite）、
不为非 PPT 模式落 sidecar。

## 9. 已定决策 + 下一步

**已定（2026-06-23 用户拍板）**：
1. **slide 画布**：贴**首页原图长宽比**（最忠实原图）。
2. **触发**：`.ppt_layout.json` 存在即默认 positioned，无新 UI / 无新参数。
3. **精修关联**：进精修**前**把 bbox 作头锚点写进区域内容（自包含）；整页精修；输出 PPTX 时按
   锚点把 bbox ↔ 精修内容配对（§4.2）。区域精修产物**仅供版面 sidecar**，`document.md`
   仍走现有页级精修、剥掉锚点，互不影响、零回归。

**step 0 spike 已完成（2026-06-23）**：跑真 VL（slide 508/503），证据见 §6 + `/tmp/spike_out/`。
结论：每区域有 bbox、文字/表格内容直接可用、figure↔crop 按阅读序映射可行——风险消解、设计简化。

**按本设计实现**，拆 4 个有序子任务（逐个有证据闭环）：
1. ✅ **已完成**（commit `677e3e2`）捕获 `LayoutRegion`（含 image_ref 认领）+ 单测（喂构造 coordinates → 断言区域/映射）。
2. ✅ **已完成**（2026-06-24）`.ppt_layout.json` 落盘 + 坐标变换纯函数（§5）+ 单测（已知 bbox → EMU 落点）。
   落点：`output/ppt_layout.py`（纯模块）+ `pipeline.py::_write_ppt_layout_sidecar`（装配后落盘、文字过 PII 闸口）。
3. ✅ **已完成**（2026-06-24）导出器 positioned 渲染分支 + fail-safe 退竖排 + 单测（sidecar 在/缺两路）。
   落点：`pptx.py::_build_presentation` 分发（sidecar 合法 → `_build_positioned` 按 `region_box_emu`
   定位渲染文本框/原生表/图，任一异常退 `_build_block_flow`；某页无可用区域按 idx 退该页竖排）。
4. ⏳ 开精修：区域单元 idx 锚点精修 + idx 重挂 + 整页退 raw 兜底 + 单测（mock LLM 含/缺 idx）。

## 10. 验收清单

- [ ] 捕获：VL 跑一页 → `PageOCR.layout_regions` 非空、bbox 非零、label/content 合理（单测 + 真 OCR）。
- [ ] 精修匹配：注入 N 个头锚点 → 精修后按锚点切出 N 段配对 bbox；锚点数不符则整页退 raw（单测 mock LLM 输出含/缺锚点两路）。
- [ ] 持久化：`_ppt_pipeline` 产 `.ppt_layout.json`，schema 合法、坐标系正确（集成测）。
- [ ] 导出：sidecar 存在 → pptx 各 region 落在缩放后 bbox 位置（python-pptx 读回断言 shape 的 left/top/width 在预期区间）；
      sidecar 缺失 → 退竖排（现有用例不回归）。
- [ ] 坐标变换：构造已知 image_size + bbox → 断言 EMU 落点（纯函数单测，从输入派生，不写死数据集关键词）。
- [ ] 全链路 fail-safe：非法 sidecar / 空 regions / 非法 bbox → 退竖排不报错。
- [ ] 门禁 `bash scripts/check_quality.sh` 全绿。

