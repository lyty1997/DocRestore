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
- **xlsx**（D4，phase-2）：表格 → Excel 单元格。
- **pptx**（D5，phase-2）：PPT 还原模式逐页 → 真幻灯片。

**核心洞察（决定全盘架构）**：docx/PDF 是**已落盘 `document.md` 的纯函数**——
不需要任何 pipeline 中间数据。Epic `#73` 自己的目标也写明「改动收敛在 `output/` + 下载 API 一处」。
故导出**不进 pipeline**，只在**下载环节**按需运行（详见 §3）。

xlsx/pptx 则不同：markdown 字符串无法可靠重建合并单元格 / 逐页版式，需要 pipeline
**旁路落结构化 IR**（当前 PPT 逐页 `bbox` 在 `pipeline.py:1389` 被压扁成单串 markdown、
表格是不透明 `<table>` 文本）。这正是它们被标 phase-2 的原因（详见 §9）。

## 2. 范围与阶段划分

| 阶段 | 子任务 | 依赖 | 本轮 |
|---|---|---|---|
| **Phase-1** | D1 导出层骨架 | — | ✅ 做 |
| **Phase-1** | D2 docx（pandoc） | D1 | ✅ 做 |
| **Phase-1** | D3 PDF（weasyprint） | D1，关联 `#77` | ✅ 做 |
| phase-2 | D4 xlsx（openpyxl） | D1 + 表格结构化 IR | ⏸ 延后 |
| phase-2 | D5 pptx（python-pptx） | D1 + PPT 版式 IR | ⏸ 延后 |

**范围约束（已与用户拍板）：**

- **下载时按需导出**，非建任务时勾选（§3 决策）。
- 本轮只做 **docx + PDF**；xlsx/pptx 延后到 IR 旁路就绪。
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
├── docx.py            # PandocDocxExporter（D2）
└── pdf.py             # WeasyPrintPdfExporter（D3）
```

`Exporter` 协议（与 `#78` 对齐）：

```python
class Exporter(Protocol):
    suffix: str  # "docx" / "pdf"
    def export(self, doc_md: Path, assets_dir: Path, out_path: Path) -> None: ...
    def ensure_available(self) -> None:  # 缺依赖抛 ExportToolUnavailable
```

- `EXPORTERS: dict[str, Exporter]`，`get_exporter(fmt)`；`SUPPORTED_FORMATS = frozenset(EXPORTERS)`。
- phase-2 在此追加 `xlsx.py` / `pptx.py`，注册表加两行即可，**下载路由零改**。

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

## 6. D2 docx（pandoc）

`document.md` → `document.docx`：

```
pandoc <doc.md> -f gfm+tex_math_dollars -o <out.docx> --resource-path=<doc_dir>
```

- `-f gfm+tex_math_dollars`：按 GitHub-Flavored Markdown 解析，且把 `$...$` 识别为 TeX 数学
  → pandoc 原生转 **OMML**（Word 公式），无需 KaTeX。
- `--resource-path=<doc_dir>`：让 `images/{stem}_N.jpg` 相对引用能被 pandoc 解析嵌入。
- HTML `<table>`（OCR 产出）：pandoc 的 GFM reader 能吃原始 HTML 块并转 Word 表格。
- pandoc 是外部二进制（~150MB）：`docs/zh/deployment.md` 写明安装；缺失 fail-closed（§5.4）。

### 6.1 验收（D2）

构造含 标题/列表/GFM 表格/`$E=mc^2$`/图片引用 的 `document.md` → 导出 docx →
用 `python-docx` 读回，断言**从输入派生**的关键文本（标题文字、表格单元格文字）存在，
公式段非空。**禁止写死数据集关键词**（CLAUDE.md 测试规则）。

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

## 9. Phase-2 预留（D4/D5，本轮不实现）

仅记录将来的旁路点，**本轮不写代码、不改 pipeline**：

- **D4 xlsx**：需表格**行列结构化 IR**。当前 `<table>` 是不透明文本（`table_dedup.py` 也只按
  文本签名去重）。旁路点：`_finalize_single_doc`（`pipeline.py:2261`，dedup 之后）解析剩余
  `<table>` → `{rows, cols, cells}` IR，挂 `PipelineResult.layout_ir`（新增 `dict|None` sidecar）。
  注意：OCR 无 cell 级 bbox，IR 只到「行列+单元格内容」粒度。
- **D5 pptx**：需**逐页版式 IR**。当前 `_ppt_pipeline`（`pipeline.py:1227-1391`）把
  `ordered_pages` 各页 `regions` 在 `1389` 行压扁成单 list、版式随 markdown 合并丢失。旁路点：
  `render_ppt_document` 调用前（页信息尚在 `ordered_pages`），按页抽 `layout_ir['pages']`。
- 两者共用 `PipelineResult.layout_ir` sidecar（非破坏性 `None` 默认），与 `#74 E1` 富 IR 工作相关，
  IR schema 需先对齐再实现。

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
- **phase-2 仅留旁路点（§9）**：刚刚好。不提前造 IR（YAGNI），但记清落点避免将来盲改。

## 12. 验收清单（Phase-1）

- [ ] D1：`?formats=docx`（先 stub passthrough）→ 下载 zip 含该产物；空 formats 行为不变。
- [ ] D1：未知 format → 400；缺依赖 → 503；三语错误提示。
- [ ] D2：派生关键文本 docx round-trip 通过；缺 pandoc skip + 部署文档写明。
- [ ] D3：公式/表格/图片 PDF 目视正确 + 抽取文本断言；公式机制择一记档。
- [ ] 前端：下载区格式选择器 + 错误本地化 + vitest 绿。
- [ ] `bash scripts/check_quality.sh` 全绿；progress.md / memory / deployment.md 更新。
- [ ] 按依赖序关 `#78`/`#79`/`#80`，Epic `#73` 勾选 phase-1。

