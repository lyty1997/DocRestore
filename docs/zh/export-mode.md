# 输出导出设计（Epic D：docx / PDF / xlsx / pptx）

> 真相源：本文件。涉及接口/数据模型变更需同步 `docs/zh/backend/data-models.md` 与
> `docs/zh/backend/api.md`，部署依赖变更同步 `docs/zh/deployment.md`，落地进度记
> `docs/zh/progress.md`。
> 关联 issue：Epic D `#73`（D1 `#78` / D2 `#79` / D3 `#80` / D4 `#81` / D5 `#82`）。
> 关联设计：[pdf-mode.md](pdf-mode.md)（输入侧 PDF）、KaTeX 公式渲染 `#77`。

## 1. 背景与目标

DocRestore 当前只输出 **markdown**（`document.md` + `images/`），下载为 zip。Epic D 让用户能把
还原结果再导出成 **Office / PDF** 三方可读格式：

- **docx**（D2）：Word 文档，标题/列表/表格/公式/图片不丢。
- **PDF**（D3）：版式化输出，公式与表格目视正确。
- **xlsx**（D4，Phase-2a）：表格 → Excel 单元格。
- **pptx**（D5，Phase-2a）：PPT 还原模式逐页 → 真幻灯片。

**核心洞察（决定全盘架构）**：docx/PDF 是**已落盘 `document.md` 的纯函数**——
不需要任何 pipeline 中间数据。Epic `#73` 自己的目标也写明「改动收敛在 `output/` + 下载 API 一处」。
故导出**不进 pipeline**，只在**下载环节**按需运行（详见 §3）。

xlsx/pptx 起初被判为「需 pipeline 旁路结构化 IR」，但 Phase-2 勘察（5 路并行只读）+ 4 轮 pandoc
spike 推翻了该前提（详见 §9）：`document.md` 里的表格本就是**结构化 HTML `<table>`**（携带行列与
`colspan/rowspan`），PPT 模式各页以 `\n\n---\n\n` 分隔——两者都能在**下载环节**从 `document.md`
纯函数重建，**无需改 pipeline**。故 D4/D5 与 docx/PDF 同架构落地（Phase-2a）；真正需要 IR 的只剩
「PPT 按 region bbox 精确定位」，延后并入 `#74` 富 IR（Phase-2b）。

## 2. 范围与阶段划分

| 阶段 | 子任务 | 依赖 | 本轮 |
|---|---|---|---|
| **Phase-1** | D1 导出层骨架 | — | ✅ 做 |
| **Phase-1** | D2 docx（pandoc） | D1 | ✅ 做 |
| **Phase-1** | D3 PDF（weasyprint） | D1，关联 `#77` | ✅ 做 |
| **Phase-2a** | D4 xlsx（openpyxl，解析 HTML 表） | D1 | ✅ 做 |
| **Phase-2a** | D5 pptx（python-pptx，逐页一 slide） | D1 | ✅ 做 |
| phase-2b | D5 按 region bbox 精确定位 | PPT 版式 IR（`#74`） | ⏸ 延后 |

**范围约束（已与用户拍板）：**

- **下载时按需导出**，非建任务时勾选（§3 决策）。
- Phase-1 做 **docx + PDF**；Phase-2a 增 **xlsx + pptx**（均下载时纯函数，无 pipeline 改动）。
  唯「PPT 按 bbox 精确定位」需富 IR，延后并入 `#74`（Phase-2b）。
- 导出是**纯本地子进程/库**（pandoc / weasyprint），**无云端出口**——不新增 PII 外泄面
  （`document.md` 已是 PII 脱敏后的最终产物，见 [privacy.md](backend/privacy.md)）。
- 不引入新的互斥处理模式；导出正交于 文档/代码/PPT 三模式与 图片/PDF 两输入源。

## 3. 关键设计决策：下载时按需导出

issue `#78` 原文设想「`OutputConfig` 加 `export_formats` 走请求级覆盖 + TaskForm 多选」。
但子系统勘察（5 路并行只读）揭示该路径要把一个**仅在最末端消费的 list** 穿过 **8 跳链路**：

```
request → model_copy → Task 快照 → DB 列 → process_tree → process_many → _stream_pipeline → Renderer
```

外加 DB schema 变更与表单状态。对照两方案：

| 维度 | **A. 下载时按需（采用）** | B. 建任务时勾选（#78 原文） |
|---|---|---|
| 改动范围 | `output/exporters/` + 下载路由一处 | 8 跳 pipeline + DB 列 + 表单 |
| 回归面 | 小（不碰 pipeline 签名） | 大（每跳签名都要改） |
| 换格式 | 下载区直接换，**不重跑** | 需**重跑整条 OCR+LLM** |
| 何时生成 | 用户真要下载才生成 | 任务完成即预生成（可能没人下） |
| 契合 #73 目标 | ✅「收敛一处」 | ✗ 摊到多模块 |
| 符合 #78 原文 | 偏离（已与用户确认改） | 严格符合 |

**结论：采用 A**。导出是**下载期关注点**，不该污染处理期配置。`export_formats` 不进
`OutputConfig`、不进 `Task`、不进 DB。

**判定：刚刚好**。A 用最小面承载全部 phase-1 能力，且给用户更灵活的「下载时选格式、随时换」
体验；B 是为对齐一句已作废的 issue 文字而付出的过度工程。

## 4. 总体数据流

下载请求 `GET /tasks/{id}/download?formats=docx,pdf` 的处理流水线（单向，无分支聚合）：

1. **路由入口** `download_task_result`（`routes.py:1280`）取 `task` → `output_dir` →
   `doc_dirs`（跳过失败子文档，逻辑不变）。
2. **解析 formats**：查询串按 `,` 拆 → 去空去重 → **fail-closed 白名单**校验
   （phase-1 仅 `{docx, pdf}`；未知值 → `400 ApiBusinessError(EXPORT_FORMAT_UNSUPPORTED)`）。
   空 formats → 退化为现状（纯 markdown zip），**零行为变化**。
3. **逐 doc_dir 导出**：对每个 `doc_dir`（单文档=根），对每个 fmt：
   - 命中缓存（`.exports/{sha256(document.md)}.{ext}`）→ 复用；
   - 否则 `asyncio.to_thread(exporter.export, document.md, images/, out_path)` 生成并落缓存。
   - 缺外部依赖（pandoc/weasyprint）→ `503 ApiBusinessError(*_UNAVAILABLE)`。
4. **装配 zip**：原有 `_build_result_zip_bytes` 的 `document.md` + `images/` 不变，
   **额外**把每个导出产物以干净 arcname（`{doc_dir}/document.{ext}` 或根 `document.{ext}`）写入 zip。
5. **返回**：`media_type=application/zip`，文件名不变。

> 设计取舍：始终以 **zip 为信封**（即便只选一个格式），因多文档任务会产出多个 `document.docx`，
> 单文件下载语义不成立。这也与 `#78` 验收「产物进下载 zip」一致。

## 5. 模块设计

### 5.1 exporter 协议与注册表（`output/exporters/`）

新增包 `backend/docrestore/output/exporters/`：

```
output/exporters/
├── __init__.py        # EXPORTERS 注册表 + get_exporter() + SUPPORTED_FORMATS
├── base.py            # Exporter 协议 + ExportError + 子进程/缓存工具
├── docx.py            # DocxExporter（D2，pandoc）
├── pdf.py             # PdfExporter（D3，weasyprint）
├── mathrender.py      # D3 公式 KaTeX 预渲染
├── html_table.py      # HTML <table> 解析公共件（xlsx/pptx 共用：网格 + 合并区）
├── xlsx.py            # XlsxExporter（D4，HTML <table> → openpyxl，每表一 sheet）
└── pptx.py            # PptxExporter（D5，逐页 → python-pptx，块级竖排 + 原生表格）
```

`Exporter` 协议（与 `#78` 对齐）：

```python
class Exporter(Protocol):
    suffix: str  # "docx" / "pdf"
    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None: ...
    def ensure_available(self) -> None:  # 缺依赖抛 ExportToolUnavailable
```

- `EXPORTERS: dict[str, Exporter]`，`get_exporter(fmt)`；`SUPPORTED_FORMATS = frozenset(EXPORTERS)`。
- Phase-2a 已追加 `xlsx.py` / `pptx.py`：实现协议 + 注册表各加一行，**下载路由零改**
  （`SUPPORTED_FORMATS` 白名单自动纳入）。

### 5.2 下载路由集成

`download_task_result` 增可选查询参数 `formats: str | None = None`：

- 解析 + 白名单校验集中到新函数 `_parse_export_formats(raw) -> list[str]`（fail-closed）。
- 新增 `_build_result_zip_bytes(output_dir, doc_dirs, *, export_formats)` —— 现签名加**关键字**
  参数，默认空 → 完全兼容现有调用与测试。
- 导出循环在 zip 装配**前**完成（产物落 `.exports/`），再由 `_add_doc_to_zip` 追加进 zip。
- 因 exporter 是阻塞子进程/IO，**整段导出包在 `asyncio.to_thread`**（下载路由是 async，
  当前 zip 装配是同步直跑，导出会显著拉长——必须 offload）。

### 5.3 导出缓存（`.exports/`）

- 每个 `doc_dir` 下建 `.exports/` 子目录；产物名 = `{sha256(document.md 内容)[:16]}.{ext}`。
- 缓存键只取 `document.md` 内容哈希（images 变化必然引起 markdown 内引用或 mtime 变化，
  且导出仅嵌入被引用图；保守起见哈希可叠加 `images/` 列表+mtime，phase-1 先只哈希 md，
  附 §10 回归用例守护）。
- `.exports/` **不进** asset 白名单（`_validate_asset_path` 不放行），**不**作为裸文件重打进 zip
  （只有被选中的产物以 `document.{ext}` 名进 zip）；避免缓存目录泄漏。
- 复用 `path_guard` 语义：产物路径必须 `is_relative_to(output_dir)`（理论恒真，防御性断言）。

### 5.4 外部依赖调用与 fail-closed

复用 `processing/code_diagnostics.py::_run_command`（`954-990`）的子进程纪律：

- `subprocess.run`/`Popen` + `start_new_session=True`；`preexec_fn` 设 `RLIMIT_CPU/DATA/FSIZE`；
  `timeout` via `communicate()`；超时 `killpg(pgid)` → `kill()` fallback；`PIPE` 必 drain。
- pandoc 是**二进制**：`shutil.which("pandoc")`，缺失抛 `ExportToolUnavailable("pandoc")`。
- weasyprint 是**Python 库**：`import weasyprint` 失败（ImportError / 缺 cairo/pango 系统库的
  OSError）→ `ExportToolUnavailable("weasyprint")`。
- exporter 只从**最终** `output_dir/{doc_dir}/document.md` + `images/` 读（renderer 已重写并解析，
  无 symlink 越界风险）；**不**碰 `{stem}_OCR/` 中间目录。

### 5.5 错误码与 i18n

`api/errors.py::APIErrorCode`（StrEnum）新增，并同步前端 `errors.*` i18n（三语）：

| code | HTTP | 含义 |
|---|---|---|
| `EXPORT_FORMAT_UNSUPPORTED` | 400 | formats 含未知/未启用格式 |
| `EXPORT_TOOL_UNAVAILABLE` | 503 | pandoc/weasyprint 缺失（`params={tool}`） |
| `EXPORT_FAILED` | 500 | 导出子进程非零退出/异常（`params={tool, format}`） |

## 6. D2 docx（pandoc，两遍 HTML 中转）

`document.md` → `document.docx`，链路 **md → HTML5 → docx**：

```text
document.md --pandoc(-f gfm+tex_math_dollars -t html5 --mathml)--> 中间 HTML（落临时目录）
中间 HTML --pandoc(-f html -t docx, --resource-path=doc_dir)--> document.docx
```

- **为何两遍**（2026-06-23 修复）：`document.md` 的表格**一律是 HTML `<table>`**、配图常含
  HTML `<img>`。pandoc 的 markdown(gfm) reader 把这些原始 HTML 当 `RawBlock html` 保留，而
  **docx writer 直接丢弃原始 HTML** —— 单遍 `gfm → docx` 会让表格与 HTML 图片全部消失
  （仅 `![]()` 图片侥幸保留）。HTML 是「能吃原始 HTML」的通用中间态（与 D3 PDF 同源）：
  第二遍的 HTML reader 把 `<table>/<img>` 解析成 pandoc 原生表格/图片 → docx writer 正常渲染。
- **`--mathml` 而非 `--mathjax`**（关键）：`--mathjax` 产 `\(..\)`，HTML reader 不再解析回数学
  （OMML 丢失、留下字面 `\(..\)`）；`--mathml` 产 `<math>`，HTML reader 原生识别 → **OMML**（Word 公式）。
- `--resource-path=<doc_dir>`（两遍都带）：中间 HTML 落独立临时目录，靠 resource-path 定位
  `images/` 相对引用，两遍都能嵌图。
- pandoc 是外部二进制（~150MB）：`docs/zh/deployment.md` 写明安装；缺失 fail-closed（§5.4）。

### 6.1 验收（D2）

构造含 标题/`$E=mc^2$`/**HTML `<table>`**/markdown 图片 + **HTML `<img>`** 的 `document.md`
（据实——真实 `document.md` 的表是 HTML 表，不是 GFM 管道表）→ 导出 docx → 用 `python-docx`
读回，断言：①**从输入派生**的标题文字落正文；②派生单元格文字落 `document.tables`；③`oMath`
（公式转 OMML）在；④`inline_shapes >= 2`（md 与 HTML 两张图都嵌入）。**禁止写死数据集关键词**。

## 7. D3 PDF（weasyprint + 公式）

`document.md` → `document.pdf`，链路 **md → HTML → PDF**：

```text
document.md --pandoc(-t html5 --mathjax, 保留 \(..\) TeX + <table>)--> HTML
HTML --KaTeX(Node) 预渲染公式 span + 挂 katex.min.css--> 自包含 HTML
自包含 HTML --weasyprint(base_url=doc_dir 解析 images/)--> document.pdf
```

**为何 weasyprint 而非 pandoc→TeX**：我们的 `document.md` 同时含 **HTML 表格**（OCR 产出）
与 **LaTeX 公式**。

| 引擎 | HTML 表格 | LaTeX 公式 | 依赖 |
|---|---|---|---|
| **weasyprint（采用）** | ✅ HTML 原生 | 需先 KaTeX 转 CSS（§7.1） | weasyprint（cairo/pango）+ Node/katex |
| pandoc + TeX(tectonic) | ✗ 易丢/乱 | ✅ 原生 | TeX 工具链（重） |

表格是结构上更难重建的东西、且 OCR 以 HTML 形态产出，故优先保表格 → 选 weasyprint。

### 7.1 公式机制（spike 已定：KaTeX 预渲染）

weasyprint **不跑 JS**，`$...$` 必须先变成静态可排版的标记。D3 实现期 spike 拍板：

**spike 证据（2026-06-23）**：先试 pandoc `--mathml` → weasyprint。结果**保真不足**——
weasyprint 对 MathML 支持太弱：`E=mc^2` 上标丢失（塌成 `mc2`）、分数 `a/b` 无分数线、
`\sqrt` 无根号、`\frac{1}{3}` 塌成 `13`；且 weasyprint 还会把 MathML 里的
`<annotation>`（pandoc 嵌的原始 TeX）一并渲染出来导致**公式重影**。MathML 路否决。

**采用：KaTeX 预渲染（Node）**——与前端 `#77` 同引擎，视觉一致。链路：

1. pandoc `-t html5 **--mathjax**`（**非 `--mathml`**）：保留 `\(..\)` / `\[..\]` 原始 TeX 于
   `<span class="math inline|display">`，并透传 HTML `<table>`。
2. `mathrender.prerender_math`：正则抽 math span 的 TeX → `_katex_render.cjs`（Node）批量
   `katex.renderToString(tex, {output:'html', displayMode})` → 替换回 HTML。
   `output:'html'` **只产 CSS 排版 HTML、不含 katex-mathml**，规避重影。
3. weasyprint 渲染 HTML，附 `katex.min.css` 为样式表（weasyprint 以 CSS 所在目录解析
   `@font-face` 字体）。spike 目视：上标/分数/根号/积分/`pmatrix` 均正确（见 progress.md 截图）。

**依赖代价**：KaTeX 是 JS-only（无 Python 移植），引入 **Node 运行时 + katex 包**
（dev 复用 `frontend/node_modules/katex`，生产见 `deployment.md`）。**仅含公式的文档**才触发
KaTeX/Node：`export()` 探测 `has_math(html)`，无公式则纯 pandoc+weasyprint（不需 Node）；
有公式但 Node/katex 缺失 → `EXPORT_TOOL_UNAVAILABLE`（fail-closed，不静默降级）。

依赖矩阵：

| 文档 | pandoc | weasyprint | Node + katex |
|---|---|---|---|
| docx（D2） | ✅ | — | — |
| pdf 无公式 | ✅ | ✅ | — |
| pdf 含公式 | ✅ | ✅ | ✅ |

### 7.2 验收（D3）

含 公式/HTML 表格/图片 的 `document.md` → PDF：①目视公式与表格正确（截图）；
②`pdfminer`/`pypdf` 抽取文本断言**从输入派生**的关键内容存在。

## 8. 前端：下载 UI 格式选择器

导出在**下载环节**选 → 选择器落在**结果页下载区**，不进 `TaskForm`：

- `TaskResult.tsx` / `TaskDetail.tsx` 下载按钮旁加一组格式勾选（`ZIP`(纯 md) / `Word` / `PDF`），
  默认仅 ZIP。
- `api/client.ts::getDownloadUrl(taskId, formats?)`：`formats` 非空时拼 `?formats=docx,pdf`
  （`URLSearchParams` 构造，不手拼）。token 仍按现状附加。
- 失败（503/400/500）：`<a download>` 直跳无法读 JSON 错误体 → 改为 `fetch` 拿 blob/错误，
  命中 `ApiBusinessError` 走 `LocalizedError` 三语提示（复用 `error_i18n` 链路）。
- i18n：新增 `taskResult.exportFormats` / `taskResult.exportDocx` / `taskResult.exportPdf`
  等键，三语（en/zh-CN/zh-TW 类型同步，缺键编译报错）。

> 不在 `TaskForm` 加多选：导出与处理解耦，表单不背下载期状态（呼应 §3 决策）。

## 9. Phase-2a：D4 xlsx / D5 pptx（下载时纯函数）

Phase-2 勘察（5 路并行只读）+ 4 轮 pandoc spike 推翻了「必须先落 pipeline 结构化 IR」的初判：
xlsx/pptx 同样是 `document.md` 的纯函数，与 docx/PDF 同架构、零 pipeline 改动。

### 9.1 D4 xlsx（openpyxl，解析 HTML `<table>`）

关键事实：`document.md` 里的表格**一律是 HTML `<table>`**（LLM prompt 默认保留 HTML 原样，
`table_dedup.py` 也按 `<tr>/<td>` 去重），**从不产 GFM 管道表**。HTML `<table>` 本身携带行列与
`colspan/rowspan`——**它就是结构化 IR**，无需 pipeline 旁路。

- `xlsx.py` 用 `html.parser` 解析每个 `<table>` → 单元格矩阵 + 合并区（occupancy 算法处理跨行跨列）。
- openpyxl：**每表一 sheet**（`Table 1/2/...`）；纯数字单元格转数值（`"100"`→100）；合并区 `merge_cells`。
- **无表退化**：文档无 `<table>` 时，落单 sheet `Document`，每非空 markdown 行一行（产物非空，便于派生断言）。
- 依赖 `openpyxl`（纯 Python，无系统库）：**懒导入** fail-closed（`ExportToolUnavailable("openpyxl")`），
  与 weasyprint 同范式（注册表启动导入 `xlsx.py`，顶层不 import openpyxl）。

### 9.2 D5 pptx（python-pptx，逐页一 slide）

**spike 决策（关键，2026-06-23）**：先验证 `pandoc -t pptx`——可原生切片 + `$..$`→OMML，但有
**硬限制**：pandoc 的 pptx 写法**无法让文字与图片同处一张 slide**（块级图片必单独成页、内联图片被丢，
4 轮 spike 实证）。屏摄 PPT 页页有图，纯 pandoc 路线只能「图各自成页（slide 膨胀）」或「丢图」，
均不满足 `#82`「每页一 slide、图文在一起」。**改用 python-pptx 自拼页**（与用户确认）。

- **切页**：按 `\n\n---\n\n`（PPT 模式页分隔）切；doc 模式无 `---` 时回退按顶层 `#` 切；都没有则整篇一页。
- **按块解析**（2026-06-23 修复）：早期版本把整行文本直接塞文本框，导致 `document.md` 里的 HTML
  `<table>` / `<div>` 原始标记**当字面文本漏到 slide 上**（用户可见一长串 `<table border=1 ...>`）。
  现把一页拆成**有序块**：`<table>` → 复用 §9.1 同一解析层（`html_table.py`）渲染成**原生 pptx
  表格**（含合并区）；图片（`![]()` / `<img>`）→ 独立图片块；块之间的散文**剥掉 HTML 标签只留文本**
  （`<br>`/块级闭合 → 换行，其余标签删，实体解码）。
- **每页一 slide**（blank 版式自绘）：首个标题→slide 标题框；正文/表格/图片**按文档顺序竖向堆叠**
  （游标式排版，剩余高度不足时给兜底高度、宁可轻微出血也不丢内容）；`$..$` 公式留 TeX 文本不渲染（lite 取舍）。
- **公共解析层** `html_table.py`：从 `xlsx.py` 抽出 HTML 表解析（`parse_tables`/`parse_one_table`/
  `build_grid`/`grid_dimensions`），xlsx（每表一 sheet）与 pptx（每表一原生表格）共用，单一真相源。
- 依赖 `python-pptx`（纯 Python）：**懒导入** fail-closed（`ExportToolUnavailable("python-pptx")`）。

### 9.3 PPT 版面定位（Phase-2b，实现中）

「PPT 按 region bbox 精确定位文本框/图片/表格」**已开工**（早期判「延后」的「bbox 永久丢失/
per-region 文本不干净」前提经 spike 证伪——VL 的 `coordinates` 在 OCR 源头就带干净分区）。设计与
进度真相源见 [`ppt-layout-export.md`](ppt-layout-export.md)。落地概览：
- **捕获**（子任务1）：`PageOCR.layout_regions`（`models.py`）+ `paddle_ocr._build_layout_regions`
  从 VL `coordinates` 接住 bbox+类型+内容，image/chart 按阅读序认领 `<img>`。
- **落盘 + 坐标变换**（子任务2）：`output/ppt_layout.py`（`.ppt_layout.json` 位置真相源 +
  像素 bbox→slide EMU letterbox 变换）；`_ppt_pipeline::_write_ppt_layout_sidecar` 装配后落盘
  （文字过同一 PII 闸口）。
- **定位渲染**（子任务3）：`pptx.py::_build_presentation` 分发——sidecar 合法 → 按 bbox 定位渲染
  （文本框/原生表/图），任一异常或某页无可用区域 → fail-safe 退竖排块流（§9.2），零回归。
- **开精修**（子任务4，待做）：区域单元 idx 锚点精修 + 按 idx 重挂 bbox。

## 10. 测试策略

遵守 CLAUDE.md：**从输入派生断言**，禁止写死数据集标识符/关键词。

| 层 | 用例 | 隔离 |
|---|---|---|
| `_parse_export_formats` | 空/合法/未知/重复/大小写 → fail-closed | 纯单测 |
| exporter 注册表 | `get_exporter` / `SUPPORTED_FORMATS` | 纯单测 |
| 缺依赖 fail-closed | monkeypatch `shutil.which`→None / 模拟 ImportError → `EXPORT_TOOL_UNAVAILABLE` | mock |
| D2 docx | 构造 md → 导出 → `python-docx` 读回派生关键文本 | 需 pandoc，缺则 skip |
| D3 pdf | 构造 md → 导出 → `pypdf` 抽取派生关键文本 + 目视 | 需 weasyprint，缺则 skip |
| 下载路由 | `?formats=` 各组合 → zip 内含 `document.{ext}`；空 formats 行为不变 | TestClient |
| 前端 | 选择器渲染 + `getDownloadUrl` 拼参 + 错误本地化 | vitest |

外部工具缺失时**skip 而非 fail**（CI 可能无 pandoc/weasyprint），但本地交付前必须实跑出证据。

## 11. 过度/欠工程判定

- **下载时按需（§3）**：刚刚好。最小面承载全部 phase-1，避免 8 跳透传的过度工程。
- **exporter 协议+注册表（§5.1）**：刚刚好。phase-2 加格式只动注册表，下载路由稳定；
  若 phase-1 写死 if/else 才是欠工程（D4/D5 会逼着重构）。
- **缓存（§5.3）**：刚刚好偏保守。导出可重复触发（每次下载），无缓存则每次重跑子进程；
  只哈希 md 是 phase-1 够用的最简键。
- **公式机制 spike（§7.1）**：刚刚好。不预先背 Node 依赖，用证据决定，D1/D2 不被阻塞。
- **D4 xlsx 解析 HTML 表（§9.1）**：刚刚好。HTML `<table>` 已是结构化 IR，下载时解析即可；
  按初判去 pipeline 造表格 IR 才是过度工程（被 5 路勘察证伪）。
- **D5 python-pptx 自拼页（§9.2）**：刚刚好。pandoc 满足不了「图文同页」是 spike 实证的硬限制，
  退回纯 pandoc 是欠工程（丢图/膨胀）；现在就做 bbox 精确定位是过度工程（需富 IR，并入 `#74`）。
- **Phase-2b 仅留旁路点（§9.3）**：刚刚好。不提前造 IR（YAGNI），但记清落点避免将来盲改。

## 12. 验收清单（Phase-1）

- [ ] D1：`?formats=docx`（先 stub passthrough）→ 下载 zip 含该产物；空 formats 行为不变。
- [ ] D1：未知 format → 400；缺依赖 → 503；三语错误提示。
- [ ] D2：派生关键文本 docx round-trip 通过；缺 pandoc skip + 部署文档写明。
- [ ] D3：公式/表格/图片 PDF 目视正确 + 抽取文本断言；公式机制择一记档。
- [ ] 前端：下载区格式选择器 + 错误本地化 + vitest 绿。
- [ ] `bash scripts/check_quality.sh` 全绿；progress.md / memory / deployment.md 更新。
- [ ] 按依赖序关 `#78`/`#79`/`#80`，Epic `#73` 勾选 phase-1。

## 13. 验收清单（Phase-2a：D4/D5）

- [ ] D4：含 `<table>`（带 `colspan/rowspan`）的 md → xlsx，openpyxl 读回派生单元格文字 + 合并区
      + 数值单元格为数字；无表 → 单 `Document` sheet 含派生正文行；缺 openpyxl skip + 部署写明。
- [ ] D5：2 页（标题/正文/图片/公式）md → pptx，python-pptx 读回 slide 数=页数、各页标题派生文字、
      图片嵌入、公式 TeX 文本在；`<img>` 与 `![]()` 均解析；缺 python-pptx skip。
- [ ] 注册表加 `xlsx`/`pptx`；下载 `?formats=xlsx,pptx` 进 zip；前端 4 格式选择器 + 三语标签。
- [ ] `bash scripts/check_quality.sh` 全绿；deployment.md 依赖矩阵 + progress.md + memory 更新。
- [ ] 关 `#81`/`#82`，Epic `#73` 勾选 D4/D5（`#74` bbox 精确定位另立 Phase-2b）。

