# PPT 版面位置导出设计（Epic D · Phase-2b / 关联 #74）

> 目标：导出 pptx 时，把每个区域（标题/正文/表格/图片）**按原屏摄图的版面位置**摆放，
> 而非现在的「逐页竖向堆叠」。方案 A（按 bbox 摆 + VL 原始内容；区域文字始终 raw，不单独精修）。
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

## 2. 方案：A（bbox + 原始内容），sidecar 始终用 raw 区域内容

| 表示 | 来源 | 位置 | 内容 |
|---|---|---|---|
| `coordinates` | VL 原始 | ✅ bbox | raw（`block_content`，spike 证实干净可用） |
| `document.md` | 页级精修后 | ❌ 无 | ✅ 精修 / 表格干净 HTML |

**用户决策（C，2026-06-24）**：positioned pptx 的区域内容**始终用 VL `block_content` raw**
（无论精修开关），不对区域单独跑精修：

- **省成本**：positioned 与页级精修解耦，**0 额外 LLM 调用**（页级精修照常产 `document.md`）。
- **够用**：PPT 多为短标题/标签，矫正后 raw 通常够用；表格走 §9.1 原生渲染。
- **取舍**：positioned pptx 文字不精修（公式类区域可能留 OCR 的 LaTeX 错误）；需要精修文本走
  `document.md` → docx/pdf（页级精修不变、全保真）。

> **「区域级 idx 锚点精修」已否决**：早期设计想把 bbox 作头锚点、整页精修后按 idx 重挂——
> 但它需要对每页区域 payload **额外跑一次 LLM**（与页级 body 精修是两份不同文本），与「不
> 新增调用」目标冲突，而短幻灯片文字 raw 通常够用，收益不抵成本。原 §4.2 机制不再实现。
> 不选启发式版面：用户要真位置，给不了。

## 3. 数据流总览

```text
VL worker coordinates[{label,bbox,text}]  ──capture──▶  PageOCR.layout_regions
                                                              │
                （文字区域过 PII 出云闸口脱敏，与 document.md 同口径；内容 raw 不精修）
                                                              ▼
              _ppt_pipeline 落 .ppt_layout.json（位置真相源，区域内容 raw）
                                                              │
              页级精修（开关开时）独立产 document.md，互不影响（决策 C）
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

### 4.2 区域内容：始终 raw（决策 C，原 idx 锚点精修已否决）

sidecar 的文字区域 `content` = VL `block_content` raw（表格为 raw HTML），**与精修开关无关**：

- 精修只动**页级 body**（`_ppt_pipeline` 的 `bodies` → `document.md`），**不碰**捕获的
  `layout_regions`（→ sidecar）。故开精修时 sidecar 天然仍 raw，无需任何匹配/重挂逻辑。
- 文字区域内容**仍过 PII 出云闸口**（`guard.redact_for_cloud`，与 `document.md` 同口径脱敏）——
  脱敏是安全要求，与「是否精修」正交。
- `image/chart` 区域 content 空、走 `image_ref`；`table` 区域 raw HTML 走 §9.1 原生渲染。

> **原方案（idx 锚点精修）为何否决**：它要把每页文字区域拼成带 `<!-- r:N -->` 头的 payload
> 额外跑一次 LLM、按 idx 重挂 bbox。但这是**与页级 body 精修并列的第二次调用**（两份不同文本），
> 与「不新增 LLM 调用」冲突；而短幻灯片文字 raw 通常够用。用户权衡后选「sidecar 始终 raw、
> 0 额外调用」（2026-06-24）。需要精修文本的用户走 `document.md` → docx/pdf。

### 4.3 持久化：`.ppt_layout.json` sidecar

`_ppt_pipeline` 组装阶段（`pipeline.py:1381` 前后，`_write_ppt_layout_sidecar`）把各页
`layout_regions` 的 raw 内容（文字过 PII 脱敏，§4.2）写一份位置真相源，落 `output_dir`：

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
- 含坐标信息但**不含原始 PII**（区域内容 raw 但已过 PII 脱敏闸口，与 document.md 同口径）。
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
图片块再按阅读序认领 `raw_text` 的 `<img src>`。这比预想干净——区域内容直接可用，
positioned 直接用 raw（§4.2 决策 C，不做区域精修）。

**残留兜底**：极端页若 image/chart 区域数 ≠ raw_text `<img>` 数 → 该页图片退竖排附页尾，
文字/表格仍按 bbox 定位（位置部分忠实，不阻塞主收益）。

> 原计划的「第二件 spike（精修能否稳定保留锚点）」已无意义：决策 C 下区域内容始终 raw、
> 不进精修，sidecar 与页级精修彻底解耦（§4.2），无锚点可保。

## 7. 边界与 fail-safe（全链路退化，绝不丢内容）

- sidecar 缺失/版本不符/JSON 非法 → 退 §9.2 竖排。
- 某页 `regions` 为空 / 全部 bbox 非法（越界、零面积、x2<x1）→ 该页退竖排（按 `document.md` 对应页）。
- 区域 EMU 越画布 → clamp 进画布。
- 区域重叠（VL 偶发嵌套块）→ 按阅读序后绘者压前者（与原图层级一致）；文本框透明底不挡图。
- 区域内容始终 raw（决策 C），无精修匹配环节，故无「精修不合规」失败路径。

## 8. 过度 / 欠工程判定

**刚刚好**：
- 数据**本就存在**（VL 已出 bbox），只做「捕获→持久化→消费」三段管道，没有重建富 IR、没动
  OCR 引擎、没动文档/代码模式、**0 额外 LLM 调用**（sidecar 用 raw，决策 C）。
- 全链路 fail-safe 退竖排，零回归风险；positioned 与 flow 并存。
- sidecar 与页级精修解耦：positioned 用 raw 区域内容、document.md 仍页级精修，互不影响。

**没有欠工程**：figure 映射的不确定性用 spike 先验证、不可靠就降级，不蒙头硬上。

**没有过度**：不引入逐页可变 slide 尺寸、不做像素级版式还原（字号/行距自适应留 lite）、
不为非 PPT 模式落 sidecar。

## 9. 已定决策 + 下一步

**已定（2026-06-23 用户拍板）**：
1. **slide 画布**：贴**首页原图长宽比**（最忠实原图）。
2. **触发**：`.ppt_layout.json` 存在即默认 positioned，无新 UI / 无新参数。
3. **区域内容**：sidecar 始终用 VL raw（文字过 PII 脱敏），**与精修开关无关**；页级精修只产
   `document.md`，与 sidecar 解耦、0 额外 LLM 调用（决策 C，2026-06-24，原 idx 锚点精修否决）。

**step 0 spike 已完成（2026-06-23）**：跑真 VL（slide 508/503），证据见 §6 + `/tmp/spike_out/`。
结论：每区域有 bbox、文字/表格内容直接可用、figure↔crop 按阅读序映射可行——风险消解、设计简化。

**按本设计实现**，拆 4 个有序子任务（逐个有证据闭环）：
1. ✅ **已完成**（commit `677e3e2`）捕获 `LayoutRegion`（含 image_ref 认领）+ 单测（喂构造 coordinates → 断言区域/映射）。
2. ✅ **已完成**（2026-06-24）`.ppt_layout.json` 落盘 + 坐标变换纯函数（§5）+ 单测（已知 bbox → EMU 落点）。
   落点：`output/ppt_layout.py`（纯模块）+ `pipeline.py::_write_ppt_layout_sidecar`（装配后落盘、文字过 PII 闸口）。
3. ✅ **已完成**（2026-06-24）导出器 positioned 渲染分支 + fail-safe 退竖排 + 单测（sidecar 在/缺两路）。
   落点：`pptx.py::_build_presentation` 分发（sidecar 合法 → `_build_positioned` 按 `region_box_emu`
   定位渲染文本框/原生表/图，任一异常退 `_build_block_flow`；某页无可用区域按 idx 退该页竖排）。
4. ✅ **已完成（决策简化）**（2026-06-24）开精修时 sidecar 仍 raw——精修只动页级 body、不碰
   `layout_regions`，故无需 idx 锚点精修/重挂（见 §4.2）+ 回归单测（开精修 → document.md 被
   精修但 sidecar 区域内容仍 raw）。

## 10. 验收清单

- [x] 捕获：VL 跑一页 → `PageOCR.layout_regions` 非空、bbox 非零、label/content 合理（单测 + **真 OCR E2E**，2026-06-24，叠加图目视各区域 bbox 精准框住内容）。
- [x] 开精修隔离：`enable_refine=True` → `document.md` 被页级精修，但 sidecar 区域内容仍 raw（单测 stub 精修器加前缀，断言前缀只进 document.md 不进 sidecar）。
- [x] 持久化：`_ppt_pipeline` 产 `.ppt_layout.json`，schema 合法、坐标系正确（集成测 + 真 OCR E2E：canvas 按首页长宽比、`image_size` 与矫正图一致、4 图引用全对上真文件）。
- [x] 导出：sidecar 存在 → pptx 各 region 落在缩放后 bbox 位置（单测 python-pptx 读回断言 left/top/width；**真 OCR E2E** soffice 渲染目视 503/508 2D 版面忠实还原）；sidecar 缺失 → 退竖排（现有用例不回归）。
- [x] 坐标变换：构造已知 image_size + bbox → 断言 EMU 落点（纯函数单测，从输入派生，不写死数据集关键词）。
- [x] 全链路 fail-safe：非法 sidecar / 空 regions / 非法 bbox → 退竖排不报错（单测覆盖）。
- [x] 门禁 `bash scripts/check_quality.sh` 全绿（1505 passed, 45 skipped）。
- [x] **真机 E2E**（2026-06-24）：活 VL OCR 3 slide → sidecar + positioned pptx 目视正确；修复 sidecar 图片引用 `_after` 命名 bbox（见 [known-issues.md](known-issues.md)）。

## 11. 区域样式增强：颜色采样 + 字号估计（2026-06-24，关联本次）

positioned pptx 此前文字固定 14pt / 纯黑 / 无背景。本节让重排文字的**颜色**与**字号**
接近原屏摄图，使导出 slide 更像原 PPT。沿用 Phase-2b 三段管道，不引迭代 / 形态学。

### 11.1 颜色采样（捕获期，新模块 `ocr/region_color.py`）

**位置**：必须在 `paddle_ocr.ocr()` 内——唯一同时握「源图像素 + 像素 bbox + image_size
一致三元组」的地方（导出期 `doc_dir` 只有输出 `images/`，源图已不在）。整页 `Image.open`
一次、逐区域切片，外层 `asyncio.to_thread` 包一次（async 安全，纯 CPU numpy）。

**取色（量化直方图，无迭代、确定性、可测）**：
1. ROI = `arr[y1:y2, x1:x2]`（bbox clamp 进 image_size；arr 尺寸与 image_size 不符→整页弃权）。
2. 内缩 ROI 中心、降采样到 ≤4000 采样点（抗摩尔纹 / JPEG 块，整步长切片不插值，不引抗锯齿过渡色）。
3. 每通道 `>>5` 量化到 8 级 → 512 桶 `np.bincount`（**粗量化**：真机屏摄 JPEG / 抗锯齿
   使底色散到邻桶，量化过细则主背景占比被摊薄而误判「无主导背景」，真机调优定为 8 级）。
4. **背景** = 最大桶（占多数）；**前景(文字)** = `count × 到背景色距` 最大的桶；各取桶内像素均值
   （均值取自桶内**真实像素**，粗量化不损色精度，只影响分桶聚合）。

**前景/背景判别用面积、不用亮度**：文字笔画永远是少数像素、底色是多数——多数簇=背景、
少数簇=前景。亮底黑字 / **暗底白字 / 彩字彩底全部正确**（绝不把暗色模式判反）。

**弃权（默认安全态，留 `None` → 渲染退默认黑字无填充）**：image/chart 区域 / ROI 过小
(<64px) / 前景背景对比不足(欧氏<60 或 Δ亮度<28) / 背景不占主导(frac_bg<0.15) / 前景占比
过高(>0.45) / 任何 numpy 异常。绝不让采样炸掉 OCR 主流程。

### 11.2 字号估计（纯渲染期，不进 sidecar）

`pptx._add_positioned_text` 内从 EMU box 反推（新增字段最少、不落盘）：
`font_pt = (box高 / 行数 / _EMU_PER_PT) / 1.2`，clamp 正文 [9,40] / 标题 [12,54]，加**宽度
护栏**防单行过长溢出；缺值退默认 `_BODY_PT`。表格保持 `_TABLE_PT`（逐格反推噪声大，保守不动）。

### 11.3 背景填充策略（必须，非可选）

只设前景色时浅色字会「白底隐形」。规则：背景**近白**(各通道≥240)→不填充（slide 默认白底即可）；
背景**非白 / 深色**→实心填充 + 无边框。暗色模式 / 彩色标题栏的浅字才可见，普通白底不画多余色块。
渐变背景靠 §11.1「底色散布无主导桶→frac_bg 不足弃权」兜住。

### 11.4 新增字段（仅 2 个色值，向后兼容）

- `LayoutRegion.fg_color / bg_color: tuple[int, int, int] | None = None`（捕获期填、弃权 `None`）。
- `PptLayoutRegion` 镜像；`to_dict` 存 `[r,g,b]` 或 `null`；`from_dict` 缺键 / 非法→`None`
  （**不 bump version**，旧 sidecar 读成 `None` 仍合法、不退整页；新代码读旧 sidecar 忽略缺字段）。
- 字号**无字段**（渲染期从 EMU box 算）。

### 11.5 过度 / 欠工程判定

**刚刚好**：稳健覆盖暗色模式（面积判别）/ 眩光（弃权）/ 渐变（无主导桶弃权）三大屏摄失败模式，
但不引 k-means 迭代 / 连通域形态学 / Lab 色空间 / 字号落盘 / 表格背景采样（均否决）。新增字段
仅 2 个色值，全链路 fail-safe 退默认，零回归（缺色照旧黑字竖排逻辑不变）。

### 11.6 验收清单（本节）

- [x] 合成像素图：黑字白底 / **白字深蓝底(暗色模式)** / 红字白底 → 取色正确（单测，断言从输入派生）。
- [x] 弃权：低对比 / 过小 / 噪声(无主导桶) / 双色块 → `None`（单测）。
- [x] **真机验证**（2026-06-24，3 slide 真 VL 区域）：12/13 文字区域采到合理色（表格因低对比正确弃权），
  暗色蓝底 banner 正确取浅前景 / 深背景（面积判别）；据真机把量化 16→8 级、frac_bg 0.35→0.15。
- [x] sidecar round-trip 带色 + **旧无色 sidecar 读成 `None` 向后兼容** + 非法色值降级不退整页。
- [x] 渲染：定位文本 run 取到 `font.color.rgb`==采样 fg、非白底加填充 / 近白不加、字号==helper 输出。
- [x] `_positioned_font_pt` clamp 边界单测。
- [x] 门禁 `bash scripts/check_quality.sh` 全绿。

