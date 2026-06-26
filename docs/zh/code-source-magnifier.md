# 代码模式源图放大镜（悬停行 → 原图局部放大 + 底部缩略图定位）

> 跟踪 issue：#93 ｜ 状态：已落地（含编辑态光标跟随增量，2026-06-26）
> 关联：Epic E 光标↔原图 bbox 高亮（`docs/zh/cursor-bbox-highlight.md`，文档/PPT 形态）

## 1. 背景与目标

代码模式右侧源图（原始拍照）整张缩在窄栏里**根本看不清**。本设计给代码模式一个
跟随鼠标的「源图放大镜」：

- 悬停某代码行 → 在编辑栏顶部放大显示**该行±1 行**对应的原图局部；
- 整张源图退化为**界面最下方**的缩略图条，标记当前行所属那张。

与文档/PPT 的「光标↔原图 bbox 高亮」（Epic E `.layout.json`）同属「正文位置 ↔ 原图坐标」
问题，但形态是**放大镜**而非在整图上描框——因为代码源图太密，描框看不清，放大才解决痛点。

## 2. 决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 精度 | **精确·行级坐标**（后端导出 `CodeLine.bbox`） | 用户要「当前行±1」，唯有行级 bbox 能稳定命中；页/列级插值遇空行/换行会漂 |
| 触发 | **鼠标悬停代码行** | 只读虚拟化查看器无真光标；悬停最贴近「指着这行看它来自哪块原图」 |
| 放大镜位置 | **嵌入 IDE 编辑栏顶部**（固定条，随编辑栏宽） | 用户拍板；与代码同列，视线不跳栏 |
| 缩略图位置 | **整个界面最下方**，横跨全宽 | 用户拍板；替换原全尺寸右侧源图面板，源图只以缩略图存在 |
| 坐标系 | 原图像素，不经处理图 | 代码模式无 content_crop / 无矫正，OCR 输入即原图（开工复核） |

## 3. 数据链与坐标对齐（已逐行核实）

后端到前端的位置真相链，全部已在现有代码里验证存在：

1. **行级 bbox 现成**：`SourceFile.pages[i].column.lines[j].bbox: tuple[int,int,int,int] | None`
   是每行在源图的像素框，`render_code_files` 落 files-index 时它仍在作用域 →
   **序列化它零额外计算**（数据本就算好被丢弃）。
2. **重叠区归属**：`SourceFile.line_provenance: dict[int, str]`（line_no → 胜出页 stem）
   决定同一 line_no 多页观测时该用哪张图的 bbox。
3. **行号即锚点**：前端 `displayLineNumber(entry, i) = entry.line_no_range[0] + i`，
   编辑器每行写 `data-line={displayLineNumber}` → **`data-line` 就是 OCR `line_no`**，
   sidecar 按 `line_no` 建键、前端按 `data-line` 查表，**直接对齐、无任何换算**。
4. **页标识复用**：sidecar 的 `page` 用 `{page_stem}.col{column_index}`（与 files-index 的
   `source_pages` / `source_page_ranges` 同格式），前端现成
   `pageKeyBySourcePage: Map<sourcePage, CodeSourceImage>` 直接解析到源图文件名 + URL。
5. **原图坐标**：代码模式不做 content_crop（文档模式专属）、不做透视矫正（PPT 专属），
   故 bbox 落在原图像素系；前端加载原图读 `img.naturalWidth/naturalHeight` 即可，
   **不需 processed-image 链路**（开工时复核「代码模式 OCR 输入＝原图」假设）。

## 4. Sidecar 设计 `.code_layout.json`

dot 前缀＝内部文件，不进下载 zip / asset 白名单（与 `.layout.json` 同约定）。

```jsonc
{
  "version": 1,
  "files": [
    {
      "path": "app/foo.cc",                 // == files-index entry.path（前端按选中文件查表）
      "lines": [
        { "line_no": 12,                      // int，OCR 行号（== 前端 data-line）
          "page": "page0001.col0",            // str，{stem}.col{idx}
          "bbox": [120, 880, 1640, 932] }     // 4×int 原图像素 (x0,y0,x1,y1)
      ]
    }
  ]
}
```

设计要点：

- **不含代码文字**：只有 bbox + 行号 + 页标识 → PII 面零暴露，**无需 redact**
  （与 `.layout.json` 需对正文脱敏不同，这是本设计的减负点）。
- **只落有 bbox 的行**：`CodeLine.bbox is None`（推断行 / gap 填充空行）跳过，
  前端查表 miss → 就近回退（§7）。
- **宽松反序列化**：坏行 / 坏文件逐个跳过、不致整份失败；`version` 不符 → 视为无数据
  （前端不显示放大镜，优雅退化）。镜像 `layout_sidecar.from_dict` 的容损策略。

## 5. 后端

### 5.1 纯模块 `output/code_layout_sidecar.py`

镜像 `layout_sidecar.py` 的 dataclass + 序列化 + 读写结构，不依赖 OCR / PII / 导出工具，便于单测。

```python
@dataclass(frozen=True)
class CodeLineBox:
    line_no: int
    page: str                              # {stem}.col{idx}
    bbox: tuple[int, int, int, int]

@dataclass(frozen=True)
class CodeFileLayout:
    path: str
    lines: list[CodeLineBox]

@dataclass(frozen=True)
class CodeLayout:
    files: list[CodeFileLayout]
    version: int = 1

def build_code_layout(sources: list[SourceFile]) -> CodeLayout | None: ...
# 对每个 SourceFile：遍历 pages[].column.lines，line.bbox 非空才收；
# 同 line_no 多页时取 line_provenance[line_no] 指定的胜出页（缺省取首个有 bbox 的页）。
# 全部文件无任何行 bbox → None（调用方不落盘）。

def to_dict(layout) -> dict[str, object]: ...
def from_dict(data: object) -> CodeLayout | None: ...   # 宽松：坏行/坏文件跳过
def write_code_layout(output_dir: Path, layout: CodeLayout) -> Path: ...   # .code_layout.json
def load_code_layout(output_dir: Path) -> CodeLayout | None: ...
```

### 5.2 pipeline 落盘（`_code_pipeline`）

在 `render_code_files(sources, output_dir, ...)` 之后追加：

```python
layout = build_code_layout(sources)
if layout is not None:
    try:
        await asyncio.to_thread(write_code_layout, output_dir, layout)
    except OSError:
        logger.warning("代码版面 sidecar 落盘失败（不阻断主流程，前端不放大）", exc_info=True)
```

落盘失败仅告警不阻断（放大镜是增强，主链路不依赖）。

### 5.3 路由 `GET /api/v1/tasks/{task_id}/code-layout`

镜像 `get_task_layout`，但**不涉及 processed**（代码模式原图坐标）：

- 入参：`task_id`（path）、`doc_dir`（可选，多文档相对子目录）；
  `..` / 绝对路径 / `is_relative_to(output_dir)` 越界 → 视作无数据 404。
- 200 → `CodeLayoutPayload`（见 §6.1）。
- 404 → 新增错误码 `CODE_LAYOUT_NOT_FOUND` + 三语 i18n；前端 client 把 404 视作
  「无放大数据、不显示放大镜」，不弹错误（与 `getTaskLayout` 同口径）。
- `load_code_layout` 放 `asyncio.to_thread`（磁盘 I/O）。

## 6. 前端

### 6.1 schema + client

```ts
// schemas.ts
const CodeLineBoxSchema = z.object({
  line_no: z.number(),
  page: z.string(),
  bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]),
});
const CodeFileLayoutSchema = z.object({ path: z.string(), lines: z.array(CodeLineBoxSchema).default([]) });
const CodeLayoutPayloadSchema = z.object({ files: z.array(CodeFileLayoutSchema).default([]) });
export type CodeLayoutPayload = z.infer<typeof CodeLayoutPayloadSchema>;

// client.ts —— 404 → undefined（无放大数据），其余错误照常抛
export async function getTaskCodeLayout(
  taskId: string, docDir?: string,
): Promise<CodeLayoutPayload | undefined> { ... }
```

### 6.2 纯函数 `features/task/codeLineMagnifier.ts`

```ts
// path → (line_no → {page, bbox})，挂载时建一次
export function buildLineIndex(
  payload: CodeLayoutPayload,
): Map<string, Map<number, { page: string; bbox: BBox }>> { ... }

// 当前行 → 放大区域：line±1 同页 bbox 并集；当前行无 bbox → 向两侧就近找有 bbox 的行回退
export function computeMagnifierRegion(
  fileIndex: Map<number, { page: string; bbox: BBox }>,
  lineNo: number,
): { page: string; region: BBox } | undefined { ... }
```

`region` 取 `lineNo-1 / lineNo / lineNo+1` 中**与 `lineNo` 同页**者的 bbox 并集
（跨页边界只并同页那侧）；都缺则向上下各扫若干行找最近有 bbox 的行，仍无 → `undefined`
（不显示放大镜，优于错显）。

### 6.3 CodeViewer 接线

1. 选中文件 / 任务变化时 `getTaskCodeLayout` → `buildLineIndex`（缓存）。
2. 代码滚动容器挂 `onMouseMove`（debounce ~60ms）：`e.target.closest('[data-line]')` 取
   `data-line` → `lineNo` → `computeMagnifierRegion(fileIndex, lineNo)`。
3. 命中 → 设 `{page, region}`：
   - `page` 经 `pageKeyBySourcePage` → `CodeSourceImage`（源图 URL + pageKey）；
   - 放大镜＝`CropZoomViewport`（`key={pageKey}` 切图重挂，`initialRegion=region`），
     内嵌该源图 `<img>` + 只读 `BlockHighlightOverlay`（当前行 bbox 描细框）；
     `naturalWidth/Height` 由 `<img onLoad>` 落地（jsdom 缺测量时回退整图）。
4. 缩略图条高亮：当前命中 `page` 对应缩略图加命中样式。
5. 鼠标移出代码区：保留最后一次放大（不闪烁），或显占位提示「悬停代码查看原图」。

### 6.4 布局（用户拍板）

- **放大镜**：嵌入 IDE 编辑栏**顶部**的固定条（随编辑栏宽，高度 `min(15vh, 160px)`，
  2026-06-26 用户反馈过高已从 `min(24vh,260px)` 收窄，见 §11），其下才是虚拟化代码滚动区。
- **缩略图条**：整个界面**最下方**横跨全宽的一行，水平滚动；当前行所属图高亮描边，
  点击打开 lightbox。**替换原全尺寸右侧源图面板**——源图自此只以缩略图存在。
- 原 `codePageAnchors` 滚动同步逻辑：缩略图条仍可复用其页级锚点做「滚动时高亮当前页缩略图」，
  与悬停高亮二选一或叠加（实现期定，默认悬停优先）。

## 7. 边界与退化

| 情形 | 行为 |
|---|---|
| 无 sidecar（老任务 / 非 VL / 无行 bbox） | `/code-layout` 404 → 不显示放大镜；缩略图条仍在（仅无放大） |
| 当前行无 bbox（推断行 / gap 空行） | 向两侧就近找有 bbox 的行；扫描窗口内仍无 → 不放大 |
| line±1 跨页边界 | 只并当前行同页的相邻行 bbox；放大镜只显当前页 |
| jsdom / 无布局测量 | `CropZoomViewport` 回退整图 100% 宽（已有行为），不崩 |
| 大 gap 注释占位行（line_no 1:1 被破坏） | 该行 data-line 无对应 sidecar 行 → miss 回退，可接受 |

## 8. 复用件清单（零造轮子）

| 用途 | 复用 |
|---|---|
| 放大镜 bbox→CSS 局部放大铺满 | `CropZoomViewport` + `fitRegion()`（`cropFit.ts`） |
| 当前行描框 | `BlockHighlightOverlay`（bbox→百分比矩形） |
| sidecar 落盘/端点/取用模板 | `layout_sidecar.py` + `GET /tasks/{id}/layout` + `getTaskLayout` |
| 缩略图列表 + lightbox | `SourceImageList`（缩小）+ `ImageLightbox` |
| 页标识→源图解析 | 现成 `pageKeyBySourcePage` |

## 9. 子任务与验收（逐个有证据闭环）

- **D0** 本设计文档 ✅
- **B1** `code_layout_sidecar.py` + 单测（坏数据宽松解析 / 无 bbox→None / provenance 归属）
- **B2** `_code_pipeline` 落盘（失败仅告警）
- **B3** `/code-layout` 路由 + `CodeLayoutPayload` + `CODE_LAYOUT_NOT_FOUND` i18n + 端点测（200 命中 / doc_dir 越界 404 / 无 sidecar 404）
- **F1** schema + `getTaskCodeLayout`（404→undefined）+ 单测
- **F2** `codeLineMagnifier.ts` 纯函数 + 单测（命中 / ±1 并集 / 跨页 / 无 bbox 回退 / 空）
- **F3** CodeViewer 接线（拉 layout + 悬停 + 放大镜 + 缩略图高亮）
- **F4** CSS（编辑栏顶部放大镜条 + 底部缩略图条）
- **F5** 截图视觉验证（强制）

**验收门槛**：单测断言从输入派生（禁写死数据集标识符）；B3 三态端点证据；
F5 截图证明「悬停某行→放大镜显示该行原图局部 + 底部缩略图标出当前图」，至少覆盖一条跨页边界行；
上下游未就绪用 fixture 构造 `SourceFile`（含 `CodeLine.bbox` + provenance）自测，留输入输出证据。

## 10. 不做（防 scope 蔓延）

- 不重跑行级 OCR、不做服务端裁图（纯前端 CSS 放大）。
- 不动文档 / PPT 模式高亮链路。
- 代码模式若将来引入几何预处理 → 再按 processed-image 机制扩展（本期假设原图坐标）。

## 11. 编辑态光标跟随高亮（增量，2026-06-26）

§2「触发」记的是**只读查看器**——无真光标故用鼠标悬停。代码模式**编辑态**（`code-editor-edit-wrap`
里的 `<textarea>`）有真实文本光标，用户要求「编辑光标在哪就高亮哪一行」：让同一放大镜
**随光标行跟随**，并在编辑器行号槽（gutter）标出当前行。

- **统一活动行状态**：原 `hover{path,lineNo}` 改名 `activeLine{path,lineNo}`，语义升为
  「当前活动代码行（来源：只读悬停 *或* 编辑光标）」。只读态由 `handleCodeHover`（mousemove
  取最近 `[data-line]`）写入；编辑态由 `handleEditorCaret` 写入。两态 DOM 互斥（编辑时无
  `.code-content-text`、只读时无 textarea），**不会互相串扰**；放大镜 / 缩略图高亮链路一字不改
  （`<CodeSourceMagnifier>` 本就常驻渲染，原先编辑态因无 mousemove 而停在上次悬停行）。
- **光标行映射**：纯函数 `lineIndexAtOffset(text, offset)` 数 `textarea.selectionStart` 之前的
  换行得 0-based 行内偏移；`displayLineNumber(entry, idx) = line_no_range[0] + idx` 还原成
  OCR `line_no`（== sidecar 键 == 只读 `data-line`），喂 `computeMagnifierRegion` 完全复用。
- **触发事件**：textarea 挂 `onSelect`/`onKeyUp`/`onClick`/`onFocus` 四路都调 `handleEditorCaret`
  （React `onSelect` 已覆盖键鼠光标移动，其余为跨浏览器与测试可靠性兜底；`activeLine` 按
  `(path,lineNo)` 去重，冗余触发零额外重渲染）。
- **行号槽当前行高亮**：编辑 gutter 的当前行号加 `current-line` 类（左强调边 + 微底色），
  直接满足「高亮哪一行」的字面诉求，与诊断波浪线类不冲突。
- **放大镜收高**：用户反馈编辑栏顶部放大条过高，`min(24vh,260px)` → `min(15vh,160px)`
  （viewport 与探测期 min-height 同步）；区域横向恒等宽、缩放不变，只是纵向裁得更紧凑。

**已知局限**：sidecar bbox 锚定原始 OCR 行号；编辑大幅增删行后行号漂移，放大区域随之近似
（与只读悬停同源限制，可接受——放大镜是「定位参考」非「精确定位」）。

**验收**：`lineIndexAtOffset` 单测（空/首/中/尾/越界钳制）；CodeViewer 编辑态光标测
（进编辑→置 `selectionStart`→keyUp→断言 gutter `current-line` 落在期望行 + 该页缩略图 `.active`）；
F5 截图复核（编辑态移动光标→放大镜跟随 + 行号高亮 + 缩略图描边 + 放大条收窄）。

### 11.1 对抗式审查修复（A/B/C，2026-06-26）

落地后跑 4 视角对抗式审查（finder→逐条 verify），6 条全 low、无功能性 bug，收敛为 3 项已修：

- **A 多行选区跟错端**：`handleEditorCaret` 原只读 `selectionStart`（恒为选区低位），forward 下拖
  多行选区时光标（焦点端）在 `selectionEnd` → 高亮/放大跟到选区**顶端**而非光标。修：按
  `selectionDirection` 取焦点端（backward→start，余→end），折叠光标两者相等行为不变。
- **B 跨态幽灵当前行**：只读悬停设 `activeLine` 后点「编辑」（仅翻 `editing`、路径不变）会把上次悬停行
  当「当前行」高亮+放大，而光标尚未落入；textarea 无 autofocus 故 `onFocus` 兜底**不触发**。
  修：`useEffect(()=>setActiveLine(undefined), [editing])` 在态切换时清空，首次真实交互后才点亮。
- **C 当前行配色压住诊断**：`.current-line`（特异性 0,3,0）的 `color/font-weight` 覆盖诊断行号
  `.has-*-diagnostic`（0,2,0）的错误红/依赖黄——光标停在报错行时丢失严重度色。修：`.current-line`
  **只留结构性高亮**（橙左条 `box-shadow` + 微底色），删 `color/font-weight`，诊断配色得以保留
  （截图验证：行号既诊断又当前=红字 + 橙条并存）。
- 新增测：选区 forward→末行 / backward→首行；切到编辑态清空悬停行（无幽灵）首落光标才点亮。

**D 已清理（独立 `refactor(tui)` commit）**：审查另报 **D**——`codePageAnchors` 的 `[data-page]` 锚点在
#93 基线（前序会话撤 `usePreviewScrollSync`）后已无消费方、注释 stale、两测断言惰性 DOM。经全仓 grep
确证代码路径无 `[data-page]`/`useScrollSync` 消费方（消费方全在文档/PPT 路径）后，移除
`CodePageAnchor`/`buildCodePageAnchors`/`clampLineIndex`/`codePageAnchors` useMemo + 只读 overlay span +
`.code-content-text .code-page-anchor` CSS + stale 注释；两测改断言**存活**的源图缩略图（`.source-image-cell`
的 `data-page` 由 SourceImageList 渲染、不依赖 `source_page_ranges`），覆盖未丢。

### 11.2 当前行高亮改整行背景带（2026-06-26，用户反馈）

用户反馈放大镜内的当前行 `BlockHighlightOverlay`：①**描边太粗挡住正文**（边框压在行 bbox 边沿、盖住
首尾字符）；②**有动态缩放效果**（`focus` 取当前行**真实窄框**，行长不同→宽度跳变，叠加 overlay 自身
`transition` 与图层 `transform` 两套动画异步 → 视觉像缩放）。决策：放大镜里改为**整行背景染色带**（不描边）。

- **focus 横向铺满固定行宽**（`codeLineMagnifier.ts`）：`focus` 由「当前行真实 bbox」改为
  `[ref.x0, line.y0, ref.x1, line.y1]`——x 取与 `region` 同一份 `pageXExtent`（当前页行宽并集），y 取
  当前行（或就近回退行）真实纵带。同页内 left/width **恒定**，仅纵向跟随光标 → 行长不再驱动宽度跳变。
- **CSS 改无边框半透明带**（`.code-magnifier-viewport .block-highlight-overlay` 覆盖）：`border:none` +
  `border-radius:0` + `background: rgb(249 115 22 / .2)`（半透明，正文透出）+ `transition:none`。
  `transition:none` 让染色带不再独立动画——它是 `figure-crop-zoom`（`transform 0.25s`）的子元素，按图层
  坐标定位，随图平移**一体贴住**当前行，消除「overlay 0.12s vs 图层 0.25s」异步造成的缩放错位。
  base `.block-highlight-overlay`（Epic E 文档光标高亮共用）**不动**，仅在放大镜作用域覆盖。
- **测**：`computeMagnifierRegion` 短行用例的 `focus` 由 `[10,20,60,40]`（短行真实宽 60）改为
  `[10,20,200,40]`（整行带，铺满全页行宽 200）；harness 旧/新并排截图核对（旧=橙描边贯穿
  `clean(` 文字、右端切词；新=无边框全幅半透明带、`cleaned = clean(text)` 帯越透出可读、横幅到 viewport 端）。
