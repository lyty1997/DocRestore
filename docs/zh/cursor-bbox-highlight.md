# 光标 ↔ 原图 bbox 高亮设计（Epic E · #74）

> 目标：编辑器里光标所在文本块 → 高亮右侧原图对应区域（特性⑥）。
>
> 状态：**已落地**（2026-06-24）。E1/E2/E3 全部实现并测试通过（见 §10）。真相源：本文件。
>
> 范围决策（已与用户对齐）：
> - **编辑器光标驱动**：只在 WYSIWYG 编辑器里，光标所在块 → 高亮源图；只读预览本期不接。
> - **本期只做正向**：光标块 → 原图高亮。反向（点原图块 → 定位 markdown）留 Epic E phase-2。

## 1. 背景与目标

特性⑥：用户在编辑器里把光标移到某段文字，右侧对应原图上该段所在的版面块被高亮成
一个矩形，帮助核对「这段文字来自照片的哪个位置」。数据源是 PaddleOCR-VL 的**块级 bbox**。

issue 树（按依赖序）：
- **#83 E1 后端坐标层**：块级 bbox 透传 + 落 sidecar +（原计划）块→bbox 映射 dedup 同步裁剪。
- **#84 E2 API 载荷**：把块→bbox 透给前端。
- **#85 E3 前端高亮 overlay**：光标块 → 原图高亮矩形。

里程碑验收（#74）：光标移动 → 原图对应块高亮矩形；经 dedup 去重 + LLM 精修后映射不错位（块级）。

## 2. 关键发现：地基已建好大半，#83 的「核心难点」可绕开

这些 issue 是 **Phase-2b 之前**写的。Phase-2b（PPT 版面定位导出）落地后，块级 bbox 的捕获链路其实已经建好：

- `paddle_ocr._build_layout_regions` **不分模式**运行（`paddle_ocr.py:357`），
  `PageOCR.layout_regions`（`models.py:69`）在**文档模式下也填充**，每个块带：
  - `bbox`（原图像素 `(x1,y1,x2,y2)`）、`label`（text/paragraph_title/table/image…）、
    `content`（raw OCR 文字 / HTML 表）、`image_ref`、`fg_color/bg_color`。
- 即 issue #83 说的「worker 拿到 `block_bbox` 但 `paddle_ocr.py` 用完即弃」**已不成立**——
  bbox 现在进了 `PageOCR.layout_regions`。

**仍缺的三段**正好对应 E1/E2/E3：
1. 文档模式**不落 sidecar**：`_write_ppt_layout_sidecar` 仅 `ppt_cfg.enable` 时写
   `.ppt_layout.json`（`pipeline.py:1411`）。文档模式下 `layout_regions` 在内存里转瞬即逝。
2. API **不透出**：`TaskResultResponse`（`schemas.py:271`）只有 markdown/doc_title/doc_dir/error。
3. 前端**无 overlay**。

### 2.1 #83 标的「核心难点」其实没必要硬扛

issue #83 把「dedup 删重叠块时，块→bbox 映射**同步裁剪**」列为核心难点。两轮代码勘察确认：
想让「markdown 块 → bbox」的映射**无损穿过 dedup + LLM 精修**，是不可逆且脆弱的——

- dedup（`processing/dedup.py:123`）按**行**删重叠，markdown 块边界（标题/段落/表格）与行边界不对齐；
- 合并后**没有稳定的块 ID**，块身份靠 `<!-- page: -->` 标记 + 页内顺序；
- LLM 精修按 **segment** 改写文字、`_reassemble` 简单 `"\n".join`（`pipeline.py:3139`），
  丢弃 `Segment.start_line/end_line`，精修后无法反查块在原文的位置。

**结论：不要硬扛。** 改成「展示期、页级模糊匹配」（§3），dedup 完全不需要裁剪任何映射。

## 3. 总体方案：展示期、页级模糊匹配

核心思路：**bbox 映射不穿透 pipeline，而在前端展示期按页就地匹配**。

`layout_regions` 是 **OCR 期、按页（原始照片）** 捕获的，**早于** dedup 和精修。
按页文件名存成 sidecar，前端光标移动时：用现成的 `pageAtCursor` 拿到光标所在**页**
（`data-page` 锚点已有），再把光标周围文字，在**该页的少量候选块**里模糊匹配 raw OCR 文字，
命中谁就高亮谁的 bbox。

数据流（四段，各段都 fail-safe）：

1. **捕获期（已有，零改动）**：`paddle_ocr.ocr()` → `PageOCR.layout_regions`
   （原图像素 bbox + raw 文字），按页。
2. **落盘期（E1 新增）**：pipeline 在**文档模式主路**也落一份按页 sidecar `.layout.json`：
   `{version, pages:[{filename, image_size:[w,h], blocks:[{bbox, label, text}]}]}`。
   文字过**同一 PII 出云闸口** `redact_for_cloud`（与 `.ppt_layout.json` 同口径，本地产物）；
   非 VL / 无区域 → 不落盘，fail-safe。
3. **API 期（E2）**：独立端点 `GET /tasks/{id}/layout` 读 sidecar → `LayoutPayload`（zod 校验）。
4. **展示期（E3）**：编辑器 `onSelectionUpdate` → `pageAtCursor` 取页 → 取光标所在块纯文本 →
   在 `pages[filename].blocks` 里模糊匹配 raw `text`（§8）→ 命中块 bbox →
   复用 `CropEditor` 的 bbox→百分比换算 → 源图上叠高亮矩形。失配 → 不高亮（退化优于错高亮）。

### 3.1 为什么页级匹配就够

- dedup 只在**页重叠区**删行，但每页 sidecar 各自保全自己的全部块；
- 光标所在 `data-page` 锚点**唯一确定用哪一页**的块集，重叠歧义自然消解
  （重复块 = 同一内容拍了两次，高亮哪页的都对）；
- 精修改写了文字，但**同页候选集很小（5~15 块）**，对 raw OCR 文字做模糊匹配足够鲁棒；
- 不需要任何块 ID 注入 markdown（那会动 strip/sanitize/merge 三处耦合，#83 自己也想避开）。

## 4. E1 后端坐标层（#83）

### 4.1 通用 sidecar：新建 `output/layout_sidecar.py`，不动 PPT 链路

PPT 的 `.ppt_layout.json`（`output/ppt_layout.py`）额外算了 EMU 画布 / letterbox，是导出专用，
高亮用不到。**决策：新建轻量通用 sidecar `.layout.json`，PPT sidecar 保持不动**（已验证、不回归）。

`.layout.json` schema（纯像素，去掉 EMU/color/image_ref，减熵到高亮够用）：

```json
{
  "version": 1,
  "pages": [
    {
      "filename": "IMG_0001.jpg",
      "image_size": [3024, 4032],
      "blocks": [
        { "bbox": [120, 88, 2900, 240], "label": "paragraph_title", "text": "第一章 绪论" },
        { "bbox": [120, 260, 2900, 980], "label": "text", "text": "本文研究……" }
      ]
    }
  ]
}
```

模块内容（纯函数 + dataclass，镜像 `ppt_layout.py` 的 round-trip / fail-safe 风格）：
- `LayoutBlock(bbox, label, text)` / `LayoutPage(filename, image_size, blocks)` / `DocLayout(version, pages)`；
- `to_dict` / `from_dict`（缺字段/非法 → 跳过该块，**不整页失败**，向后兼容）；
- `build_doc_layout(pages) -> DocLayout | None`（无任何块 → None，不落盘）；
- `write_doc_layout(output_dir, layout)` / `load_doc_layout(output_dir) -> DocLayout | None`。

### 4.2 落盘点：文档模式主路

在 pipeline 文档模式收尾处（与 `document.md` / `.document.anchored.md` 落盘同段）装配并写
`.layout.json`：遍历 `ordered_pages`，每页把 `layout_regions` 转 `LayoutBlock`
（`bbox/label/content→text`），`text` 过 `redact_for_cloud`（同 PII 闸口），`build_doc_layout` → `write_doc_layout`。
非 VL / 无区域 → `build_doc_layout` 返回 None → 不落盘，导出/高亮端 fail-safe（前端无数据则不高亮）。

- 文件名 `.layout.json`（隐藏 sidecar，**不进下载 zip**，和 `.document.anchored.md` 同级）。
- 命名 stem 用与 renderer 同源的 `output_dir.name 去 _OCR`（Phase-2b 已踩过「丢 `_after`」坑，复用同一真相源）。
- 落盘走 `asyncio.to_thread` + `try/except OSError` → `logger.warning`，不阻断主流程。

### 4.3 坐标系（详见 §7）

`layout_regions.bbox` 是 **OCR 处理图的像素**。文档模式默认**不矫正** → OCR 跑原图 →
bbox 坐标系 = 原图 = 前端 `SourceImageList` 显示的图。`image_size` 记录 OCR 图尺寸（文档模式 = 原图尺寸）。

### 4.4 验收（mock）

1. 跑一页屏摄 → 产出 `.layout.json`，把块 bbox 叠回原图可视化，确认矩形落在对应块上。
2. 构造重叠两页 mock → 两页 sidecar 各自独立、块不串位（**无需裁剪**，各页 blocks 互不相干）。
3. 非 VL 引擎（FixtureOCREngine）→ `layout_regions` 空 → 不落盘、不报错。

## 5. E2 API 载荷（#84）

### 5.1 独立端点而非塞进 result

`TaskResult` 已不小，layout 仅高亮按需用 → **懒加载独立端点**，多文档每子目录各取一份：

```
GET /api/v1/tasks/{taskId}/layout?doc_dir=<相对子目录，可选>
→ 200 LayoutPayload | 404（无 sidecar）
```

`LayoutPayload`（pydantic，后端 `schemas.py`）：

```python
class LayoutBlockPayload(BaseModel):
    bbox: tuple[int, int, int, int]   # 原图像素 (x0,y0,x1,y1)
    label: str
    text: str

class LayoutPagePayload(BaseModel):
    filename: str
    image_size: tuple[int, int]       # (w, h) 像素
    blocks: list[LayoutBlockPayload]

class LayoutPayload(BaseModel):
    pages: list[LayoutPagePayload]
```

- 与 `data-page` 配对：`blocks` 按 `filename` 分页，前端 `pageAtCursor` 拿到 filename 直接索引。
- 路由读 sidecar 走已有 output_dir 解析 + `path_guard` 边界（与 `/assets`、`/source-images` 同口径）。

### 5.2 前端 zod（类型单源）

`frontend/src/api/schemas.ts` 加 `LayoutPayloadSchema`，`type LayoutPayload = z.infer<…>`；
client 加 `getTaskLayout(taskId, docDir?)`。

### 5.3 验收

mock sidecar → 端点返回数组，字段/类型符合契约（zod 通过）；无 sidecar → 404 优雅；多文档按 `doc_dir` 取对页集。

## 6. E3 前端高亮 overlay（#85）

### 6.1 取数

进编辑模式时 `getTaskLayout` → 存 `Map<filename, LayoutBlockPayload[]>` + `Map<filename, [w,h]>`。

### 6.2 光标 → 块

扩展 `pageAtCursor`（`MarkdownWysiwygEditor.tsx:125`）为 `blockAtCursor`：
`onSelectionUpdate` 时取光标所在**顶层块节点**的纯文本 `textContent` + 最近前置 `pageAnchor` 的 filename。
（debounce ~80ms 防抖。）

### 6.3 匹配 → bbox（§8）

`matchBlock(pageBlocks, cursorText)`：归一化后选重合度最高块，低于阈值返回 `undefined`。

### 6.4 overlay 组件

新组件 `BlockHighlightOverlay`：在 `SourceImageList` 对应页 `<img>` 上叠一层 `position:absolute` div，
用 `CropEditor` 同款换算画高亮矩形：

```
left   = bbox.x0 / image_size.w * 100  (%)
top    = bbox.y0 / image_size.h * 100  (%)
width  = (x1-x0) / image_size.w * 100  (%)
height = (y1-y0) / image_size.h * 100  (%)
```

**分母用 payload 的 `image_size`（OCR 图尺寸），不用 `img.naturalWidth`**——二者文档模式应相等，
但 payload 更早可用、避免图片 decode race。

- 滚动：复用现成「源图随文滚」（`onScrollContainerChange` / `data-page`），高亮只是叠加层，不改滚动逻辑。
- **sanitize 无需改**：overlay 在 `SourceImageList` 侧（React 组件，不经 markdown 渲染）→
  不动 `markdownSanitize.ts` 白名单。这点**纠正 issue #85 的顾虑**：块身份在编辑器节点遍历 + payload 匹配里解决，不往 markdown 注入块锚点 data-*。

### 6.5 验收

光标移到某段 → 右侧原图对应块高亮矩形；失配不高亮不报错；`scripts/screenshot.js` 截图验证。

## 7. 坐标系与矫正图处理

| 模式 | OCR 跑在 | bbox 坐标系 | 源图列表显示 | 是否对齐 | 本期 |
|---|---|---|---|---|---|
| 文档（默认） | 原图 | 原图像素 | 原图 | ✅ 对齐 | **接前端高亮** |
| PPT（rectify=True） | `{stem}_after.jpg` | 矫正图像素 | 原图 | ❌ 不对齐 | 不接（数据有，坐标系与显示图不符） |

文档模式不矫正，bbox 与显示图同坐标系，直接对齐——本期主场景。PPT 模式因显示原图、bbox 是矫正图坐标，
留 phase-2（届时显示矫正图或反算）。`image_size` 作为 % 分母的单一真相源，前端不依赖 `<img>` 实际解码像素。

## 8. 块模糊匹配算法

- **输入**：`cursorText`（光标所在编辑器块纯文本，已被精修改写）、各候选 `block.text`（raw OCR）。
- **归一化**：去全部空白 + 标点 + 转小写，截前 N 字（如 40）。
- **评分**：对每候选块——
  - 一者为另一者子串 → 高分（按较短串长度归一）；
  - 否则取最长公共子串长度 / 较短串长度 作比例分。
- **取最高分块**；最高分 < 0.5 → 返回 `undefined`（**不高亮，退化优于错高亮**）。
- **复杂度**：每页候选 5~15、每次光标移动一次匹配，O(候选数 × 文本长) 可忽略；配 ~80ms debounce。
- **鲁棒性**：标题/短段易撞 → 同分时取阅读序靠前 / 离上次高亮近的块；接受偶发不精确
  （高亮是辅助提示，非精确控制）。

## 9. 过度 / 欠工程判断

判定：**刚刚好**。

- **复用最大化**：bbox 捕获 Phase-2b 已做（零新捕获）；换算复用 `CropEditor`；页锚点复用 `data-page`；
  源图渲染复用 `SourceImageList`。新代码集中在「一个通用 sidecar + 一个 GET 端点 + 一个 overlay 组件 + 一个匹配函数」。
- **绕开高复杂度脆弱链**：不让块→bbox 映射穿透 dedup/精修（issue 原方案的核心难点），改展示期页内匹配。
- **欠工程风险**：模糊匹配偶发错块 → 阈值退化 + 仅辅助提示兜底；不追求像素级精确块映射（投入产出比差）。
- **过度工程规避**：不注入 markdown 块 ID（动三处耦合）；不做反向（本期）；不做行级（VL `text_lines` 恒空）；
  PPT 前端高亮延后（坐标系不匹配显示图）。

## 10. 验收清单与里程碑

**E1（#83）** ✅ 落地（`output/layout_sidecar.py` + `pipeline._write_doc_layout_sidecar`）
- [x] 文档模式落 `.layout.json`（块 bbox + 页 image_size），不进下载 zip（zip 显式 allowlist `document.md`+`images/`）。
- [x] 块 bbox/text round-trip 保真（`test_layout_sidecar.py`）；像素叠框可视化见 E3 真机截图。
- [x] 重叠两页 mock → 各自 sidecar 块不串位（`test_doc_layout_sidecar.py`）。
- [x] 非 VL 引擎 → 不落盘、不报错。
- 测试：`tests/output/test_layout_sidecar.py`(12) + `tests/pipeline/test_doc_layout_sidecar.py`(4)。

**E2（#84）** ✅ 落地（`LayoutPayload` + `GET /tasks/{id}/layout` + 前端 `getTaskLayout`）
- [x] `GET /tasks/{id}/layout` 返回 `LayoutPayload`，字段/类型符合契约（zod 通过）。
- [x] 多文档按 `doc_dir` 取对页集；无 sidecar → 404 优雅（前端 client 404→undefined 不弹错）。
- [x] doc_dir 边界守卫（`..` 越界 → 404）；新增 `LAYOUT_NOT_FOUND` + 三语 i18n。
- 测试：`tests/api/test_layout_endpoint.py`(5) + `frontend/tests/api/client.test.ts`(3)。

**E3（#85）** ✅ 落地（`blockAtCursor` + `computeBlockHighlight` + `matchBlock` + `BlockHighlightOverlay`）
- [x] 编辑器光标移到某段 → 原图对应块高亮矩形（`blockAtCursor` 真实 Tiptap 测 + 真机截图核对）。
- [x] 失配不高亮、不报错（`matchBlock` 阈值退化）。
- [x] 真机截图验证（静态 harness 真图叠框：蓝框精确落段、非命中页无框、% 与图严丝合缝）。
- [x] 未改 `markdownSanitize.ts`（overlay 在 `SourceImageList` React 层，非 markdown 渲染）。
- 测试：`blockMatch.test.ts`(10) + `blockHighlight.test.ts`(6) + `BlockHighlightOverlay.test.tsx`(3) + `SourceImageList.test.tsx`(3) + `blockAtCursor.test.ts`(3)。

> 注：`CursorBlock` / `SourceImageHighlight` 域类型与 `computeBlockHighlight` 下沉到
> `features/task/blockHighlight.ts`（避免 features→components 反向依赖，组件统一消费）。
> `data-page` 锚点从 `<img>` 移到外层 `.source-image-cell`（承载 overlay 相对定位），
> scroll-sync 仍按 `[data-page]` 定位（cell 与 img 垂直位置等价，零回归）。

**里程碑（#74）**：光标移动 → 原图对应块高亮矩形；经 dedup + 精修后不错位（块级，页内模糊匹配兜底）。

## 11. 范围外（Epic E phase-2）

- 反向联动：点原图块 → 定位/滚动到 markdown 对应文字（#89）。
- ~~只读预览 hover 高亮~~ → **已纳入 phase-2，见 §12（#88）**。
- PPT 模式前端高亮（坐标系/显示矫正图问题，#90）。
- 行级 bbox 高亮（VL `text_lines` 恒空，需后处理切行，#91）。
- 精确块映射（穿透 dedup/精修的无损映射）。

## 12. E4 只读预览 hover 高亮（#88，phase-2）

> 状态：phase-2 首个子任务。把光标高亮从「仅 WYSIWYG 编辑器」扩到**只读预览模式**，
> **零新算法**，复用 phase-1 全部地基（`getTaskLayout` + `computeBlockHighlight` +
> `BlockHighlightOverlay` + `SourceImageList` overlay）。

### 12.1 唯一新增：`previewBlockAtPointer`（镜像 `blockAtCursor`）

预览侧无 Tiptap 节点，改从 DOM 求「光标块」。新建纯函数
`features/task/previewBlockAtPointer.ts`：`previewBlockAtPointer(target, container) → CursorBlock | undefined`，
语义与编辑器 `blockAtCursor`（§6.2）**一一对应**：

| 维度 | 编辑器 `blockAtCursor` | 预览 `previewBlockAtPointer` |
|---|---|---|
| 「块」 | 光标所在**顶层块节点**（`$from.node(1)`，doc 直接子节点） | 命中元素向上到**容器直接子节点**（`.markdown-preview` 直接子块） |
| 文本 | `node(1).textContent.trim()` | `block.textContent.trim()` |
| 页 | 最近**前置** `pageAnchor` 的 `page` 属性 | 最近**前置** `[data-page]` 锚点的 `dataset.page` |
| 退化 | 无页 / 空块 → undefined | 容器外 / 无前置页 / 空块 → undefined |

取「容器直接子节点」而非「最近 p/h1-h4/li」是为了**严格镜像** `node(1)`：react-markdown v9
把块直接渲染为 `.markdown-preview` 的直接子节点（无 wrapper），列表/表格作为整块（与编辑器
depth-1 一致），匹配面与 E3 完全相同 → 同一段在预览/编辑两模式得到同一高亮。
「最近前置 `[data-page]`」用 `compareDocumentPosition` 在文档序里取该块之前最后一个页锚点
（`injectPageAnchors` 已把 `<!-- page: X -->` 转成 `<span class="page-anchor" data-page="…">`）。

### 12.2 接线（`DocCodePreview`，链路其余零改）

- `canHighlight` **去掉 `editMode` 约束** → `viewMode==='doc' && !selectedDocFailed && selectedDoc!==undefined`，
  预览与编辑两模式都取 layout、都把 `highlight` 传 `SourceImagePanel`（原本仅编辑模式传）。
- 预览容器 `.markdown-preview` 挂 `onMouseMove`（debounce ~80ms，同编辑器侧）→
  `previewBlockAtPointer(e.target, e.currentTarget)` → `setCursorBlock`；`onMouseLeave` → 清空。
  > React 合成事件在 `setTimeout` 回调里 `currentTarget` 会被置空，**同步**先把
  > `target`/`container` 取出再进定时器。
- `editMode` 切换时 `setCursorBlock(undefined)` 复位，避免切模式残留上一模式的高亮。

### 12.3 验收（#88）

- [x] `previewBlockAtPointer` 单测：构造含 `[data-page]` + 多块的预览 DOM，hover 不同块断言
  `{page, text}`、容器外/空块/无前置页 → undefined（镜像 `blockAtCursor.test.ts`）。
- [x] overlay 渲染**复用 E3 已像素核对的 `BlockHighlightOverlay`**（本期零视觉改动），
  截图验证 hover 段落 → 原图叠框、移出清空。

## 13. E6 PPT 模式高亮（#90，phase-2）

> 状态：phase-2。用户实际在用 PPT 模式，发现光标在文档上但原图不高亮——根因是 E3/E4 为
> **纯文档模式特性**，PPT 任务只落 `.ppt_layout.json` 不落 `.layout.json`，且 bbox 在**矫正图**
> 坐标系而源图栏显示原图（§7 早已标注延后）。本节让高亮覆盖 PPT 模式。
>
> **后续通用化（§15）**：本节的 `rectified` 标志 / `rectified-image` 端点已**通用化**为
> `processed` / `processed-image`，覆盖 PPT 矫正 + content_crop 裁剪两类预处理（下文 `rectified`
> 读作 `processed`、`_after` 是其一个 variant）。

### 13.1 关键发现：`.ppt_layout.json` 已含高亮所需，现有任务无需重跑

实测 PPT 任务 sidecar `.ppt_layout.json`（`output/ppt_layout.py`）每页已有：
- `filename` = **原图名**（与 `<!-- page: X -->` marker、源图基名同源 `page.image_path.name`）；
- `image_size` = **矫正图**像素尺寸（如原图 1706×1279 → 矫正图 1205×809）；
- `regions[].bbox` = **矫正图**坐标系像素、`regions[].content` = raw OCR 文字。

即 `.ppt_layout.json` 与 E1 的 `.layout.json` 信息等价（仅键名 `regions/content` vs `blocks/text` +
多了 `slide_size_emu`/`image_ref`/颜色）。**结论：不改 pipeline、不重跑**，让 API 层在 `.layout.json`
缺失时回退读 `.ppt_layout.json` 转成 `LayoutPayload` 即可——现有 PPT 任务立即可高亮。

### 13.2 坐标系对齐：源图栏改显矫正图（唯一理智方案）

bbox 在矫正图坐标系，原图却因透视矫正长宽比已变（1706×1279→1205×809）。两条路：
- **(A) 源图栏对 PPT 显示矫正图 `_after.jpg`**：bbox/image_size 与显示图同系，overlay 直接对齐。✅ 选此。
- (B) 反算把矫正图 bbox 透视逆变换回原图：bbox 变成**四边形**（非轴对齐矩形），overlay 要画 polygon，
  复杂且脆弱。❌ 否决。

矫正 slide 是去畸变的正视图、与导出的 pptx 一致，展示价值更高。三键对齐保持不变：
`cursorBlock.page`(marker=原图名) = `layout.filename`(原图名) = 源图 cell `data-page`(原图名)；
**仅 `<img src>` 从原图换成矫正图**，pageKey/data-page 不动 → `computeBlockHighlight` 与 cell 命中零改。

### 13.3 改动清单（复用 E1-E4 全链）

- **后端 B1**（`api/routes.py` `get_task_layout`）：`load_doc_layout` 返回 None 时回退
  `load_ppt_layout`，`regions→blocks`（`content→text`）、`rectified=True`。
- **后端 B2**（`api/schemas.py`）：`LayoutPayload` 加 `rectified: bool = False`。
- **后端 B3**（`api/routes.py`）：新端点 `GET /tasks/{id}/rectified-image?name=&doc_dir=`，
  原图名 → `{output_dir}/{doc_dir}/.rectified/{stem}_after.jpg`，镜像 `get_source_image` 的越界守卫
  （词法 `is_relative_to` 不跟随 symlink + `is_file` 确认）；缺失 → 404（前端 `onError` 回退原图）。
- **前端 F1**（`schemas.ts`/`client.ts`）：`rectified` 入 zod；加 `getRectifiedImageUrl(taskId, name, docDir?)`（带 token）。
- **前端 F2**（`DocCodePreview`→`SourceImagePanel`→`SourceImageList`）：`layout.rectified` 时给源图
  `<img src>` 用矫正图 URL（`data-page`/pageKey 仍原图名），`onError` 回退原图。overlay 不变。

### 13.4 验收（#90）✅ 落地

- [x] `/layout` 对 PPT 任务回退 `.ppt_layout.json` → `rectified=true` + blocks（`content→text`）。
- [x] `rectified-image` 端点：原图名 → `.rectified/{stem}_after{suffix}`，缺失 → 404、穿越 → 400。
- [x] 前端 PPT 任务源图栏显矫正图（`onError` 回退原图）、光标块 → 矫正图叠框对齐。
- [x] 文档模式回归：`.layout.json` 在 → `rectified=false`、仍显原图（不回归）。
- 测试：`tests/api/test_layout_endpoint.py`（+4：PPT 回退 / rectified-image 200·404·400）；
  前端 `client.test.ts`（+3 `getRectifiedImageUrl`）+ `SourceImageList.test.tsx`（+2 矫正图 src）。
- 真机视觉：用现有 PPT 任务真实 `.ppt_layout.json` 全部 region bbox 按 % 叠真实矫正图 `_after.jpg`，
  标题/正文/双图/footer 各区**精准框住内容**，坐标系对齐确证。
- **现有 PPT 任务零重跑即可高亮**（API 回退读已落的 `.ppt_layout.json`，无 pipeline 改动）。

## 14. MinerU 勘察结论与借鉴边界（mineru.net「版面图」效果）

用户希望达到 mineru.net 在线提取器的「彩色版面图 + 联动」效果，勘察了本地 MinerU 全量克隆
（`ref/MinerU`，5-agent workflow + 综合官亲验关键文件）。

### 14.1 结论：联动前端未开源，只有服务端出图 + 数据契约

| 维度 | ref/MinerU 里有没有 | 证据 |
|---|---|---|
| 独立 JS/TS 前端工程 | **零** | 全仓无 `package.json`/`*.vue/*.tsx/*.jsx` |
| 浏览器内 bbox 叠框 / 双向联动 | **零** | grep `scrollIntoView`/`data-block-id`/`data-bbox` 零命中 |
| 服务端画框出图（静态，非交互） | **有** | `mineru/utils/draw_bbox.py` reportlab `canvas.rect` 烧进 `_layout.pdf` |
| bbox 数据契约 | **有且完备** | `content_list.json`：0-1000 归一 bbox + `page_idx` + `type` + `index`(阅读序) |

唯一「前端」是 Gradio WebUI（`mineru/cli/gradio_app.py`），**左右两栏完全独立**（左 = 静态 `_layout.pdf`
经 `gradio_pdf.PDF` 展示，右 = markdown/json 文本），**无 id 映射、无滚动同步、无 hover/click 联动**。
**mineru.net 那个精致联动 viewer = SaaS 专有，不开源**（`README_zh-CN.md:66`）。

### 14.2 借鉴边界（借契约/思路，不抄代码）

- **能借（增量小改）**：① category 着色思路（`draw_bbox.py` 的 BlockType→色映射）；
  ② 阅读序号（block 的 `index` 字段）。→ 落为 **E8 版面全览叠加**（见下方 issue）。
- **不必借**：0-1000 归一化坐标——我们 `BlockHighlightOverlay` 用 `payload.image_size` 作 % 分母
  已解决 decode race + 文档/PPT 两套坐标系，归一化对我们**零实际收益**（还会与 PPT 的 EMU 导出抢
  bbox），**不单开 issue**。
- **借不了**：MinerU 的**稳定块 ID 双向映射**——前提是「块身份从 OCR 到输出不变」，而我们 dedup +
  LLM 精修打断了块身份链（#83 架构反转已证伪），故照搬会崩。我们的「页级模糊匹配」是自身约束下的
  正解，**明确放弃稳定 ID**。反向联动（#89）MinerU 开源部分也没有，复用 `blockMatch` 对称自建。

### 14.3 衍生 issue

- **E8（版面全览叠加）**：源图叠**全部**版面块的彩色分类框 + 阅读序角标（仿 mineru 版面图）；
  sidecar 加 `index`，前端加 `LayoutOverlay` + `label→色` 映射 + toggle。复用现有 overlay 换算。
- **#89（反向联动）**：点原图块 → 定位 markdown，复用 `blockMatch` 对称反查（MinerU 无现成参考）。

## 15. content_crop 高亮错位修复 + 「处理图」机制通用化

> 用户报「光标在文档上高亮框非常不准，且没开精修」。真机实测（注入 device token 打开任务）
> 定位：该任务**文档模式 + content_crop（正文自动裁剪，默认开）**。`.layout.json`
> `image_size=[1418,1646]`=裁剪图，原图 `DSC04641.JPG=[2467,1646]`——bbox 在裁剪图坐标系，
> 源图栏却显原图 → 标题框画在 3%–81% 而正文实际 ~23%–68%，**左偏 ~20% + 横向拉宽 1.74×**。

### 15.1 根因：预处理坐标系不匹配是**通用** bug，#90 只解了 PPT

`pipeline.py:2047`：OCR 前任一预处理（PPT 矫正 / content_crop 裁剪 / 手动裁剪）后，`ocr_input`
是处理图，OCR 出的 `image_size`+`layout_regions.bbox` 在**处理图坐标系**，但 `page.image_path`
被改回原图（marker/源图按原名匹配）。#90 仅对 PPT 让源图栏显矫正图；content_crop / 手动裁剪
同病未解。content_crop 是**纯水平裁剪**（保全高、裁左右空白边），裁剪图已落 `.content_crop/{stem}_crop`。

### 15.2 方案：把 #90「显处理图」机制通用化（零重跑）

决策（用户选）：泛化而非后端平移 bbox（后者需重跑现有任务）。`.content_crop/{stem}_crop` 已在盘上，
**现有任务零重跑**即对齐：

- `rectified` 标志 → **`processed`**（bbox 在处理图坐标系，须显处理图）；按**探处理图目录**
  （`.rectified` / `.content_crop` 有文件）统一判定，比「PPT 恒 true」更准（全回退原图的页 → false）。
- `rectified-image` 端点 → **`processed-image`**：逐 variant 探
  `.rectified/{stem}_after{suffix}`（PPT）、`.content_crop/{stem}_crop{suffix}`（裁剪），命中即返回；
  均无 → 404（前端 `onError` 回退原图，未处理页 bbox 本在原图系，回退即对——**逐页混合自洽**）。
- 前端 `getProcessedImageUrl` / `SourceImageList processed`：`processed` 时 img 显处理图
  （`data-page`/pageKey 仍原图名保三键对齐），`onError` 一次性回退原图。

### 15.3 坐标系自洽（逐页混合）

| 页情况 | sidecar bbox/image_size | processed-image | 显示 → 对齐 |
|---|---|---|---|
| 裁剪成功 | 裁剪图坐标 | `.content_crop/_crop` 命中 | 裁剪图 ✅ |
| PPT 矫正 | 矫正图坐标 | `.rectified/_after` 命中 | 矫正图 ✅ |
| 未处理（检测跳过/回退） | 原图坐标 | 404 | onError→原图 ✅ |

### 15.4 验收

- [x] 后端 `test_layout_endpoint.py`（11 passed，+content_crop layout `processed=true` + processed-image
  探 `_crop`）；API 全目录 255 passed 零回归。
- [x] 前端 217 passed（client `getProcessedImageUrl` 3 + SourceImageList processed src 2 改名）；
  tsc -b + `npm run lint` 0 error。
- [x] 真机视觉：用**用户实际任务**的真实裁剪图 `.content_crop/DSC04641_crop.JPG`（1418×1646）+
  `.layout.json` 真实 bbox 按 % 叠加，标题/双表/各标题块**精准框住**（对比修复前原图上左偏拉宽）。
- 代价：「原图」栏对裁剪页显示去掉左右空白边的裁剪图（内容一致），与 PPT(#90) 同口径。

## 16. 图片/图表块高亮（按 image_ref 匹配）+ 橙色高亮框

> 用户报「插图图片不高亮、图表不高亮」。根因：image/chart 区域 OCR **无文字**
> （`content=""`），文字模糊匹配天然命中不了；且光标落在图片块时 `textContent` 为空，
> 块检测直接返回 undefined。表格（table）有 HTML 文字，已可文字匹配。

### 16.1 方案：图片/图表按 `image_ref` 精确匹配

image/chart 区域捕获时已按阅读序认领 `<img src="images/N.jpg">`
（`paddle_ocr._build_layout_regions`，`_IMAGE_REGION_LABELS={image,chart}`）。让 sidecar 带上
**最终输出引用** `image_ref`，与 markdown `<img src>` 对齐后按引用匹配：

- **后端**：`LayoutBlock`/`LayoutBlockPayload` 加 `image_ref`；`_write_doc_layout_sidecar` 用
  `resolve_output_image_ref(ocr_stem, region.image_ref)` 算输出引用（`ocr_stem` =
  `page.output_dir.name` 去 `_OCR`，裁剪/矫正时是处理图 stem，如 `DSC04643_crop`）→
  `images/{stem}_N.ext`，**与 markdown `<img src="images/DSC04643_crop_0.jpg">` 一致**（已验证）。
  to_dict/from_dict 带 image_ref，旧 sidecar 无此字段 → 默认空（向后兼容）。PPT 回退用
  `region.image_ref`（ppt sidecar 本就有）。
- **前端**：`CursorBlock` 加 `imageRef`；`extractImageRef(src)` 取 `<img src>` 的 `images/xxx` 尾段
  （复用 asset URL → `images/` 提取）；`previewBlockAtPointer`/`blockAtCursor`（编辑器遍历 image 节点）
  在**块无文字**时取图片引用；`computeBlockHighlight` 有 `imageRef` → 按 `block.image_ref` 精确匹配，
  否则文字模糊匹配。
- **限制**：image_ref 是 sidecar **新字段**，现有任务的 `.layout.json` 无此字段 → 图片高亮**需重跑任务**
  （不像 content_crop 零重跑——裁剪图已在盘，但 image_ref 须重新落 sidecar）。

### 16.2 橙色高亮框

`.block-highlight-overlay` 原用主题 `--color-primary`（与按钮/进度条共享，改色会波及）→ 改为**独立橙色**
（`#f97316` 边框 + 半透明填充 + 橙色外发光），不动共享变量。

### 16.3 验收

- [x] 后端 `test_layout_sidecar`（+image_ref from_region / round-trip / 向后兼容 3 测）；
  `resolve_output_image_ref` 值与 markdown `<img src>` 一致性已验证。
- [x] 前端 222 passed（+`extractImageRef` 2 + previewBlockAtPointer 图片块 1 + computeBlockHighlight
  imageRef 命中/失配 2）；tsc + lint 0 error。
- [ ] 真机：橙色即时可见（前端热更新）；图片高亮须**重跑任务**生成带 image_ref 的 sidecar 后验证。

