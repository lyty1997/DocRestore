# 开发进度

## 2026-06-18 - Epic A A2（#76 前端 PDF 输入）落地 → Epic A 收口

按设计 `docs/zh/pdf-mode.md` 实现 A2 前端，分支 `feature/s-a1-pdf-render`，提交 `019f97d`：

- **新建 `frontend/src/features/task/fileKind.ts`**：纯逻辑类别判定模块
  （`fileKind` / `isPdfFilename` / `isAcceptedFilename` / `classifySelection`，导出
  `IMAGE_EXTENSIONS` / `PDF_EXTENSION` / `ALLOWED_EXTENSIONS` / `ACCEPT_ATTR`），便于单测复用。
- **`FileUploader.tsx`**：`accept` 加 `application/pdf`、白名单加 `.pdf`；选择时
  **互斥预校验**（图片与 PDF 混选→红框提示且不发起上传，对应后端 D6 闸一/闸二的客户端前置）；
  「选择图片文件」按钮文案改「选择文件」。
- **`UploadPreviewPanel.tsx`**：`.pdf` 不能 `<img>`，改渲染占位卡——`<a target=_blank>`
  PDF 角标，点击新标签页打开；图片仍走原 `<img>` + lightbox。
- **i18n 三语**：`fileUploader.fileTypeHint`（含 PDF + 互斥说明）/ 新增
  `fileUploader.mixedInputError` / `uploadPreview.pdfDocument` / `noImages` 文案泛化。
- **`App.css`**：`.upload-preview-pdf` 占位卡（4:3 同图片缩略图）+ `.upload-mixed-warning` 红框。

**证据/门禁**：新增 3 个测试文件共 14 例（`fileKind.test.ts` 10 + `FileUploader.test.tsx` 2
+ `UploadPreviewPanel.test.tsx` 2），frontend vitest 135 passed；
`check_quality.sh` EXIT=0（mypy / ruff / typos / 前端 typecheck + lint / 后端 pytest 1392 passed）。
**视觉验证**（Playwright + dev server + 真实后端 insecure 回环）：①空闲态按钮+PDF/互斥提示
②混选红框告警 ③真实上传单 PDF→预览面板 PDF 占位卡渲染正确，三张截图均已核对（验证产物已清理未入库）。

**Epic A（#72）收口**：A1 后端 + A2 前端全部落地，PDF 输入端到端打通
（上传 PDF → 摄取入口逐页渲染 → 复用 OCR→去重→精修 → 一 PDF 一文档）。
**遗留**：本分支累计 7 commit 待合 dev（届时 release PR 收口 `Fixes #72 #75 #76`）；
后续 Epic 未开工——C1 `#77`（公式渲染）/ Epic D `#73` / Epic E `#74`。

## 2026-06-18 - Epic A A1（#75 后端 PDF 输入）落地

按设计 `docs/zh/pdf-mode.md` 实现 A1 后端，分支 `feature/s-a1-pdf-render`，3 个有证据闭环：

- **A1-①**（`ab41422`）：新建 `backend/docrestore/pipeline/render/pdf.py`
  `render_pdf_to_dir`（pypdfium2 逐页渲染 RGB PNG）——幂等 sentinel `.render_done.json`
  / 坏页跳过 / 损坏 PDF 上浮 / `max_pages` 截断 / `max_long_side` 降采样 / `safe_pdf_stem`
  净化 / 零填充命名自动加宽；新增 `PdfRenderConfig`（`PipelineConfig.pdf`，仅服务端默认）；
  `pyproject.toml` 加 pypdfium2 + mypy override。单测 10 例。
- **A1-②**（`536759e`）：`process_tree` 摄取入口插 `_expand_pdfs`（单 PDF 落根命中
  `process_many` 快路 / 多 PDF 分子目录）；坏 PDF 转占位失败合入复用部分失败聚合；
  抽 `_process_subdirs` 化解 C901；content_crop 对 PDF 渲染页跳过（sentinel 判定 D8）；
  pypdfium2 改懒加载。集成测 5 例。
- **A1-③**（`332bb3d`）：上传 `.pdf` 放行 + PDF 200MB 按 ext 分流；全图片 xor 全 PDF
  互斥双闸（upload_files 闸一 + create_task `_has_mixed_input` 闸二）。测试 6 例。

**证据/门禁**：每步过 `check_quality` 等价检查；最终 mypy 78 文件 0 错 / ruff / typos /
pytest 1392 passed, 0 failed。过程坑：Pillow JPEG 懒加载（已入 known-issues）；
ruff hook 比项目 gate 严（ASYNC240/C901 项目 ignore 但 hook 拦，按本文件惯例 to_thread + 抽函数解决）。

**遗留**：~~A2（#76 前端）未开工~~ **已落地（见本文件顶部 2026-06-18 A2 条目）**。

## 2026-06-17 - Epic A（PDF 输入）设计定稿

**背景**：MinerU vs PaddleOCR-VL benchmark 结论「不引入 MinerU、维持 PaddleOCR-VL」
（见 `output/bench/quality/report.md`）后，基于现状设计 6 个 feature 并建 GitHub issue 树
（Epic A `#72` / C1 `#77` / Epic D `#73` / Epic E `#74`，14 个 issue）。本次完成 **Epic A
（PDF 输入）设计**。

**方法**：ultracode workflow 并行 7 子系统只读核查 → 综合设计 → 对抗式挑刺（9 agent）。
挑刺揪出 6 处必补欠工程，全部吸收进设计。

**设计定稿**（`docs/zh/pdf-mode.md`，用户确认 Q1–Q5）：
- 架构：**摄取入口渲染**（非上传时）、**单 PDF 落 `image_dir` 根命中 `process_many` 快路、
  多 PDF 分 `{stem}/` 子目录**；复用 `process_tree` 多文档 / `IncrementalMerger` 跨页 /
  source-images 锚点三套机制，核心流式链路一行不动。引擎 `pypdfium2`（Apache，bench 已验证）。
- 6 处必补：D2 渲染幂等（`.render_done.json` sentinel，防 resume 重渲染+缓存键漂移）/
  D4 `{stem}_page_NNNN.png` 前缀强制（防多 PDF basename 串页）/ D5 `pdf_stem` 安全净化 /
  D6 互斥双闸（`create_task` 兜底闸从零新增）/ D8 content_crop 对 PDF 默认关 / D9 长边上限。
- 确认项：content_crop 默认关 / PDF 上限 200MB / max_pages 500 截断+警告 /
  配置仅服务端默认 / zip 不打源 PDF。

**产出**：`docs/zh/pdf-mode.md`（含 PlantUML 数据流图，已编译验证 exit 0）；
`architecture.md` §6.3 + `README.md` 索引同步。

**遗留**：A1（`#75` 后端）/ A2（`#76` 前端）实现待开工；建议先闭环 A1 自包含核心
（`render/pdf.py` + `PdfRenderConfig` + 单测，Pillow 造多页 PDF fixture）再做 pipeline 接入与前端。

## 2026-06-03 - 统一 LLM 精修开关 + PPT 按页精修（替代失效的 llm_polish）+ code-review

**背景**：对 PPT 还原模式（AGE-83，S0–S6 刚合并 dev）做了一轮 max-effort code-review，
随后用户实测反馈 **PPT 模式的 `llm_polish` 无效**——`_ppt_pipeline` 只记 warning 占位、
从未真正调用精修器。用户要求：**所有模式用同一个 LLM 精修开关统一控制是否精修，
PPT 模式不再单独设功能**。

**设计决策**（用户确认）：精修默认**开**（保持文档/代码模式现状）；文档模式=分段精修
（与 OCR 并行），**PPT 模式=按页精修**（同样在出队循环内与 producer OCR 重叠）。

**改动**：
- `LLMConfig.enable_refine: bool = True`（统一开关）；删除 `PowerPointRestoreConfig.llm_polish`。
- `Pipeline._get_refiner` 单点拦截：有效 `enable_refine=False` → 返回 None，文档/代码/PPT
  既有 `if refiner is None: 跳过` 路径统一生效；`initialize` 同步加守卫。
- `_ppt_pipeline` 重构：逐页出队 → `rewrite_image_refs_to_ocr_dir` → 按页 `_refine_segment_with_cache`
  （复用文档段级精修的缓存+截断兜底）→ 单页保序组装；签名改为 `(queue, output_dir, report_fn, *, llm, total, quality)`。
- `render_ppt_document` 加可选 `bodies` 参数（按页预精修正文，None 时维持内部 rewrite）。
- API：`LLMConfigRequest.enable_refine`；删 `PowerPointRestoreConfigRequest.llm_polish`；client.ts/useTaskRunner 同步。
- 前端：删 PPT 专属润色 toggle + `pptPolish`，新增独立「LLM 精修」单一 toggle（默认开，全模式生效）→ `llm.enable_refine`；三语 i18n（`refineTitle/refineDesc` + `progress.pptPage`，删 `pptPolishLabel`）。
- 顺带修 review 发现的 PPT 重试丢配置：`retry_task`/`resume_task` 转发 `ppt=task.ppt`，`get_task_async` 补 `code=row.code`。

**验证**：`bash scripts/check_quality.sh` 全绿（mypy --strict 66 文件 0 错 / ruff / typos /
前端 tsc + eslint / pytest 998 passed, 45 skipped）。新增 `tests/pipeline/test_ppt_refine.py`
（按页精修保序 + 统一开关关闭跳过）+ `test_ppt_renderer.py` 加 bodies 用例。设计文档
`ppt-mode.md`（含 §15 变更记录 + PlantUML 重编译验证）/ `architecture.md` 同步。

**review bug 跟踪（4 个全修复，用户确认）**：① ✅ `slide_rectify.rectify()` numpy view 别名导致 height
重复乘 `(1+ratio)`、矫正图竖向拉伸 ~20%（角点取 .copy() 再外扩 + 回归测试）；② ✅ PPT 强制 VL（新增
`_ocr_config_for_ppt_mode` + `_ocr_config_for_mode` 分派器，仿代码模式强制 basic；官方文档结论：PPT 还原所需
markdown+LaTeX+裁图+阅读序只有 PaddleOCR-VL 能产，非质量对比是能力匹配，故不做 4 路 bake-off）；③ ✅
`_rectify_sync` 落盘段整体包 try/except(OSError,cv2.error) 回退原图，兑现"任何失败回退原图"契约 + 回归测试；
④ ✅ `_order_corners` 旋转四边形角点塌缩（改极角排环 + 4 角互异测试）。质量门禁全绿（pytest 1001 passed, 45 skipped）。


## 2026-06-02 - PaddleOCR-VL 1.5 → 1.6 全面升级（废弃 1.5）+ 12G 显存 OOM 修复

**背景**：PPT 还原 spike（AGE-84）选定 VL-1.6 为主引擎后，进一步对比 1.5 vs 1.6——化学结构页两者≈等价
（1.6 在视觉折行处把正文断成两段、略差），但**表格页 1.6 明显胜**：表结构 7 列正确（1.5 错乱成 8 列 +
重复 Organism 列 + 幻影 `$c$` 列），实体 ID OCR 也更准（P31116 vs P3I116）。故全面升级、废弃 1.5。

**环境**（分支 `feature/vl16-upgrade`）：`ppocr_vlm`（server）+ `ppocr_client`（client/worker）均
`pip -U paddleocr[doc-parser]` 从 3.4.0 升 **3.6.0**（vllm 0.10.2 / torch 2.8-cu128 / flash_attn 2.8.2 复用不重编）；
临时验证 env `ppocr_vlm16` 用后删除。

**代码改动**：
- commit `86717e6`：`OCRConfig.paddle_server_model_name`→1.6 + 新增 `paddle_pipeline_version="v1.6"`；
  `paddle_ocr.py` init_cmd 透传 pipeline_version；`paddle_ocr_worker.py` `PaddleOCRVL(pipeline_version=...)`；
  start.sh / run_e2e / bench / docs(zh+en) 全部 1.5→1.6。
- commit `38530e9`（OOM 修复）：paddleocr 3.6 的 VL-1.6 在 12G 卡开 CUDA graph 会 `No available memory for the
  cache blocks`。新增 `backend/docrestore/resources/ppocr_vl_backend.yaml`（`enforce_eager: true` +
  `gpu_memory_utilization: 0.92`），`OCRConfig.paddle_server_backend_config` 默认指向它，`start.sh` 透传
  `--backend_config`（`PPOCR_BACKEND_CONFIG` 可覆盖/置空——≥16G 显存换 CUDA graph 提速）。

**验证（e2e 通过）**：VL-1.6 server 带 backend_config 正常启动（不再 OOM）；文档模式
`PaddleOCRVL(pipeline_version="v1.6", vllm-server)` 从 ppocr_client 出 markdown（472 字符、内容正确）；
代码模式 basic PP-OCRv5 在 3.6 出 28 行行级结果。`tests/` 无 1.5 残留，pre-commit（mypy/ruff/typos）全过。

**架构澄清**：VL-1.6 server（ppocr_vlm vllm）**文档模式 + PPT 模式共用**；代码模式走 basic PP-OCRv5
**纯本地**（ppocr_client，不起 server）。

**遗留**：① `feature/vl16-upgrade` 分支未合并 dev（待 review/merge）；② `paddle_pipeline_version` 新字段的
docs/backend 说明待补；③ PPT 还原模式本体（S1 设计 AGE-85 起）待开工。


## 2026-05-31 - 代码模式碎片化诊断 + 跨页归类重构设计（方案 1+4，已确认待实现）

**背景**：chromium 显示子系统代码数据集（157 张 IDE 截图）跑代码模式，本应收敛成 **8 个真实源文件**，
实际产出 **16 个**，半数是从真实文件掉下来的「幽灵碎片」（`ui/g/`、`giesz.cc`、`c/gl_surface_egl.h`、
双下划线 `__gles2.h`、`gpu_mojo/media/client/linux.cc` 等）。

**诊断（已对照原图 + `text_lines.jsonl` 中间结果坐标确认）**：代码正文 OCR 没问题，崩在「从 IDE 界面壳子
（标题栏/标签/面包屑）反推文件名+路径」这条零容错元数据链——三层防线全被 OCR 噪声击穿：①面包屑「唯一真相」
本身被污染（丢点 `gles2.h`→`gles2h`、漏字符 `gl`→`g`、图标 `C`→目录 `c`、文件名碎块当多级目录）；
②标签兜底抓到灰色 preview 标签（OCR 看不到高亮、`×` active 正则脆）+ 窗口标题噪声过滤脆（`-src[`→`-sic[`）；
③`_merge_near_duplicate_filenames` 开口太窄（精确 dir 分桶 + 10% 比例硬闸把 18% 双下划线变体判成独立文件）。

**设计产出**：`docs/zh/backend/processing.md` **§3.6**（含 2 张已 `java -jar plantuml.jar` 真编译验证的活动图；
按用户要求直接写进代码模式章节，不另起 references 文档）。核心原则「行号+行内容 > 文件名」：文件名（方案 1 清洗后）
只提候选，行号重合区内容一致性裁决归属。四 Stage——S0 每页行账本完整性校验（保证源干净）→ S1 batch 文件名归一
（高置信词表 snap 碎片）→ S2 行号锚定跨页归类（重合区内容一致性三分支 + garbage 碎片跨桶救援）→ S3 共识合并 +
命名 + provenance。已拆 4 个 sub-issue（S0→S1→S2→S3）。

**用户确认的三点调整**：①阈值用经验初值、落地后多数据集调参；②line provenance **必做**（可溯源调试）；
③文档迁入 processing.md §3.6（删原 references 独立文档）。**新增「文件名/路径 run 级加权共识恢复」小节**回答用户提问
——先用行号+内容确认 run，再对 run 内全部名字观测做**路径分段投票 + 段内字符级共识**（替代现有整串投票），并把
窗口标题栏 filename 纳入票池。

**Linear issue 树**（team claude-code-team / project DocRestore）：父 **AGE-78**，子 **AGE-79 S0**（行账本校验，无依赖）/
**AGE-80 S1**（文件名归一，无依赖）/ **AGE-81 S2**（行号锚定归类，blocked-by S0+S1）/ **AGE-82 S3**（共识合并+命名+
provenance+端到端回归，blocked-by S2）。依赖已用 blocks/blocked-by 连好；各子 issue 含 API 契约 + 「无输入输出证据不得 Done」门槛。

**S0（AGE-79）已落地**（分支 `feature/s0-line-ledger`，commit 7da65f4）：新增 `processing/code_line_ledger.py`
（`build_line_ledger`：行号单调性按视觉 y / 重复行 / inferred / 回查原图 OCR 忠实性 + 回填 OCR score 作 confidence），
pipeline 逐页用该栏源 `layout.columns[idx]` 建账本、列级 flag 并入 `col.flags`（经 quality_report 暴露）。9 单测全过，
`tests/processing`+`tests/pipeline` 427 passed 无回归。AGE-79 → Done，已贴证据。

**S1（AGE-80）已落地**（commit 6028c48）：新增 `processing/code_path_reconcile.py`（`build_vocabulary` 加权词表 +
`build_canonical_map` 传递解析 + `reconcile_paths` 少数派碎片 snap，同扩展名硬约束 + stem 距离≤1 + minority 守门 +
等距 ambiguous，原值留痕 `path_candidates(source=vocab)`），pipeline 在 group 前对全 batch reconcile。**实测修了一个
误并 bug**：`snap_filename_max_distance` 2→1，否则 `x11`↔`x11xv`(差"xv"两字符的真实文件)被错并。真实 16-path 验证
精确 snap 4 个真噪声碎片、garbage/跨扩展名留 S2、x11/x11xv 不误并。12 单测过，439 passed 无回归。AGE-80 → Done。

**S2（AGE-81）实现完成待校准**（commit 5d6b9e7，状态 In Review）：`group_into_files` 签名扩展 (ledgers, config)，
新增 `_overlap_verdict`（confirm/conflict/weak/insufficient 四态）+ `_annotate_overlap_status`（仅标注零回归）+
`_cross_bucket_rescue`（garbage 碎片靠行号重合 confirm 归并，无命中标 orphan_unrescued）。pipeline 累积 ledgers 传入。
**真实端到端**（157 页中间结果离线复跑）：baseline 16（精确等于线上）→ S1 13 → S1+S2 **9**；giesz/openmax/.c 三个
garbage 碎片精确救援，第 4 个 gpu_mojo 拆名碎片与邻页重合且填补行号缺口但 OCR 噪声致一致率 0.56/0.75 落 weak 带，
按设计安全留 orphan。10 单测全过 449 passed 无回归。**校准发现**：θ_high=0.9 对真实 OCR 偏严（同文件重合落 weak），
是否放宽待用户拍板，未擅自在单数据集放宽安全阈值。

用户选「weak + 行号桥接」校准（commit 3c162b2）：跨桶救援接受 weak（多数行一致非冲突）+ orphan 填补 run 行号缺口
（结构桥接）的情形，标 cross_bucket_rescued_weak。**真实端到端 16 → 8 全达成**（gpu_mojo 拆名碎片经桥接归位）。
12 S2 单测全过，451 passed 无回归。AGE-81 → Done。S0/S1/S2 三步落地，文件碎片化问题在该数据集上根治。

**S3（AGE-82）已落地**（commit ec3cb88）：`_merge_columns_by_line_no` 改多数共识（替换 keep-first）+ `line_provenance`
（行号→胜出页，可溯源）；`recover_canonical_path` 取代整串投票做 run 级命名（uniform no-op 防腐安全 / filename 加权投票+
同长度字符共识 / dir 仅由含 dir 观测分段投票不被 dir-less 投没 / 段数并列偏好更完整路径 / 低置信标 consensus_low）；
删两个死代码 helper。

**双数据集端到端泛化验证**（直接复跑 `*_OCR/` 中间产物完整代码路径）：124772c5 **16→8**（全部正确）、e8e88280
**7→6**（零误并：BUiLD.gn/openmax_status.h/两个 250+ 页大文件各自保留；_unknown 页被正确吸收；目录前缀保住）。
baseline 精确等于线上输出，护栏在未见数据全部成立。39 新单测 + 全量 994 passed（3 个 deepseek 为预存环境失败，
非回归）。**AGE-78（父）+ S0/S1/S2/S3 全 Done，碎片化根治。**

**遗留**：①窗口标题栏 filename 纳入命名票池（低优先 follow-up，需 ide_meta_extract 增量，当前命名已正确）；
②θ 阈值多数据集进一步标定；③**已合并 dev**（merge 6d51d9e，feature 分支已推送 origin 备份），待真实任务端到端验证。
详见 memory [[code_mode_fragmentation_diagnosis]] / [[linear_workspace]]。

## 2026-05-30 - 文档减熵：全量对齐流式实现 + 删 DOC_BOUNDARY 残留

**背景**：前几轮删了批量路径死代码（DOC_BOUNDARY 簇 / strip_repeated_lines / dedup 访问器）后，
docs/ 仍大量描述**流式重构前的批量版架构**——pipeline.md §5 流程图画的是"OCR 全收齐→合并→分段→
reassemble"串行模型，§10 整章是 DOC_BOUNDARY 多文档聚类，`process_many` 还写"返回 list"。真正描述
现行实现的反而是被标"历史参考"的 streaming-pipeline.md，事实源被写反了。本轮以**当前代码为唯一真相源**
（逐符号 grep 核实存活/删除），把活文档全面对齐流式生产者/消费者架构。

**改动范围**（zh + en 各 7 文件 1:1）：
- **pipeline.md**：§3.1 签名补 `code`/`controller`、`process_many` 改返回**单个** `PipelineResult`；
  §5 流程图重写为「OCR 生产者 `_ocr_producer` ∥ 流式消费者 `_stream_process`（IncrementalMerger
  增量合并 + RateController 自适应 L* 切段 + 满 5 页异步取 PII lexicon）→ `_finalize_single_doc`
  （reassemble→gap fill→final refine→程序化去重兜底→render）」；§4 依赖表 dedup→IncrementalMerger /
  segmenter→StreamSegmentExtractor / 加 RateController；删 §10 多文档整章并重编号 §11→§10、§12→§11；
  修死测试引用 `test_process_tree_parallel.py`→`test_process_tree.py`、不存在的 `_stream_and_collect`。
- **architecture.md**：§3 数据流改流式（删 step⑧边界检测、step③去 strip_repeated_lines）；删 §5.4
  多文档边界检测、§5.5→§5.4；§6.3 删已落地的"流式 Pipeline 实施"未来项。
- **processing.md**：删 §3.2 strip_repeated_lines；§3.2 合并 IncrementalMerger(生产)+PageDeduplicator(基准)；
  §3.3 分段以 StreamSegmentExtractor 为生产路径、DocumentSegmenter 为参考；后续小节重编号。
- **llm.md / data-models.md / api.md**：删 `detect_doc_boundaries` 协议+实现+prompt 块、删 §3.11
  `DocBoundary` 模型并重编号、`extract_first_heading` 措辞改为设 `doc_title`、api 多项归因改 process_tree 子目录。
- **references/streaming-pipeline.md**：历史档不逐行改，仅顶部加 2026-05-29 dated 更正 + 对"保留给下一版"
  句加删除线，纠正已被推翻的前瞻声明。

**验证**：活文档（排除 progress/archive/streaming-pipeline 历史档）grep 12 个死符号**零残留**；
en/zh 章节重编号一致（pipeline §1-11、data-models §3.1-3.16，无悬空交叉引用）。

**遗留**：无。详见 memory [[docs_streaming_alignment]] / [[doc_boundary_removed]]。

## 2026-05-29 - 删除 DOC_BOUNDARY 文档聚合死代码 + tests 减熵审计

**背景**：审计 tests/（约 1076 用例）发现一批过时/空转/冗余测试。其中 DOC_BOUNDARY 文档聚合
那套"保留给下一版"的代码，经核实代码模式用的是独立的 `group_into_files`（页列→源文件）聚合，
从未复用 DOC_BOUNDARY；用户确认文档模式也不再用 → 保留理由被证伪，整体清除。

**删除范围**（生产零调用，调用方只在测试自我循环）：
- 后端：`models.py::DocBoundary`；`llm/prompts.py` 4 个符号（`_DOC_BOUNDARY_PATTERN` /
  `parse_doc_boundaries` / `DOC_BOUNDARY_DETECT_SYSTEM_PROMPT` / `build_doc_boundary_detect_prompt`）；
  `llm/base.py` 抽象+具体 `detect_doc_boundaries`（连带删 `import json`）；`pipeline.py` 两段死簇
  （`_split_by_doc_boundaries`/`_resolve_split_points`/`_build_sub_docs`/`_resolve_sub_output_dir`
  与 `_detect_doc_boundaries`/`_insert_doc_boundaries`，连带删失效 import `Region`/`sanitize_dirname`）。
- 测试：删 `test_doc_boundary.py`/`test_doc_split.py`/`test_boundary_gap_combo.py` 整文件 +
  `test_process_tree.py::TestProcessTreeDocTitleDir` + `test_full_chain_mocked.py::TestMultiDocFullChain`；
  手术编辑 `test_base_semaphore.py`/`test_concurrent_tasks.py`/`test_selective_rerun.py` 去掉
  `detect_doc_boundaries` 入口/mock/fake 方法。
- 前端：删 zh-CN/zh-TW/en 三处 `progress.docBoundary` key + 更新 progressPhase.ts 注释。

**保留**（活路径仍用）：`extract_first_heading` / `_HEADING_RE`（pipeline 取文档标题）、`_PAGE_MARKER_RE`。

**验证**：后端 mypy --strict + ruff 全过（mypy 仅剩 ocr torch 类型预存噪声）；前端 `npm run typecheck` 过；
pytest pipeline+llm+processing 561 passed/67 skipped、api+其余 258 passed/10 skipped；收集 1076→1054 无报错。

**遗留**：见下方"续二"。详见 memory [[test_suite_audit]] / [[doc_boundary_removed]]。

### 续：清理旧集成测试 + 空转测试（同日）

**A. 整删 6 个模块级 skip 的旧集成测试**（绑定旧串行 pipeline 接口，>1 月未改，功能已被单元测试接住）：
`test_truncation.py` / `test_warnings_e2e.py` / `test_pii_integration.py` / `test_local_provider_e2e.py` /
`test_process_tree_parallel.py` / `test_full_chain_mocked.py`。删前已 grep 确认无其它文件 import 其 helper/fixture。

**B. 删空转测试**（仅删"断言不验证行为"的，保留装配契约/解耦不变量测试）：
- `test_prompts.py`：删 2 个纯 prompt 关键词断言（`test_system_prompt_keywords` / `test_preserves_page_markers`）；
  **保留**结构/meta 位置/prefix 顺序/no-truncation 等装配契约测试（细读后确认非空转，原审计过度标记）。
- `test_pii_detect_prompt.py`：删 2 个关键词断言，保留 verbatim 透传契约（3 个）。
- `test_code_restore_config.py`：删 4 个纯 pydantic 赋值回读，保留默认值守卫 + `TestCodeModeOcrDecoupling` 解耦不变量。
- `test_router.py`：3 个 `assert not None` **强化为 isinstance**（空转→真路由覆盖），非删除。

**验证**：改动文件 35 passed；pipeline+llm+privacy 342 passed/16 skipped；全量收集 1054→1010、无 collection error。
`test_gap_fill_prompt.py` 细读后全部保留（测输入嵌入 + 条件分支 + fill_gap mock 行为，非空转）。

### 续二：preprocessor 收集报错修复 + 代码模式 e2e 重复核查（同日）

**① 修 `test_preprocessor.py` 收集报错**：`preprocessor.py:27` 模块级 `import torchvision`，缺它会让
本文件 collection error（而非 skip）。补 `pytest.importorskip("torchvision")`，与已有 `importorskip("torch")`
一致 → 缺 torchvision 时优雅 skip。

**② 代码模式 e2e"三层重复"核查 → 结论：基本不是真重复**（审计再次过度标记，同 prompt 误标）：
- `test_code_file_grouping`(单元) / `test_age8_e2e`(处理链验收) / `test_code_mode_e2e`(HTTP API 层) 是健康
  测试金字塔，各抓不同失败面，**不删**。
- `test_age8_compile_classify.py` 是 `_classify_errors`（OCR 噪声 vs sysroot 分类启发式）的专属单元测试，
  `compile_check` 只间接覆盖编排层，两者不重叠，**不删**。
- **唯一删除**：`test_age8_e2e.py`（217 行/7 用例）——fixture `tests/fixtures/age8-probe-basic` 从未提交 git、
  自始至终一次没跑过；其产物级断言已被真在跑的 `test_code_mode_e2e.py`（自包含合成 fixture）覆盖。用户确认删除。

**验证**：全量收集 1010→1003、无 collection error；全量 960 passed / 41 skipped。
**已知预存失败（与本次无关）**：`test_deepseek_engine.py` 3 个 fail——本机缺 DeepSeek vLLM worker venv，
其 skipif 只挡 torch/GPU 没挡 worker 路径未配（独立 test 卫生问题，文件本次未碰）。

## 2026-05-29 - 仓库整体减熵：归档老 progress / references 加 STATUS / .gitignore 收尾

通读 backend / frontend / docs / scripts 全量代码与文档后，对疑似 legacy 项逐条核实，多数被
误报（memory 中"已决策保留项"明确保留），实际可减熵的有限。本轮处理：

- **docs/progress.md → docs/progress.archive.md**：项目根的旧进度档案（2492 行，2026-03-14
  ~ 2026-05-11）改名归档，顶部加 banner 说明"当前事实源为 `docs/zh/progress.md`"。修
  `docs/zh/backend/performance_toolkit.md` / `docs/en/backend/performance_toolkit.md`
  指向；更新 `docs/README.md` / `docs/zh/README.md` / `docs/en/README.md` 索引（en
  README 原本的 `progress.md` 死链改为指 `../zh/progress.md` + archive）。
- **docs/zh + en/backend/references/*.md（6 份）顶部加 STATUS 标签**：明确 streaming-pipeline
  / pipeline-parallel / deepseek-ocr2 三份均为历史参考，当前以 `pipeline/` 代码 + 模块
  事实源文档为准；防止未来维护者被旧设计 dict 签名 / 参数名误导。
- **.gitignore 补 `test_images/Chromium_VDA_code`**：本机符号链接（→ `/mnt/TrueNAS_Share/
  chromium/chromium_decode/code/`），与已 ignore 的 `TMedia_source_code` 同类，git status
  不再挂残留。

**未动（核实后确认是有意保留，非 legacy）**：
- `parse_doc_boundaries` / `_split_by_doc_boundaries` / `DocBoundary` / `detect_doc_boundaries`
  —— 多文档聚类逻辑，下一版代码照片还原恢复时复用
- `tests/pipeline/test_process_tree.py`（仅末尾 `TestProcessTreeDocTitleDir` 标 skip，前
  3 个 class 仍 active）与 `tests/pipeline/test_boundary_gap_combo.py`（全 skip）——
  streaming-pipeline.md 明确"代码还原版解锁"
- `compile_*` / `_legacy_compile_status` / `legacy_compile_failure` —— 前端 CodeViewer
  仍硬依赖兼容字段
- `_legacy_analytical_l_star()` —— rate_controller 冷启动 fallback，注释明确
- `FixtureOCREngine` / 所有 `scripts/*` / 所有 i18n key / 所有 CSS class —— 引用核查全部命中
- memory `MEMORY.md` 28 索引 ↔ 28 文件完全一致，无垃圾

**未做（用户选项决议）**：`docs/en/known-issues.md` 仍未补（本轮范围限 legacy 清理，i18n
缺失另行处理）。

工程量：9 文件改动 + 1 rename，无源码改动。

## 2026-05-28 - 全量文档校齐：TextLine / 代码模式 OCR 契约 / 前端代码模式审查 / WYSIWYG 编辑器

通读项目文档与代码后对齐近期改动遗留的几处不一致：

- **zh + en `backend/data-models.md`**：新增 §3.2 `TextLine`（代码模式抽象产物，与 OCR
  provider 解耦），§3.3 `PageOCR` 补 `text_lines` 字段与生命周期"代码模式必填"说明；
  原 3.3~3.16 顺延为 3.4~3.17，无对外引用断裂。
- **en `architecture.md` §6**：补 §6.2 "OCR Contract for Code Mode" 与 §6.3
  "Current Boundaries and Future Extensions"，与 zh 同步；原 "Future Extension
  Directions" 内容并入 §6.3。
- **en `README.md` Core capabilities**：补 "Code mode" 一条，与 zh 6 条对齐。
- **en `frontend/features.md`**：补 §7 "Code Mode Review"（覆盖 files-index /
  CodeViewer / 编辑态实时诊断 / localStorage 接受记录），并把断裂的 §5.5/§6/§7 改回
  连续编号 §5~§11，结构与 zh 完全 1:1。
- **zh + en `frontend/features.md` 组件结构**：在 `TaskResult` 下补
  `MarkdownWysiwygEditor`，在 `TaskDetail` 下补 `CodeViewer`。
- **zh + en `frontend/tech-stack.md` §1 核心技术**：新增 "Markdown WYSIWYG 编辑：
  @tiptap/react 3.22 + StarterKit + extension-image/link/placeholder/table"。

工程量：zh 4 文件 + en 5 文件，无源码改动。无新增 §标号引用，文档内章节号自洽。

## 2026-05-28 - 代码模式设计文档补全（zh + en）

继前一项清理后，按"先评估、再按缺口补"补全代码模式设计文档。zh 覆盖度（命中行数）：
`pipeline.md` 0→14、`llm.md` 0→9、`data-models.md` 7→21、`processing.md` 20→保持
但补 §2 一行 + §3.6 决策由来 + 数据对象专节）；en 同步跟齐。

补充内容：
- **processing.md §2 补 code_column_ocr.py**（zh + en）：原 §2 漏列。
- **data-models.md §3.13~§3.16 新增代码模式数据对象专节**（zh + en）：`PathCandidate` /
  `IDEMeta` / `CodeLine`+`CodeColumn` / `PageColumn` / `SourceFile`，逐字段对齐
  `processing/code_file_grouping.py` / `code_assembly.py` / `ide_meta_extract.py` 真相源。
- **en/data-models.md 补两个历史 en/zh 漂移**：原 en 缺 `CodeRestoreConfig`（§4.8）和
  `CodeDiagnostic`（§4.9），`PipelineConfig` 还漏 `code` 字段；本轮整改后 en §4.8~4.10 与
  zh 完全对齐。
- **pipeline.md §11 新增"代码模式编排"**（zh + en）：覆盖 `code.enable` 入口分支 /
  OCR 强制 basic / 链路顺序 / 错误处理 / 并发与资源 / 输出兼容；§11 相关文档顺延 §12。
- **llm.md §5.6/§5.7/§5.8 新增**（zh + en）：`CodeLLMRefiner`（refine/rewrite 双模式 +
  chunk + 回退）/ `DiagnosticCodeRepairer`（窗口 + 行号重映射 B7 C2 + 行数守恒 +
  共置兄弟 B7 C13）/ `CodeConsistencyAuditor`（变行数必重诊断 B7 C3 + 接受门）。
- **en/processing.md 补缺失的 §3.5 Code Mode Processing Chain**（同步上轮决策由来由 §3.5
  顺延为 §3.6），§4 Dependencies 补 `CodeRestoreConfig` 与 `code_file_grouping.py` 两行。

工程量：zh 5 文件改动、en 4 文件改动；新增 ~330 行文档，无源码改动。门禁未跑（无 Python
源码变更，post-edit hook 不触发）。

## 2026-05-28 - 迭代垃圾清理 + AGE-71 多任务调度需求拆分

Linear：在 claude-code-team（key AGE）/ DocRestore 项目下新建 feature **AGE-71**（按硬件
资源与 LLM 响应自适应并行/队列调度），拆 6 个子任务 AGE-72~77（设计先行 AGE-77 → 调度层
AGE-72 / 探测信号 AGE-73 / 决策策略 AGE-74 / API AGE-75 / 前端 AGE-76），全部归入 DocRestore。

迭代垃圾清理：
- **AGE-8 历史设计文档合并入主线**：把代码模式布局识别的设计决策由来（v1 像素几何切分失败
  → v2 行号锚点不变量 → v3 回滚强插入/中心点归类/num_range 上限）与 1259 张/6 数据集鲁棒性
  结论（IDE 99.82%、文档误判 0%、栏数 1/2/3 自适应）凝练进 `backend/processing.md §3.6`（zh）
  与 en 对应小节；删除 `age-8-ide-code.md`/`age-8-robustness-report.md`/`age-58-code-mode-quality-plan.md`
  （zh+en 共 5 篇），清理 docs/README、zh/README、zh/backend/README 索引引用。
- **开发期一次性脚本删除（12 个）**：age8 probe/analyze/e2e/robust/validate 系列 + age50_seed_fixture
  + analyze_tables（均 0 测试/代码依赖）。保留 age8_compile_check/stub_includes（有测试+前端引用）、
  bench_*/gpu_sampler/compare_profile/run_e2e（config.py + 参考文档引用的性能工具集）、worker/setup/start。
- **磁盘中间产物清理**（均已 gitignore，约 23MB）：test_images/*_OCR、outputs/preview、output、
  data/age50-fixture-images、screenshots、.playwright-mcp(225 张)、debug_image；**保留 data/docrestore.db** 运行库与工具缓存。

遗留：
- `docs/en/backend/processing.md` 缺 §3.5 代码模式处理链路（zh 有，历史 en 同步遗漏），本轮已补"设计决策由来"
  小节但未补链路描述，属既有 en/zh 漂移，单独排期。
- `test_images/Chromium_VDA_code` 为新增 IDE 数据集，建议比照其他数据集补进 .gitignore。

## 2026-05-27 22:50 CST - 代码模式 B7+B4 多 agent 评审与缺陷修复

对 dev 相对 main 的 128 提交按阶段分段评审（B1-B7），本轮完成 B7（AGE-58 诊断/精修）
与 B4（AGE-8 代码模式核心）两段的严重项 + 中优先级修复，并清理数据集特定关键词。

完成内容：
- **CLAUDE.md 质量门禁约定**：同步 codex 的 `scripts/check_quality.sh` + `pre-commit`
  入口；`.gitignore` 解禁 CLAUDE.md、排除 `.claude/*.env`（密钥）与 `.claude/skills/`。
- **B7 修复（9 commit）**：C1 column 二次 OCR `zip(strict)` 崩溃；C7 code_context
  `read_text` 容错；C5/C6 ocr_postfix 误伤正则收紧；C13 诊断子进程进程组兜底；
  C2/C3/C4 scoped repair 行号重映射 + 行数守恒 + audit 重诊断；C11/C12 阻塞 IO 移出
  事件循环 + PUT 串行化；C8/C9 诊断失败行兜底抽取 + 纯语法工具归类；C19 实时诊断
  同目录 `#include` 解析；C23 OCR 噪声列号还原源列。
- **数据集关键词清理（1 commit，~47 文件）**：chromium/openmax/DSC* → 中性占位
  （app/core/widget、page<N> 等），测试 fixture 输入与断言成对替换；保留浏览器
  Chrome 区域术语与 package-lock.json。
- **B4 严重修复（4 commit）**：G2 code_assembly 不再静默丢栏内代码行；H1 目录兼容
  改全连接（空目录不桥接不同目录）；H3 消歧后缀全局唯一；H2 renderer 防同名覆盖 +
  非法字符清洗 + per-file try/except；H5 落实 code.enable→OCR basic 自动切换。
- **B4 中优先级修复（1 commit）**：G4 char_width 按东亚全角加权；G6 缺号检测剔除
  尾部离群行号；G7 栏归属中心点兜底；G8 行号非递减计数 + max>min；H4 breadcrumb
  重叠去重受像素重叠约束。

验证：
- 各修复均带回归用例，逐文件过 `mypy --strict` + `ruff` + `typos` + pre-commit。
- 全量 `pytest`：989 passed（数据集清理后）/ 767 passed（B4 后子集），0 fail；
  `tests/ocr` 的 3 fail+1 error 为本机缺 torchvision/DeepSeek 模型的环境问题，与本轮无关。
- **真实数据 E2E（用户跑全量 272 图 before/after 对比）**：聚合脏度指标（OCR 错/CJK
  噪声/格式/杂散数字）持平互有正负，质量报告内容一致、每文件状态一致 → 无质量下降；
  大文件 615 行差异主要是 LLM 非确定性；可确定归因的确定性改动（如 C5 `1/`→`//`）均向好。

遗留问题：
- **B1/B2/B3/B5/B6 段未评审**（性能并行/流式 v2/质量链/回归 WYSIWYG/后续修复）。
- G3（indent 基线均匀平移保留相对结构）、G5（跨页按行号拼接为合理约定）经评估非缺陷，未改。
- E2E 因 LLM 随机性无法单次严格隔离"代码效果 vs 采样噪声"；如需严格对比应设 temperature=0。
- `.gitignore` 数据目录名已中性化为 `ide_code_sample`，本地 `test_images/Chromium_VDA_code`
  目录现为未跟踪状态，需手动重命名或保留（不入库）。

## 2026-05-14 22:59:47 CST - AGE-63 路径置信度参与代码文件分组

完成内容：
- `code_file_grouping` 的 canonical filename / dir 选择改为优先使用路径置信度总和，再看频次和长度，避免低置信 OCR 变体覆盖高置信路径候选。
- 多 segment 合并时，如果存在低置信路径参与，会写入 `code.grouping.low_confidence_path_merged` 风险 flag。
- 保持 `group_into_files(PageColumn)` 入口兼容，内部开始消费 AGE-62 的 `path_confidence`，为后续更彻底的 `CodeSegment` 分组重写铺路。
- 补充分组测试，覆盖低置信 filename 变体、兼容目录合并时的高置信目录选择和低置信合并 flag。

验证：
- `python -m pytest tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，54 passed, 7 skipped。
- `ruff check backend/docrestore/processing/ide_meta_extract.py backend/docrestore/processing/code_file_grouping.py backend/docrestore/output/code_renderer.py tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py`：通过。

遗留问题：
- 本次仍保留 PageColumn 输入，尚未完全改成 `group_segments_into_files()`；同页多 column 归同路径的强约束仍沿用既有 `_enforce_one_page_one_file()`。

## 2026-05-14 22:53:13 CST - AGE-62 代码来源置信度建模

完成内容：
- 新增 `PathCandidate`，`IDEMeta` 保留 breadcrumb、tab、peer 等路径候选、原始文本、候选来源和置信度。
- `IDEMeta` 新增 `path_confidence`，保留旧的 `filename` / `path` / `language` 字段，确保现有分组逻辑兼容。
- 新增 `CodeSegment` 与 `segment_from_page_column()`，把每张图每个 column 转成可审计的最小来源单元。
- `files-index.json` 新增 `path_confidence` 与 `source_segments`，每个 segment 包含来源页、column、bbox、行号范围、选中路径、路径候选和 flags。
- 补充测试覆盖 breadcrumb/tab 候选、tab fallback、peer 补全、segment provenance 和 renderer 索引输出。

验证：
- `python -m pytest tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py -q`：通过，45 passed, 7 skipped。
- `python -m pytest tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，7 passed。
- `ruff check backend/docrestore/processing/ide_meta_extract.py backend/docrestore/processing/code_file_grouping.py backend/docrestore/output/code_renderer.py tests/processing/test_ide_meta_extract.py tests/processing/test_code_file_grouping.py tests/output/test_code_renderer.py`：通过。

遗留问题：
- AGE-62 只建立 provenance 和置信度数据面；AGE-63 才会把分组逻辑切到 `CodeSegment` / `PathCandidate`。

## 2026-05-14 22:44:33 CST - AGE-61 代码模式质量报告闭环

完成内容：
- `files-index.json` 新增代码模式质量摘要：组合后的 `flags`、`source_file_flags`、`source_column_flags`、来源页/column 计数和轻量 `quality` 字段。
- 新增 `detect_code_mode_quality()`，把 `code.refine.truncated`、大 gap 折叠、缺失行号、未知文件名、meta fallback、unpaired code、路径安全降级等代码模式风险写入 `.quality_report.json`。
- `merged_pages` 仅作为规模信息保留，不单独生成质量 issue，避免把大文件多页合并误判为失败。
- 代码模式 pipeline 在渲染后接入质量检测，新任务会产出非空代码质量报告。
- 补充 renderer 和 quality report 单元测试，覆盖 flags 聚合、旧索引兼容、代码模式 issue 生成和路径安全降级。

验证：
- `python -m pytest tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_zip_code_mode.py tests/api/test_code_mode_e2e.py -q`：通过，35 passed, 1 skipped。
- `ruff check backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/quality_report.py backend/docrestore/pipeline/pipeline.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- AGE-61 只建立质量可观测性闭环，不改变分组、OCR 或 LLM 修复策略；后续继续 AGE-62。

## 2026-05-14 18:37:20 CST - AGE-58 实现路径拆分为子 issue

完成内容：
- 在 Linear 中将 AGE-58 拆成 9 个 child issue：AGE-61 到 AGE-69，覆盖质量报告、`CodeSegment` / `PathCandidate`、分组重写、UI 噪声与确定性清理、多语言诊断、小窗口 LLM 修复、全文件一致性 pass、二次裁剪 OCR 和可选代码库上下文。
- 为子 issue 添加父子关联到 AGE-58，并设置关键依赖关系：AGE-62 依赖 AGE-61，AGE-63/AGE-64 依赖 AGE-62，AGE-65 依赖 AGE-61/AGE-64，AGE-66 依赖 AGE-62/AGE-65，AGE-67 依赖 AGE-66，AGE-68 依赖 AGE-62/AGE-64，AGE-69 依赖 AGE-62/AGE-66。
- 在 AGE-58 留下拆分总览评论，说明主线最短闭环和增强线执行顺序。
- 更新 `docs/zh/backend/age-58-code-mode-quality-plan.md`，新增 Linear 子 issue 拆分表。

验证：
- Linear 返回的子 issue 编号与父子关联已确认。

遗留问题：
- 尚未开始实现；建议从 AGE-61 开始建立质量报告闭环。

## 2026-05-14 18:29:52 CST - AGE-58 小段后全文件一致性修复设计

完成内容：
- 补充 AGE-58 修复方案 9.1 节，明确小段修复后可以做全文件 pass，但定义为“全文件一致性审计 + 受限 patch”，不是整文件重写。
- 设计 prompt 组织原则：区分 `editable ranges` 与 `read-only excerpts`，全局上下文可包含 file outline、symbol table、imports/includes、诊断、前序局部修复摘要、重复 OCR 混淆和 unresolved 项。
- 明确大文件不发送完整代码，改用 outline、diagnostics、numbered excerpts、suspicious ranges 和 previous repairs 组织上下文。
- 要求全文件 pass 输出结构化 JSON patch，绑定原始行号范围；未列入 editable range 的问题只能返回候选范围，不能直接修改。
- 已在 Linear AGE-58 同步该补充。

验证：
- 文档级补充，无代码测试。

遗留问题：
- 后续实现需要设计 editable range 生成策略、patch 应用器和诊断恶化回退机制。

## 2026-05-14 18:26:20 CST - AGE-58 小片段关联修复设计补充

完成内容：
- 补充 AGE-58 修复方案中“小片段 LLM 修复”的关键边界：小片段不是只给 LLM 局部十几行，而是“编辑范围小、只读上下文大”。
- 新增 `CodeRepairContext` 草案，包含编辑行号范围、局部代码、所在符号、文件 outline、诊断、相关片段、路径候选和来源页。
- 明确关联修复流程：先输出修复计划和依赖判断，再生成一个或多个 scoped patch；不能因为需要上下文就退回整文件 rewrite。
- 约束证据不足的业务逻辑问题必须保留 unresolved / OCR-Q，不允许 LLM 强行猜。
- 已在 Linear AGE-58 同步该补充。

验证：
- 文档级补充，无代码测试。

遗留问题：
- 后续实现时需要决定 `CodeRepairContext` 的 parser/linter 数据来源，以及多语言 file outline 的最小可行实现。

## 2026-05-14 18:14:49 CST - AGE-58 代码模式质量修复方案

完成内容：
- 新增 `docs/zh/backend/age-58-code-mode-quality-plan.md`，把代码模式质量修复拆成质量可观测性、column segment 建模、路径候选置信度、UI 噪声过滤、确定性 OCR 清理、诊断驱动 LLM、小片段修复、二次裁剪 OCR 和可选代码库上下文。
- 明确修复主线不依赖参考源码，必须泛化到多项目、多语言；参考源码匹配只作为可插拔增强，不绑定 示例、C/C++ 或任何固定项目结构。
- 修正质量判断口径：多页归并到少量源文件对大文件是合理现象，`merged_pages` 只作为风险信号，不能单独作为失败条件。
- 更新后端文档索引，新增 AGE-58 方案入口。
- 更新已知问题，记录“代码模式质量不能只看归并文件数量”的复用经验。
- 已在 Linear AGE-58 补充修复方案文档位置、设计口径和推荐实施顺序。

验证：
- 已核对 `docs/zh/architecture.md`、`docs/zh/backend/age-8-ide-code.md`、`docs/zh/backend/age-8-robustness-report.md`、`docs/zh/known-issues.md` 和代码模式相关实现模块后撰写方案。

遗留问题：
- 尚未进入实现；推荐后续先做 Phase 0-2：代码质量报告、`CodeSegment` / `PathCandidate`、UI 噪声过滤与确定性 OCR 清理。

## 2026-05-14 17:45:38 CST - AGE-58 代码模式质量偏差排查

完成内容：
- 对比内部 IDE 代码截图数据集原始照片、`/tmp/docrestore_b5950355` OCR 中间产物与 `files/` 最终代码产物，确认最终偏差不是单一 OCR 识别率问题。
- 定位主要质量损失来源：整屏 OCR 混入 VSCode 顶栏、breadcrumb、搜索框、Loading 遮罩、底部 Terminal/Marketplace 等 UI 噪声；双栏 IDE 截图被按不稳定路径 OCR 过度归并；`files-index.json` 中多个文件带 `code.refine.truncated`，LLM 修复基本未能作用于大文件。
- 抽样确认 PP-OCR basic 对可见代码行能提供可用行级 bbox 与部分文本，但暗色主题小字号、红色语法高亮、拍摄透视和低对比会造成 `//`、下划线、大小写、引号、括号和标点的系统性错误。
- 已在 Linear AGE-58 补充排查结论与分阶段优化方案，后续应优先做版面裁剪、路径/分组置信度和编译驱动修复，而不是只切换 OCR 引擎。

验证：
- 抽样查看 `page06835.JPG`、`page06853.JPG`、`page07032.JPG` 原图，分别覆盖双栏初始页、右侧头文件 Loading 遮罩、搜索框遮挡与后段行号页。
- 抽样读取对应 `text_lines.jsonl`、`files-index.json` 和最终 `widget_decode_helper.cc`、`widget_decode_helper.h`、`BUILD.gn`、`gles2_dmabuf_to_egl_image_translator.cc` 产物进行交叉比对。

遗留问题：
- AGE-58 仍需实现代码模式质量门禁、双栏裁剪重 OCR、路径候选校正、行号连续性分组和编译/LLM 小片段修复；本次仅完成归因和方案同步。

## 2026-05-14 16:41:51 CST - 修复代码模式未切 PP-OCR basic 导致 text_lines 为空

完成内容：
- 定位失败原因：代码模式的 fail-fast 校验本身正确，问题是前端只提交 `code.enable=true`，没有把默认 PaddleOCR 任务显式切到 `paddle_pipeline="basic"`；后端默认 `vl` pipeline 不产出 `PageOCR.text_lines`，因此 272 页全部被判定为缺少行级 bbox。
- `OCRConfigRequest` 新增 `paddle_pipeline: Literal["basic", "vl"] | None`，允许请求级覆盖 PaddleOCR pipeline。
- `TaskForm` 在默认 PaddleOCR 且开启代码模式时提交 `ocr.model="paddle-ocr/ppocr-v4"` 与 `ocr.paddle_pipeline="basic"`；选择 DeepSeek 等非 PaddleOCR 引擎时不强塞 Paddle 专用字段，保留 OCR 抽象边界。
- 补充前端回归测试，锁住“勾选代码模式 → 默认 PaddleOCR 请求 basic pipeline”的行为。
- 更新已知问题文档，明确 `text_lines` 校验与前端显式 basic 请求的关系。

验证：
- `npm exec vitest -- --run tests/components/TaskForm.test.tsx`：通过，1 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `python -m pytest tests/api/test_create_task_code_mode.py -q`：通过，9 passed；pytest-asyncio 输出 loop scope deprecation warning。
- `ruff check backend/docrestore/api/schemas.py tests/api/test_create_task_code_mode.py`：通过。

遗留问题：
- “预加载引擎”接口当前只按 model/GPU 判断 ready，没有把 `paddle_pipeline` 暴露到状态响应；即使预热了 VL，任务提交 basic 后 EngineManager 仍会按 pipeline 维度切换，不影响正确性，但预热提示还不够精确。

## 2026-05-13 17:39:03 CST - AGE-57 代码模式 Review 窗口 IDE 化

完成内容：
- `CodeViewer` 从纯 `<pre>` 升级为轻量 IDE 视图：代码区显示 gutter 行号、按语言 token 着色，并根据 `compile_failing_lines` 高亮编译/语法失败行。
- 新增代码文件在线编辑入口，编辑后通过 `PUT /tasks/{task_id}/files/{file_path}` 保存回 `output_dir/files/`；后端路径校验沿用代码文件读取边界，只允许写已存在的代码产物。
- 保存成功后同步更新前端内存索引与后端 `files-index.json` 的 `line_count` / `line_no_range`，避免文件列表行数长期显示旧值。
- 抽出 `features/task/codeSyntax.ts` 维护轻量语法 token 化逻辑，覆盖 C/C++、Python、JS/TS、Shell、JSON/Markdown 等常见代码模式输出。
- 补充中英文/繁中文案，完善桌面和窄屏样式；窄屏下代码路径独占一行，避免被编辑按钮挤压断行。

验证：
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx`：通过，4 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `python -m pytest tests/api/test_code_mode_e2e.py -q`：通过，3 passed；pytest-asyncio 输出 loop scope deprecation warning。
- `ruff check backend/docrestore/api/routes.py backend/docrestore/api/schemas.py backend/docrestore/api/errors.py tests/api/test_code_mode_e2e.py`：通过。
- Vite dev server + Playwright 视觉验证：已生成 `screenshots/current.png` 和 `screenshots/current-mobile.png`；桌面与移动宽度均无横向溢出，错误行、语法着色、行号和编辑按钮显示正常。

遗留问题：
- 本次视觉验证使用注入的代码模式 DOM fixture 覆盖样式；因 FastAPI 后端未启动，Vite 首页请求 `/api/v1/tasks`、`/api/v1/gpus`、`/api/v1/ocr/status` 出现 502，这不影响代码视图样式验证。后续可增加真实已完成代码模式任务 fixture 做端到端截图。
- 轻量语法着色不是完整语言解析器，不能跨行识别块注释/多行字符串；当前定位是 Review 窗口可读性增强，复杂 IDE 语义能力仍建议后续接入成熟编辑器库。

## 2026-05-13 11:03:00 CST - 修复多文档预览路径型 page marker 同步回归

完成内容：
- 定位回归原因：共享原图列表统一把左侧源图 `data-page` 归一化为裸文件名，但 Markdown 的 `<!-- page: ... -->` 若带相对路径，右侧锚点仍使用原始路径，导致左右锚点 key 不一致。
- `injectPageAnchors()` 改为用同一套 `imageNameToPageKey()` 归一化 page marker，保证 `section/p2.jpg` 注入为 `data-page="p2.jpg"`。
- `filterImagesForDoc()` 同时支持 page marker 原文和归一化后的 page key 匹配，保留带输入子目录的多文档过滤能力。
- 补充回归测试，覆盖带相对路径的 page marker 源图过滤和 Markdown 锚点归一化。

验证：
- `npm exec vitest -- --run tests/components/DocCodePreview.test.ts tests/features/task/markdown.test.ts tests/hooks/useScrollSync.test.ts`：通过，30 个测试。
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx`：通过，2 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。

遗留问题：
- 若同一个任务中不同输入子目录存在同名图片，且 page marker 只保留裸文件名、`doc_dir` 又无法提供输入目录线索，前端仍只能匹配到全部同名页；需要后端在 page marker 中稳定保留输入相对路径才能彻底消除歧义。

## 2026-05-12 23:18:00 CST - 抽象预览滚动与原图列表复用

完成内容：
- 新增 `SourceImageList` 共享组件，统一文档模式与代码模式的原图列表渲染、`data-page` 锚点写入和点击放大 lightbox 行为。
- 新增 `sourceImagePreview` 数据模型工具，集中维护源图文件名到 page key 的转换规则，避免两个模式各自拆文件名。
- 新增 `usePreviewScrollSync`，把预览场景固定为 `continuous` 同步策略；文档模式与代码模式都通过该 hook 接入底层 `useScrollSync`。
- `SourceImagePanel` 收敛为文档模式外壳组件，底层列表复用 `SourceImageList`；`CodeViewer` 移除内联原图列表和 lightbox 实现。

验证：
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx tests/components/DocCodePreview.test.ts tests/hooks/useScrollSync.test.ts`：通过，16 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `pytest tests/output/test_code_renderer.py -q`：通过，8 passed, 1 skipped。
- `ruff check backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- `mypy --strict backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- Vite dev server + FastAPI + 本地 `age50-fixture` 任务完成 Playwright 验证：文档模式源图列表存在共享锚点；代码模式代码正文与原图列表锚点一致，双向滚动同步仍生效；页面无 console error。

遗留问题：
- 当前抽象覆盖原图列表与同步策略；代码正文锚点生成仍保留在 `CodeViewer`，因为它依赖代码模式专有的 `source_page_ranges` 与文件内容行号。

## 2026-05-12 22:43:00 CST - 代码模式结果与原图同步滚动

完成内容：
- `files-index.json` 新增向后兼容的 `source_page_ranges` 字段，记录每个 `source_pages` 对应来源页的起止行号，供前端把代码位置映射回原图。
- 代码模式 `CodeViewer` 复用 `useScrollSync`：代码正文按来源页行号插入隐形 `data-page` 锚点，右侧原图使用同名锚点，实现代码与原图双向同步滚动。
- 旧代码模式任务没有 `source_page_ranges` 时，前端按 `source_pages` 顺序均分代码正文生成锚点，保留历史产物可预览性。
- 代码模式窄宽度布局改为纵向堆叠，避免三栏网格把代码栏挤到不可读，保证同步滚动区域仍可操作。
- 补充前端组件测试与后端 renderer 测试，覆盖新索引字段、代码/原图同名锚点和旧索引兼容。

验证：
- `pytest tests/output/test_code_renderer.py -q`：通过，8 passed, 1 skipped。
- `npm exec vitest -- --run tests/components/CodeViewer.test.tsx tests/hooks/useScrollSync.test.ts`：通过，12 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- `ruff check backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- `mypy --strict backend/docrestore/output/code_renderer.py tests/output/test_code_renderer.py`：通过。
- Vite dev server + FastAPI + 本地 `age50-fixture` 代码模式任务完成 Playwright 视觉验证；窄宽度和桌面宽度布局正常，页面无 console error。浏览器实测代码正文与原图列表均有同名 `data-page` 锚点，窄宽度下代码滚动会同步原图列表，原图滚动会同步代码位置。

遗留问题：
- `source_page_ranges` 对大 gap 折叠后的显示行映射仍是基于原始行号的近似位置；如后续需要像 IDE 一样逐行精确，可在后端索引中输出渲染后行号映射。

## 2026-05-12 14:01:38 CST - 前端预览连续滚动同步优化

完成内容：
- 将文档预览的源图/Markdown 同步滚动从顶部跳转式 `start` 对齐改为 `continuous` 连续映射。
- `useScrollSync` 新增基于视口中心的 page 区间比例映射：屏幕中间文本落在当前 page 的哪个相对位置，源图列表就滚到同 page 区间的对应位置，并保持在视口中间。
- 最后一页没有下一锚点时，使用锚点元素高度或剩余内容高度计算连续比例，避免末页同步退化为固定位置。
- 程序化滚动防递归标记改为只标记被同步侧，避免用户连续滚动源侧时后续 scroll 事件被 150ms 窗口吞掉。
- 补充 `continuous` 模式单元测试，覆盖比例映射和连续滚动源侧不被阻断。

验证：
- `npx vitest run tests/hooks/useScrollSync.test.ts`：通过，10 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- Vite dev server + Playwright 截图验证首页正常渲染，控制台无 warning/error。当前没有可直接复现的已完成预览任务页面，滚动行为以 hook 单元测试覆盖。

遗留问题：
- 若后续需要更贴近真实视觉，可增加带已完成任务 fixture 的前端集成页或 e2e 测试，用真实源图高度和 Markdown 锚点验证滚动同步手感。

## 2026-05-12 18:36:14 CST - 代码模式 OCR 解耦与失败状态持久化

完成内容：
- 移除 `code.enable=true` 时 API / `PipelineConfig` 对 PaddleOCR `paddle_pipeline` 的自动改写；代码模式改为依赖 `PageOCR.text_lines` 抽象契约。
- 代码模式在未获得任何行级 OCR 输出时明确失败，提示当前 OCR 引擎缺少 `PageOCR.text_lines`，避免静默产出空源文件。
- retry / resume 新任务继承原任务 `CodeRestoreConfig`；对旧 bug 产生的无 code 快照任务，从 `files-index.json` 或 `files/` 代码产物做最小兼容推断。
- `task_results` 增加子文档级 `error` 持久化，重启后可恢复部分失败 tab 的错误状态。
- 更新架构文档与已知问题，明确代码模式的 OCR 抽象边界。

验证：
- `pytest tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/persistence/test_database.py tests/pipeline/test_task_manager.py tests/pipeline/test_code_mode_ocr_contract.py`：通过，64 个测试。
- `ruff check ...`（本次改动相关文件）：通过。
- `mypy --strict ...`（本次改动相关后端与测试文件）：通过。
- `bash scripts/check_quality.sh`：通过；895 passed, 73 skipped。前端 lint 仅输出 ESLint 多 tsconfig 性能提示。

遗留问题：
- 若未来引入新的 OCR 引擎，需要在对应引擎中填充 `PageOCR.text_lines` 才能用于代码模式。

## 2026-05-12 20:13:49 CST - 修复多子文档预览源图过滤

完成内容：
- 定位 `58c941046bab5159d695025d7bf9b15765534fa7` 后多子文档部分 tab 不滚动的问题：前端只按 `doc_dir/` 过滤源图，但边界检测产生的 `doc_dir` 是输出子目录名，不一定对应输入图片路径前缀。
- 新增 `features/task/sourceImages.ts`，优先从当前子文档 Markdown 的 `<!-- page: ... -->` 标记提取页文件名来匹配源图。
- 对 `section/输出标题` 这类“输入子目录 + 边界拆分输出目录”场景，逐级剥掉尾部输出目录后再匹配源图前缀。
- 保留无 page marker 时按 `doc_dir/` 前缀过滤的旧行为。
- 补充前端单元测试，覆盖扁平边界拆分、子目录内边界拆分、无页标记兼容和页标记去重。

验证：
- `npx vitest run tests/components/DocCodePreview.test.ts tests/hooks/useScrollSync.test.ts tests/features/task/markdown.test.ts`：通过，28 个测试。
- `npx vitest run`：通过，57 个测试。
- `npm run typecheck`：通过。
- `npm run lint`：通过；仅输出 ESLint 多 tsconfig 性能提示。
- Vite dev server + Playwright 首页截图完成；因后端服务未启动，控制台出现 `/api/v1/tasks`、`/api/v1/gpus`、`/api/v1/ocr/status` 的 502，不影响本次前端渲染检查。

遗留问题：
- 当前没有可直接加载的已完成多子文档任务 fixture，真实任务页的滚动手感仍建议后续用 e2e fixture 覆盖。

## 2026-05-14 23:08:19 CST - AGE-64 代码模式 OCR 后处理与 UI 噪声过滤

完成内容：
- 新增 `clean_code_ocr_text()`，在代码模式渲染前过滤 IDE/VS Code 强信号 UI 噪声行，并以空行保留原始行数。
- 保留 `correct_ocr_artifacts()` 兼容入口，扩展语言感知规则：`//` 注释前缀错识恢复、C/C++ 预处理指令大小写规范化。
- 代码模式 pipeline 接入后处理结果 flags，`files-index.json` 与 `.quality_report.json` 可追踪 UI 噪声过滤、孤立 OCR 伪字形过滤和行数保持。
- 补充多语言与安全回归测试，避免 Python 注释语义、字符串字面量和普通代码行被误过滤。

验证：
- `python -m pytest tests/processing/test_ocr_postfix.py tests/pipeline/test_quality_report.py tests/output/test_code_renderer.py tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，78 passed, 1 skipped。
- `ruff check backend/docrestore/processing/ocr_postfix.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/processing/test_ocr_postfix.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- 当前只做确定性强规则过滤；弱信号 UI 噪声和跨行语义修复留给 AGE-65/AGE-66 的关联与全文修复链路处理。

## 2026-05-14 23:20:50 CST - AGE-65 多语言语法诊断接入

完成内容：
- 新增 `CodeDiagnosticRunner`，支持 Python/JSON/TOML/XML/YAML 标准解析与 JS/TS/C/C++/Go/Rust 外部工具诊断；工具缺失降级为 `tool_unavailable`。
- 代码模式 pipeline 启用诊断，renderer 将新 `diagnostic` 对象和旧 `compile_status`、`compile_failing_lines` 等兼容字段写入 `files-index.json`。
- `.quality_report.json` 新增 `code_diagnostic` 阶段，记录语法失败、语义失败、工具缺失和诊断运行失败。
- 补充 fake 工具链单测，覆盖工具存在、工具缺失、语法失败行号提取、语义失败分类、索引回填与质量报告记录。

验证：
- `python -m pytest tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_code_mode_e2e.py tests/api/test_zip_code_mode.py -q`：通过，46 passed, 1 skipped。
- `ruff check backend/docrestore/processing/code_diagnostics.py backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- 当前诊断是单文件低成本检查，不加载项目完整依赖图；`semantic_dirty` 更多表示缺上下文或依赖，后续修复窗口应优先使用 `syntax_dirty` 行。

## 2026-05-14 23:31:22 CST - AGE-66 诊断驱动 scoped code repair

完成内容：
- 新增 `CodeRepairContext` / `DiagnosticCodeRepairer`，基于语法诊断失败行生成小编辑窗口，prompt 携带只读上下文、outline、来源页、路径候选和相关片段。
- LLM scoped repair 输出修复计划、依赖判断和 JSON patch；patch 只能落在 `edit_range` 内，越界或解析失败即回退该窗口。
- patch 应用后重新运行轻量诊断；诊断结果恶化时回退 patch，证据不足时保留 unresolved。
- 代码模式 LLM 前新增预诊断：有 `syntax_dirty` 行时走 scoped repair；大文件缺少诊断窗口时跳过整文件 LLM，避免常态 `code.refine.truncated`。
- 质量摘要与质量报告补充 `code.repair.*` 风险标记。

验证：
- `python -m pytest tests/llm/test_code_repair.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_code_mode_e2e.py -q`：通过，68 passed, 1 skipped。
- `ruff check backend/docrestore/llm/code_repair.py backend/docrestore/llm/code_refine.py backend/docrestore/llm/prompts.py backend/docrestore/processing/code_diagnostics.py backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/llm/test_code_repair.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py`：通过。

遗留问题：
- 当前 scoped repair 的相关片段选择仍是同目录启发式；更强的跨文件符号关联可在后续 issue 中基于 outline / import/include 图继续增强。

## 2026-05-15 04:05:47 CST - AGE-67 小段修复后的全文件一致性审计 pass

完成内容：
- 新增 `CodeConsistencyAuditor` 与 `CodeConsistencyAuditContext`，在 scoped repair 后读取全文件 outline、symbol table、只读摘录、诊断、未解决项与重复 OCR 混淆。
- 审计 prompt 只允许输出绑定行号的受限 JSON patches；未授权范围的问题只能返回 `candidate_ranges`。
- patch 应用前校验必须落在 `editable_ranges` 内；试图修改只读范围、解析失败或输出截断会回退。
- patch 应用后重新运行轻量诊断；诊断恶化时回退该 patch。
- pipeline 在诊断驱动 scoped repair 后追加全文件一致性审计，质量摘要与质量报告补充 `code.audit.*` 标记。

验证：
- `python -m pytest tests/llm/test_code_repair.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py tests/api/test_code_mode_e2e.py -q`：通过，74 passed, 1 skipped。
- `ruff check backend/docrestore/llm/code_repair.py backend/docrestore/llm/prompts.py backend/docrestore/output/code_renderer.py backend/docrestore/pipeline/pipeline.py backend/docrestore/pipeline/quality_report.py tests/llm/test_code_repair.py`：通过。

遗留问题：
- 当前 repeated OCR confusion 使用字符归一启发式生成候选行；后续可结合语言 parser token 与项目符号索引降低误报。

## 2026-05-15 04:12:38 CST - AGE-68 代码 column 裁剪增强二次 OCR

完成内容：
- 新增 `code_column_ocr` 模块，基于 `IDELayout` 行号锚点和 column 行生成 per-column crop bbox。
- crop 预处理支持灰度、自适应对比度、对比度增强、锐化和 2x/3x 放大，并将 crop OCR bbox 回映射到原图坐标系。
- 代码模式 pipeline 增加可选 `secondary_column_ocr`，开启后先用整图 OCR 识别 layout，再对每个 column 裁剪增强重跑 OCR，保留 tab/sidebar/terminal 等首轮 layout provenance。
- API 请求与 `CodeRestoreConfig` 增加二次 OCR 配置项；默认关闭，避免不支持临时 crop OCR 的环境增加成本或失败。
- 补充单测覆盖裁剪边界、增强缩放、bbox 回映射和双栏 crop OCR 集成。

验证：
- `python -m pytest tests/processing/test_code_column_ocr.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/api/test_code_mode_e2e.py tests/output/test_code_renderer.py -q`：通过，36 passed, 1 skipped。
- `ruff check backend/docrestore/processing/code_column_ocr.py backend/docrestore/pipeline/config.py backend/docrestore/api/schemas.py backend/docrestore/pipeline/pipeline.py tests/processing/test_code_column_ocr.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py`：通过。

遗留问题：
- 当前二次 OCR 默认关闭；真实数据集需要开启 `code.secondary_column_ocr=true` 后做抽样对比，确认不同 OCR 引擎对 crop 临时图的行级输出质量。

## 2026-05-15 04:21:44 CST - AGE-69 可选 CodeContextProvider

完成内容：
- 新增离线 `CodeContextProvider` Protocol 与 `LocalCodeContextProvider`，支持本地参考源码目录的 `list_files`、`search_paths`、`search_snippets`。
- 支持多语言文件发现、shebang 识别、路径 fuzzy match、片段检索，并默认跳过 `.git`、`node_modules`、build/cache 等目录。
- `CodeRestoreConfig` / API 请求增加 `context_root`，默认空字符串关闭；非法或空路径返回 None，主流程不受影响。
- pipeline 在提取 IDE meta 后追加 `reference` 来源的 `PathCandidate`，不覆盖 OCR path。
- scoped repair / consistency audit 的 `related_snippets` 接入参考源码片段，作为只读证据传入 LLM prompt。

验证：
- `python -m pytest tests/processing/test_code_context.py tests/llm/test_code_repair.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/api/test_code_mode_e2e.py -q`：通过，43 passed。
- `ruff check backend/docrestore/processing/code_context.py backend/docrestore/llm/code_repair.py backend/docrestore/pipeline/config.py backend/docrestore/api/schemas.py backend/docrestore/pipeline/pipeline.py tests/processing/test_code_context.py tests/llm/test_code_repair.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py`：通过。

遗留问题：
- 当前 snippet 检索基于 token 命中和文件路径启发式；后续可加离线 symbol index 和 import/include 图提高召回质量。

## 2026-05-15 04:26:34 CST - AGE-58 代码模式质量改进子任务收口

完成内容：
- AGE-61 至 AGE-69 子 issue 已全部完成并在 Linear 标记 Done。
- 已提交并评论每个子 issue 的 commit URL，覆盖质量报告、来源建模、文件分组、噪声过滤、多语言诊断、scoped repair、全文一致性审计、column 二次 OCR 和可选代码库上下文。
- AGE-69 提交 `b0e87bcf0cba5e894384829e504628de079629c8`，补齐离线 `CodeContextProvider` 后，AGE-58 当前实现路径已闭环。

验证：
- AGE-69 相关测试：`python -m pytest tests/processing/test_code_context.py tests/llm/test_code_repair.py tests/test_code_restore_config.py tests/api/test_create_task_code_mode.py tests/api/test_code_mode_e2e.py -q`：通过，43 passed。
- AGE-69 相关 ruff：通过。
- AGE-69 commit hook：`mypy --strict`、`ruff`、`typos` 通过。

遗留问题：
- AGE-58 父 issue 建议进入 review/验收阶段；需要用真实代码图片任务开启 `secondary_column_ocr` 与可选 `context_root` 后跑一轮对比，确认 OCR 偏差、路径合并和 LLM 修复质量是否达到预期。

## 2026-05-19 00:17:22 CST - 代码诊断审查标注与依赖错误降级

完成内容：
- `CodeDiagnostic` 增加 `items` 结构化行级标注和 `dependency_errors`，用于前端审查波浪线与 tooltip。
- C/C++ 诊断 target 支持 `include_root`，renderer 与内存诊断会把输出根目录传给 `gcc/g++ -I`，避免生成文件间的本地 include 被误判为缺失。
- 缺失头文件类错误分类为 `dependency_dirty` / `dependency`，不再作为 `syntax_dirty` 驱动 LLM 语法修复。
- files-index 前端 schema 增加 `diagnostic` 和 `diagnostic.items`；CodeViewer 使用结构化诊断渲染红色/黄色/语义波浪线和行 tooltip。
- 用 `/tmp/docrestore_02bca34c` 的 `widget_decode_helper.cc` 验证：诊断越过本地生成头文件，停在外部 `base/compiler_specific.h`，状态为 `dependency_dirty`。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py -q`：通过，20 passed, 1 skipped。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py backend/docrestore/output/code_renderer.py tests/processing/test_code_diagnostics.py tests/output/test_code_renderer.py`：通过。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `npx vitest run tests/components/CodeViewer.test.tsx`：通过，5 passed。
- Playwright 打开 `http://127.0.0.1:5173/` 完成截图验证；页面自身 API 502 来自未启动后端，不影响本次静态 UI 渲染检查。

遗留问题：
- 第二轮 stub header 复诊断尚未实现；当前只把真实缺失依赖降级为审查标注。后续可在 dependency pass 后生成空 stub 继续暴露被 include 阻挡的真实语法 OCR 错误。
- GN/BUILD 文件仍缺专门诊断与文件名大小写归一，可作为下一步独立优化。

## 2026-05-19 12:28 CST - 代码模式多语法错误复诊断

完成内容：
- `CodeDiagnosticRunner` 增加恢复式复诊断：首轮语法错误后，在临时副本中屏蔽已定位错误行并重复运行解析器/工具，继续收集后续独立语法错误。
- Python/JSON/TOML 解析型诊断改为多轮收集；Python 对疑似复合语句头会同步清空缩进 suite，避免残留缩进错误遮挡后续顶层错误。
- C/C++、JS/TS、Go、Rust 等外部工具诊断在语法错误场景下复跑临时副本，合并去重后的多条 `diagnostic.items`。
- 前端 `CodeViewer` 增加多条 syntax diagnostic 回归测试，确认多处红色波浪线和 tooltip 都能渲染。
- `docs/zh/known-issues.md` 新增“代码语法诊断不能只停在首个错误”条目，沉淀本次处理策略。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，12 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，6 passed。
- `npm run lint`：通过。
- `npm run typecheck`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。

遗留问题：
- 复诊断是行级恢复策略，能暴露后续独立语法错误，但对跨多行未闭合括号/字符串等强耦合错误仍可能只能保留已收集结果。

## 2026-05-19 18:27 CST - 代码编辑实时诊断与可接受标注

完成内容：
- 修正复诊断策略：C/C++ 缺失 include 这类 dependency 行也会在临时副本中屏蔽后继续检查，避免只停在第一个头文件错误。
- 新增 `POST /tasks/{task_id}/code-diagnostics`，对代码模式源文件草稿做只读实时诊断，不保存文件。
- `CodeViewer` 编辑态增加 350ms debounce 实时语法检查；诊断结果同步到行号 gutter 和诊断列表。
- 用户可按条“接受此诊断”，例如代码片段中可接受的缺失头文件；接受记录按任务、文件、行内容和诊断信息写入浏览器 localStorage，并可一键恢复。
- 保存代码文件后会把当前实时诊断回写到前端索引状态，避免保存后仍显示旧诊断。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，13 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py backend/docrestore/api/routes.py backend/docrestore/api/schemas.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，7 passed。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。
- Vite + Playwright 打开 `http://127.0.0.1:5173/` 并截图 `docrestore-code-diagnostics-home.png`，控制台无 error。

遗留问题：
- 编辑态红色/黄色标注当前落在 gutter 和诊断列表；原生 textarea 内部无法直接画逐行红色波浪线，若要做到完全 IDE 式内联波浪线，需要后续替换为 CodeMirror/Monaco 等编辑器组件。

## 2026-05-20 18:27 CST - OCR 中文噪声语法标注补强

完成内容：
- 复现 `/tmp/docrestore_02bca34c/files/app/core/widget/gles2_dmabuf_to_egl_image_translator.cc` 漏标：当前诊断只返回 3 个缺失 include，未到第 90 行 `if(hEglImage 二 EGL_NO_IMAGE_KHR){ 王`。
- 在工具诊断后追加不依赖编译器的 OCR 噪声词法扫描：忽略注释、块注释和字符串，只扫描代码区 CJK / 全角字符。
- 噪声扫描结果以 `syntax` / `ocr_noise_non_ascii` 合并进 `diagnostic.items`；即使编译器被 include 或语义错误短路，也能标出代码区中文/全角 OCR 噪声。
- 前端补充回归：接受 include 依赖诊断后，后续 OCR 噪声语法诊断仍保留显示。
- 真实文件验证：`gles2_dmabuf_to_egl_image_translator.cc` 现在返回 line 90、column 15、`ocr_noise_non_ascii`，消息包含 `OCR noise character '二' appears in code`。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，14 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，8 passed。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。

遗留问题：
- 词法扫描目前只报每行第一个 OCR 非 ASCII 噪声字符；同一行多个噪声可通过修复第一处后再次诊断暴露。

## 2026-05-20 18:35 CST - 编译器复诊断 include stub 落地

完成内容：
- 确认上一版并未真正生成缺失头文件 stub，只是错误地把 dependency 行号当作当前 `.cc` 行去屏蔽；对 include 链路里的缺失头文件无效。
- `_collect_additional_tool_diagnostics` 改为从 `missing_include` 诊断消息提取头文件路径，在临时 `__include_stubs__` 下生成 stub header，并把该目录加入编译器 `-I`。
- C/C++ 复诊断增加临时 prelude header，提供常见 EGL/GLuint 等占位类型，减少缺依赖造成的无效阻塞。
- 复诊断现在会迭代新增缺失 include stub，同时继续收集编译器后续语法错误，再把 dependency 和 syntax items 合并去重。
- 真实文件验证：`gles2_dmabuf_to_egl_image_translator.cc` 诊断从只返回 3 个 dependency，变为包含 include dependency、编译器后续 syntax errors，以及 line 90 的 `expected unqualified-id before 'if'` 和 `ocr_noise_non_ascii`。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/processing/test_code_diagnostics.py -q`：通过，14 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/processing/code_diagnostics.py tests/processing/test_code_diagnostics.py`：通过。
- `./node_modules/.bin/vitest run tests/components/CodeViewer.test.tsx`：通过，8 passed。
- `npm run typecheck`：通过。
- `npm run lint`：通过。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/output/test_code_renderer.py tests/llm/test_code_repair.py -q`：通过，24 passed, 1 skipped。

遗留问题：
- 生成的 stub header 只为复诊断暴露更多语法错误，不代表依赖语义真实存在；前端仍应允许用户把缺依赖标注为可接受。

## 2026-05-20 19:44 CST - CodeLLMRefiner 分块避免 token 截断

完成内容：
- 修复代码模式 `CodeLLMRefiner` 整文件 refine 容易触发 `finish_reason=length, raw_len=0` 的问题。
- `refine` 模式新增按行/字符数自动切块：超过 80 行或 3500 字符的 SourceFile 分块调用 LLM，避免输出 JSON 超出 provider token 上限。
- 每个 chunk 仍保持行数守恒；单个 chunk 截断、JSON 失败或行数变化时，只回退该 chunk，不再导致整个 SourceFile 回退原文。
- 分块结果会合并 corrections / unresolved，并按原文件行号偏移；flags 增加 `code.refine.chunked=N`、`code.refine.chunk_truncated=i` 等审计信息。
- `rewrite` 模式暂不自动分块，因为 rewrite 允许重排行，盲切会破坏语义边界。
- 已更新 `docs/zh/known-issues.md`，记录 CodeLLMRefiner 整文件截断处理策略。

验证：
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/llm/test_code_refine.py -q`：通过，21 passed。
- `/home/lyty/work/ai/env/anaconda3/bin/python -m pytest tests/llm/test_code_refine.py tests/llm/test_code_repair.py tests/output/test_code_renderer.py tests/pipeline/test_quality_report.py -q`：通过，66 passed, 1 skipped。
- `/home/lyty/work/ai/env/anaconda3/bin/ruff check backend/docrestore/llm/code_refine.py backend/docrestore/processing/code_diagnostics.py backend/docrestore/api/routes.py backend/docrestore/api/schemas.py tests/llm/test_code_refine.py tests/processing/test_code_diagnostics.py`：通过。

遗留问题：
- 当前分块按行数/字符数切，不做函数级语法边界识别；后续可用诊断窗口或轻量 parser 进一步按函数/类边界切分。

## 2026-05-22 12:36 CST - 文档事实源整理与代码模式现状同步

完成内容：
- 扫描 `docs/`、后端、前端、测试和脚本结构，确认会影响开发判断的过期点主要集中在文档入口、模块索引、代码模式 API/前端/processing 说明。
- 更新根 README、`docs/README.md` 和 `docs/zh/README.md`，明确 `docs/zh/` 当前模块文档优先，`progress.md` 是迭代流水，AGE / references 默认是历史设计记录。
- 更新系统架构和后端索引，把代码模式当前链路补齐为 `PageOCR.text_lines` → IDE 布局 → 代码栏组装 → SourceFile 分组 → LLM 精修/修复 → 轻量诊断 → `files/` / `files-index.json`。
- 更新 `processing.md`、`data-models.md`、`api.md` 和 `frontend/features.md`，补齐 `CodeRestoreConfig`、`CodeDiagnostic`、代码模式文件 API、实时诊断 API、CodeViewer 编辑态诊断与接受诊断机制。
- 修正根 README 和历史专题文档中的旧链接，为 AGE-8 / AGE-58 文档加历史状态提示，并更新 `CodeViewer.tsx` 顶部注释，避免旧描述影响代码开发。

验证：
- `rg --files docs backend frontend tests scripts .codex`：完成工程结构扫描。
- `git diff --check`：通过。
- `npm run typecheck`：通过。

遗留问题：
- 英文文档仍有较多历史 AGE / references 表述，本次只在双语总入口标明事实源优先级；后续如需要对外发布英文文档，应单独同步当前代码模式 API 和前端说明。
- AGE 历史文档未逐篇改写，保留为设计记录；开发时仍应以模块文档和代码为准。

## 2026-05-28 CST - `_split_by_compatible_dir` 回退全连接为单连接（消除 OCR dir 噪声拆桶回归）

完成内容：
- 用户对照 baseline `12d71f4c` 与回归运行 `3b556ce7` 的 `.quality_report.json`、`files-index.json`，定位 Chromium VDA 数据集文件数 7→14、warn 20→37 的回归。
- 锁定根因：`f8d16c1` 把 `code_file_grouping._split_by_compatible_dir` 的兼容判定从单连接 `any` 改为全连接 `all`，本意堵"空 dir 桥接 a/x↔b/x"边缘 case，但实际让 OCR 把面包屑 `/` 误识为 `7`、漏识、多识空段产出的同源 dir 噪声变体（如 `media7gpu7openmax/...h`、`media/gpu/openmax/-/...h`）无法再借空 dir 桥接回 canonical 主桶，被拆成 1–2 张照片的孤立小桶独立丢给 audit/repair，触发截断 + 编译失败。
- 验证机制：用 baseline `e74eab0` 与 HEAD 的 `code_file_grouping.py` 各自跑同一份 PageColumn 输入，文件数分别 10 vs 14，差 4 个 dir 噪声变体；确认 `f8d16c1` H1 是唯一回归源。
- 中间试过方案 A（在 `_merge_near_duplicate_filenames` 之前加一道 `_merge_near_duplicate_dirs` 兜底合并），但用户判定"在屎山上打补丁"不优雅，回退。
- 选择直接 revert H1：把 `any` 改回（保留 `f8d16c1` H3 `_disambiguate_duplicate_paths` 唯一后缀修复，它是独立真实问题），在 `_split_by_compatible_dir` 注释里写明取舍依据（OCR 噪声常态压倒 a/x↔b/x 理论边缘 case），删除 `test_empty_dir_does_not_bridge_different_dirs` 回归用例。
- revert 后用户重跑得到 `80d16349`：文件数 14→7、warn 37→21、audit.truncated 10→5、repair.truncated 9→5、diagnostic.syntax_dirty 12→5，全面恢复 baseline。
- 提交 `f6b06a8`，pre-commit mypy/ruff/typos 均通过。

验证：
- `python -m pytest tests/processing/test_code_file_grouping.py -q`：20 passed, 3 skipped（age8-spike fixture 已删）。
- `python -m pytest tests/processing tests/output tests/pipeline -q`：460 passed, 72 skipped。
- 用同一份 OCR 数据跑 `group_into_files` probe：文件数从 14 降回 10，4 个噪声变体（`media7gpu7openmax/...h`、`media7gpu/openmax/...h`、`media/gpu/openmax/-/...h`、`media/gpu/openmaxom/bu/...cc`）被正确并回 canonical 主桶。
- 真实 pipeline 跑 `80d16349`：files/ 树与 baseline 100% 一致，document.md 仅差 4 行（缩进微调）。

遗留问题：
- `80d16349` 中 `openmax_status.h` 出现 LLM `repair.truncated`（baseline 是 `repair.applied=1`），导致 OCR 噪声字符（如 `王`、枚举名首字母 `E` 误识为 `F`、`StatusTraits {` 误识为 `StatusTraits Y` 等）残留——属 LLM 输出长度抖动，与本次 revert 无关；可考虑独立排期 `code_repair` 的 truncated 重试或 max_tokens 自适应策略。
- baseline 也合不到的 3 个剩余 noisy 变体（`media/gpu7openmax/...h`、`media/openmax_..._accelerator.h`、`media/openmax_..._accelerator.cc`）是 `_dirs_compatible` 旧规则盲区（compact 既不等也不互为后缀且不空），不在本次回归范围；若未来要堵 a/x↔b/x 桥接，正确做法是先在 `_dirs_compatible` 内为非空 compact 加 Levenshtein ≤ 2 容忍再换全连接（方案 B），不要直接砍单连接。

## 2026-06-03 CST - PPT 还原模式 S1 设计定稿 + OpenSpec 生成（AGE-85）

> **注（2026-06-04 更新）**：项目随后弃用 OpenSpec，`openspec/` 已整体删除、PPT 设计真相源回归 `docs/zh/ppt-mode.md`。本条为历史记录，其中 OpenSpec 相关步骤（change / spec / validate）均已废止。

完成内容：
- 基于 S0 选型结论（AGE-84：VL-1.6 主引擎 + 透视矫正必需 + 化学结构裁图，剔除 MinerU/dots），产出 PPT 模式 S1 设计文档 `docs/zh/ppt-mode.md`（约 560 行，含三模式分支架构组件图 + 端到端流水线活动图，均经 PlantUML 真编译验证）。
- 架构定位：流式 Pipeline 第三消费者分支 `_ppt_pipeline`，与文档/代码模式互斥三选一，共享 `_ocr_producer` + `page_queue`。链路 S2 透视矫正(逐页前处理 hook) → S3 VL-1.6 doc_parser 识别+自动裁图 → S4 逐页保序组装合并 document.md。
- 关键决策（用户 2026-06-03 拍板 6 项）：A 不跨页去重（每页独立幻灯片）；B LLM 轻润色默认关 + 前端透出开关（按 OCR 效果决定）；C 前端 radio 三选一互斥（替代 codeMode toggle）；D 信任 VL 阅读序、留 S3 实测回退 region bbox 排序；E 页间分隔线 + page marker；F DB ppt 列同 code 列机制。
- 复用 `PageOCR`（VL markdown 入 raw_text、裁图入 Region.cropped_path）、`PipelineResult`、两阶段图片引用、前端多文档展示；净新增 `slide_rectify.py` + `ppt_renderer.py` + 1 config + 1 request schema + 1 消费者分支 + producer hook + 前端三选一 + DB migration（工程量评估「刚刚好」）。设计文档 §7 已列 17 处接入点 文件:行。
- 首次在项目引入 OpenSpec（CLI v1.2.0，`openspec init --tools claude`）：生成 change `add-ppt-restore-mode`，含 proposal/design/tasks + 4 个 capability spec（ppt-perspective-rectify / ppt-page-recognition / ppt-document-assembly / ppt-mode-integration，对应 S2–S5）。
- 同步 `docs/zh/architecture.md`（§3 数据流加 PPT 分支、§6.3 扩展边界加指针，均标注「设计中」）。

验证：
- `bash ~/.claude/skills/plantuml-in-markdown/scripts/extract_and_compile.sh docs/zh/ppt-mode.md`：2 张图 exit 0、非空 PNG。
- `openspec validate add-ppt-restore-mode --strict`：valid，退出码 0（0/32 tasks）。

遗留问题：
- OpenCV(cv2) 既未在 `pyproject.toml` 声明也未在生产 env 安装 → S2(AGE-86) 接入第一步必须补（tasks 1.1）。
- B/D 两项留实测口：VL 单页阅读序可靠性、LLM 轻润色收益均待 S3 真图实测决定。
- 下一步：S2（AGE-86）透视矫正开工（待用户确认）。

## 2026-06-03 CST - PPT 模式 S2 透视矫正落地（AGE-86）

完成内容：
- 新增 `backend/docrestore/processing/slide_rectify.py`：`detect_slide_quad`（Otsu 亮区→最大外轮廓→approxPolyDP 取4角）/ `rectify`（warpPerspective 转正视图 + 顶边上抬 20% 补暗标题栏）/ `rectify_page`（异步入口 `asyncio.to_thread`，落盘 `.rectified/` before/after，失败回退原图不中断）。
- 模块纯图像处理、不依赖 `PipelineConfig`（解耦）；pipeline 接入（`_ocr_producer` 逐页 hook）留 S5/AGE-89 tasks 4.7。
- 依赖 `opencv-python-headless` 加进 `pyproject.toml` + 装入 docrestore env（cv2 4.13.0）；cv2 加 mypy override。
- 单测 `tests/processing/test_slide_rectify.py` 8 例：合成图确定性（检测/角点排序/矫正/小亮块过滤）+ `rectify_page` 落盘与回退；断言从输入派生。

验证：
- `pytest tests/processing/test_slide_rectify.py`：8 passed。
- 真图 `test_images/PPT` **9/9 全命中**：1706×1279 屏摄 → ~1200×970 正视图，强透视拉正、标题栏完整、吊顶/观众裁掉（before/after 对照留存 `/tmp/ppt_rectify_evidence`）。
- processing 全量 284 passed 无回归。
- commit `01ad20b`（分支 `feature/ppt-restore-mode`）；AGE-86 → Done。

遗留问题：
- 矫正对当前 9 张屏摄 100% 命中；更复杂场景（下边缘被观众严重遮挡、屏幕强反光）鲁棒性待 S6 全量/更多数据验证。
- pipeline 接入 + `.rectified/` 打包排除留 S5。

## 2026-06-03 CST - PPT 模式 S3 VL doc_parser 识别验证（AGE-87）

完成内容：
- 起 PaddleOCR-VL-1.6 vllm-server（`EngineManager.ensure`，启动 ~50s），对 S2 矫正后 3 张真图跑 `doc_parser`，验证 S1 设计 §11-C/§14-D 关键假设。
- **化学结构裁图覆盖** ✓：化学骨架式/SMILES 反应路径裁成 `images/*.jpg`（HTML img 引用），不误转文字（501 页裁 2 张、503 页裁 11 张）。
- **公式 LaTeX** ✓：数据表内 kcat 单位 `$1/s$`、`$^{+}$` 转 LaTeX；数据表识别为 HTML table（Entry/Gene/Organism/kcat/EC 共 7 列，EC 号准确）。
- **阅读序可靠** ✓：标题→正文→图→表→说明顺序正确 → **决策 D 确认：信任 VL 阅读序，不引入 region bbox 排序**（§11-C/§14-D 关闭）。

验证：
- 3 张矫正图 OCR 成功：raw_text 414/269/2365 字，regions 2/1/11；产出 `{stem}_OCR/result.mmd` + `images/`。
- GPU 干净释放（server shutdown 无 vllm 孤儿残留）；证据留存 `/tmp/ppt_ocr_out`。

遗留问题：
- 2.1（PPT OCR 配置确保 vl、不走 code 强制 basic）属 S5 `ocr_effective` 分支代码，留 S5/AGE-89。
- 本次验证 3 张（化学页 + 数据表页）；全量 9 张及更多版式覆盖在 S6 E2E。

## 2026-06-03 CST - PPT 模式 S4 ppt_renderer 多页保序合并（AGE-88）

完成内容：
- 新增 `backend/docrestore/output/ppt_renderer.py::render_ppt_document`：单页按 VL 阅读序组装 → 多页按输入文件序合并单 `document.md`，**不跨页去重**（每页独立幻灯片），复用 `Renderer.render` 做图片复制/marker 处理/写盘。
- 重构 `processing/dedup.py`：抽出 module 级 public `rewrite_image_refs_to_ocr_dir`（图片引用加 `{stem}_OCR` 前缀），`PageDeduplicator._rewrite_image_refs` 委托，文档/PPT 模式共用单一真相源。
- **修 `output/renderer.py` 图片正则不支持中文/Unicode 文件名 bug**：`[A-Za-z0-9_.]+` → markdown `[^/)]+` / HTML `[^/]+`，文档模式同受益；已记 `known-issues.md`。
- LLM 轻润色（3.4）移至 S5 `_ppt_pipeline`（render 纯组装）。

验证：
- 单测 `tests/output/test_ppt_renderer.py` 5 例：保序 / 不跨页去重 / HTML img 重写+复制 / marker 磁盘去内存留 / 分隔线。
- 回归 tests/output + pipeline + processing/test_dedup **223 passed**（含 renderer 正则改动）。
- **真实端到端**（S2 矫正 → S3 VL doc_parser → S4 组装）：3 页真实 PageOCR → `document.md` 3840 bytes，保序合并（FGRFP→NeoPathTP→REME）+ 分隔线 + 5 张中文文件名裁图复制 + 数据表 HTML/LaTeX + 化学结构图引用，阅读序正确。
- commit `0fbd7f6`（分支 `feature/ppt-restore-mode`）。

遗留问题：
- LLM 轻润色在 S5 `_ppt_pipeline` 接入。
- 真实验证 3 页；全量 9 页 + 多版式 E2E 在 S6。

## 2026-06-03 CST - PPT 模式 S5 全栈接入（AGE-89）

完成内容：
- **后端**（commit `13ace55`）：`config` PowerPointRestoreConfig + PipelineConfig.ppt；`schemas` 请求 schema + CreateTaskRequest.ppt；`errors` APIErrorCode.MODE_CONFLICT；`routes` ppt_cfg 合成 + code/ppt 互斥校验；`task_manager` Task.ppt + `database` DB ppt 列 migration（同 code 列机制）；`pipeline` process_tree/process_many/_stream_pipeline 签名加 ppt + `elif ppt_cfg.enable` → `_ppt_pipeline` + `_ocr_producer` 矫正 hook（逐页 rectify_page，page.image_path 改回原图名）+ 新增 `_ppt_pipeline`（保序组装，润色占位）。
- **前端**（commit `2b12ddd`）：TaskForm codeMode toggle → mode radio 三选一（文档/代码/PPT 互斥）+ PPT 润色开关；useTaskRunner/client ppt 透传；i18n 3-locale；App.css radio 样式。
- `slide_rectify` ImageBGR 改 `cv2.typing.MatLike`（适配 opencv 4.13 自带 stub）。

验证：
- 全量 backend mypy **Success（65 files）**；后端 **613 passed** 无回归。
- 前端 `tsc -b` + eslint 通过；**playwright 视觉验证**：radio 三选一并列、选 PPT 切换描述 + 显示润色开关。

遗留：
- 4.11 下载打包排除 `.rectified/` 留 S6（点目录，影响小）。
- LLM 轻润色 `_ppt_pipeline` 占位（开启记 warning 未接入）；完整实现后续。
- 下一步 S6（AGE-90）：全量 9 图 E2E + 质量门禁 + 文档收尾。

## 2026-06-03 CST - PPT 模式 S6 全量 E2E + 质量门禁（AGE-90，父 AGE-83 闭环）

完成内容：
- 完整 Pipeline 跑 PPT 模式 on `test_images/PPT` 全 **9 图**（`process_tree(ppt=PowerPointRestoreConfig(enable=True))`，与前端建任务同链路）：9 图屏摄 → 矫正（`.rectified/` 18 张 before/after）→ VL `doc_parser` 识别 → `_ppt_pipeline` 组装。
- 产出 `document.md` 9636 bytes，**9 页 marker 全保序**（页序 = 输入文件序），14 张裁图复制，无错误；三类内容齐全（文字 + 公式 LaTeX + 化学/表格裁图引用）。
- 4.11 确认：下载打包白名单仅 `document.md` + `images/**`，`.rectified/` 天然排除，无需改动。

验证：
- **质量门禁全绿**：backend mypy --strict Success(65) + ruff All passed + typos OK + 后端 613 passed；前端 `tsc -b` + eslint + playwright 视觉验证。
- GPU shutdown 干净释放（15 MiB）。
- 父 AGE-83 + S0–S6（AGE-84~90）全部 Done。

遗留：
- LLM 轻润色 `_ppt_pipeline` 占位（开启记 warning 未接入）；完整实现后续。
- 英文文档 `docs/en/` PPT 模式同步留后续。
- **PPT 还原模式 S0–S6 全部完成**；`feature/ppt-restore-mode` 待合并 `dev`。

## 2026-06-03 CST - max-effort code-review 第二轮修复（复审上一轮提交，4 项）

背景：对上一轮提交（统一精修 + 首轮 4 修）再跑 max-effort code-review，发现 4 项并按用户确认修复。

完成内容：
- **#1 隐私回归（最高优先级）**：统一 `enable_refine` 拦截点在 `_get_refiner` 无条件返回 None，连带关掉 PII 实体检测（`_delayed_pii_detect`）与代码头脱敏（`_redact_code_headers`）的 LLM 客户端 →“关精修 + 开脱敏”时人名/机构名泄漏。修法：`_get_refiner(llm, *, for_refine=True)`，PII 等用途传 `for_refine=False`（只看 model）；`initialize` 预建 refiner 去掉 `enable_refine` 前置；代码模式 base_refiner 改 `for_refine=False`，精修两段另按 `enable_refine` gate。
- **#2 PPT 不跨页去重**：新增 `SLIDE_REFINE_SYSTEM_PROMPT`（只修格式、保留公式/裁图、不去重）；`RefineContext.is_slide` → `build_refine_prompt` 选 slide prompt；`_refine_segment_with_cache(slide_mode=True)` 透传 + 4 处重试 ctx 带 `is_slide`；缓存独立 `slide` 命名空间。
- **#3 `_order_corners` 旋转误标**：改“y 排序分上下、组内分左右”（取代上一轮极角 + x+y 锚点），旋转下标号仍正确，回归断言标号正确。
- **#4 `rectify` 退化 sliver**：新增 `_MIN_RECTIFIED_SIDE_PX=16`，任一边低于阈值回退原图，不产竹签图喂 OCR。
- **顺带前端 UI**：处理模式三选一与 LLM Provider 选择统一为「整框背景染色 + 无 radio 小圆点」的分段按钮样式（`.mode-radio-option--active` 比照 `.llm-provider-option--active`，两处 radio input 视觉隐藏但保留可聚焦）。Playwright 截图验证选中态整框染色且点击切换正确。

验证：
- backend `mypy --strict` Success(65) + `ruff` All passed + `typos` OK；**pytest 1011 passed, 45 skipped**（新增 11 个回归用例：get_refiner for_refine 解耦 ×4 / prompts slide 选择 ×2 / cache slide 命名空间 ×2 / ppt_refine is_slide ×1 / slide_rectify 旋转标号 + sliver ×2）。
- 前端未改动（`enable_refine` 已透出），沿用上次门禁绿。

遗留：
- 低优先级 review 项记入 `known-issues.md`：关精修仍报“精修第 X 页”文案、`progress.pptDone` 死键、`_ocr_config_*` 克隆、retry 无 PPT 兜底、关精修建空 `.llm_cache/`。
- 英文文档 `docs/en/` 同步仍留后续。

## 2026-06-04 CST - review 第二轮低优先级 5 项清理

完成内容（清空上一条「遗留」里的低优先级 review 项）：
- **进度文案**：`_ppt_pipeline` 按 `refining` 分支——关精修报 `progress.pptPagePlain`「处理第 X 页」，不再误报「精修第 X 页」。
- **死 i18n 键**：删除从不发射的 `progress.pptDone`（en/zh-CN/zh-TW）；新增 `progress.pptPagePlain`（三语）。
- **去克隆**：`_ocr_config_for_code_mode` / `_ocr_config_for_ppt_mode` 合一为参数化 `_ocr_config_force_pipeline(ocr, default_ocr, pipeline_name)`，两薄封装分别传 basic / vl。
- **retry PPT 兜底**：新增 `TaskManager._retry_ppt_config`（对称 `_retry_code_config`），`task.ppt` 为空时用 `output_dir/.rectified/` 推断回 PPT 模式；retry/resume 改走它。
- **空缓存目录**：`_ppt_pipeline` 段级缓存 `enabled=enable_cache and (refiner is not None)`，关精修时不再建空 `.llm_cache/`。

验证：
- backend `mypy --strict` Success(66) + `ruff` + `typos` + 前端 `tsc -b` + `eslint` 全绿；**pytest 1016 passed, 45 skipped**（新增 5 用例：retry/resume PPT 兜底 ×2、`_ocr_config_for_ppt_mode` 强制/穿透 ×3；并扩充 `test_ppt_refine_disabled_skips` 断言 pptPagePlain + 无 `.llm_cache/`）。
- `known-issues.md` 对应「暂缓」清单改记为「已清理（2026-06-04）」。

遗留：
- 前端 `TaskProgress` stage 标签未本地化 `ppt_refine`/`ppt_render`/`ppt_page` 仅为次要技术 token 展示（message_key 主文案已本地化），后续顺手再清。
- 英文文档 `docs/en/` 同步仍留后续。

## 2026-06-04 CST - max-effort code-review 第三轮（复审 52a9f4c+806f7d9）4 修 + 前端韧性

背景：对前两笔未推送提交再跑 max-effort code-review（9 路 finder + 逐条源码核实），修复 4 项并按用户确认处理。

完成内容：
- **#2 键盘焦点回归**：模式 / Provider 选择器隐藏原生 radio 后无焦点指示（WCAG 2.4.7）。给 `.mode-radio-option` / `.llm-provider-option` 各加 `:focus-within { outline }`，键盘 Tab/方向键导航整框可见。
- **#3 slide 重试引用不存在规则**：UI 噪音重试提示硬编码「请按 system 规则 11-13」，但 `SLIDE_REFINE_SYSTEM_PROMPT` 只有规则 1-8。修法：slide prompt 新增规则 8（页内 UI 噪音清理：`复制代码`/工具栏行，原规则 8→9）使代码截图幻灯片首轮即清；重试提示去掉规则编号、改自带删除指令（对文档版 11-13 与 slide 版规则 8 都成立）。
- **#4 `_retry_ppt_config` 兜底**：retry/resume 改 `ppt = _retry_ppt_config(task) if code is None else None`（代码/PPT 互斥，避免旧 output_dir 同时残留两类产物时 code+ppt 同传 create_task）；`.rectified/` 推断命中打 info 日志（不再静默），docstring 写明 `rectify=False`/全回退/自定义目录的局限（仅影响丢快照旧任务，新任务靠持久化 `task.ppt`）。
- **Vite 报错刷屏 + 卡死需重启（用户报）**：根因 (1) Vite 8 对 HTTP 代理错误无条件打日志（`configure` 改不掉、且已返回 502）；(2) 前端 `listGpus`/`getOcrStatus`/`listTasks` 三处挂载请求只打一次、失败即静默放弃，后端后起也不再拉 → 必须重启前端。修法：新增 `frontend/src/lib/retry.ts::retryUntilSuccess`（退避 1/2/4/8s 末值循环、卸载即停），接入三处挂载 effect → 后端就绪后界面自动恢复、无需重启。

验证：`mypy --strict` Success(66) + `ruff` + `typos` + 前端 `tsc` + `eslint` 全绿；**pytest 1019 passed, 45 skipped**（新增 3 用例：slide UI 规则 / slide 重试提示自洽 / retry 互斥优先 code）。

决策 / 遗留：
- **review #1（实体脱敏）经复核更正**：「PPT 把人名/机构名送云端」非 PPT 独有——文档模式主精修同样把人名/机构名原样送云端（实体词表只用于 gap-fill）；结构化 PII 已由 producer 正则全模式入云前脱敏。属全链路既有行为、非本 diff 引入。用户拍板「全链路精修前脱敏（流式+输出兜底）」，设计已落 `docs/zh/backend/privacy.md §9`（项目自有文档体系，不走 OpenSpec），待实现。
- #2 焦点态 CSS 未跑 Playwright 截图验证（需起整套栈 + 键盘 Tab），待补。
- 英文文档 `docs/en/` 同步仍留后续。

## 2026-06-04 CST - #1 全链路实体脱敏前置（流式+输出兜底）落地

背景：max-effort review #1 复核确认人名/机构名实体脱敏只覆盖 gap-fill、未覆盖主精修与输出（全链路缺口）。用户拍板「全链路精修前脱敏（流式+输出兜底）」，设计落 `backend/privacy.md §9`（弃用 OpenSpec）。本条为实现。

完成内容：
- **核心**：`_refine_segment_with_cache` 加 `redactor`/`entity_lexicon` kwargs，缓存查找前 `redact_snippet`（缓存键用脱敏后文本，resume 一致）；文档主分段与 PPT 按页共用此入口。
- **助手**：抽 `_detect_entities(text, llm, pii_cfg)`，`_delayed_pii_detect` 委托它；PPT 与短文档兜底复用。
- **文档模式**：`_stream_process` 建 redactor + 透传 lexicon 到 `_try_extract_and_refine`/尾段；页数不足阈值结尾补建词表；`_finalize_single_doc` render 前对 `doc.markdown` 输出兜底。
- **PPT 模式**：`_ppt_pipeline` 接 `pii_cfg`，积累页文本到阈值建词表、每页精修前应用，组装前对 `bodies` 输出兜底（覆盖早窗口页）；调度处传 `pii_cfg`。
- **约束如实保留**：实体检测本身把文本送所配置 refiner（云端则上云一次），要名字完全不出本机需配 local provider。

验证：`mypy --strict` Success(66) + `ruff` + `typos` + 前端 `tsc`+`eslint` 全绿；**pytest 1025 passed, 45 skipped**（新增 `tests/pipeline/test_entity_redaction.py` 6 用例：核心脱敏 / 无词表不改 / 检测开关 ×2 / PPT 输出兜底 / 关脱敏零改动且不调检测）。`privacy.md §9` 标已落地、`known-issues.md` 对应条目标已闭环。

遗留：
- #2 焦点态 CSS 仍未 Playwright 截图验证（待补）。
- 英文文档 `docs/en/` 同步仍留后续。

## 2026-06-04 - code-review max 全量审查 → GitHub issues (#8–#22) + 修复前 3 项（#10/#8/#9）

**背景**：对全项目做 max-effort code-review（9 维度 finder 并行 + 逐条 Read 核验），
聚焦控制流/数据流的传输中断与异常，确认 15 个真实问题。用户决定从 Linear 切到
GitHub 管理，已建 issue #8–#22 + Milestone「code-review 修复（控制流/数据流）」
（severity/area/code-review 标签）。本条为按优先级修复前 3 项（同一异常路径根因簇）。

**修复**：
- **#10（CRITICAL，privacy）**：`block_cloud_on_detect_failure`（默认 True）声明即死代码、
  零读取 → 实体检测失败时仍把含人名/机构名的整段送云端。新增 `Pipeline._should_block_cloud`
  （仅"开 PII + 要求人名/机构名脱敏 + 检测返回 None + flag 真"时阻断），文档模式
  `_stream_process` 与 PPT 模式 `_ppt_pipeline` 检测失败即置 `refiner=None`，
  `_finalize_single_doc` 加 keyword-only `block_cloud` 跳过 gap fill + final refine 两处云端调用。
- **#8（HIGH，中断）**：`_stream_pipeline` 旧 `finally: await ocr_task` 不取消生产者 →
  消费者提前退出后生产者跑完所有图才结束（阻塞 shutdown / 遗弃任务 / GPU 空转）。改
  try/except/finally：except 先 `ocr_task.cancel()` 再 suppress await、保留原异常上抛；
  成功路径保留 try 外 await 让生产者真实异常浮现。
- **#9（HIGH，异常）**：`_call_llm` 用 `except Exception` 不覆盖 CancelledError →
  HALF_OPEN 探测被取消时 `_probe_in_flight` 永久泄漏、全局熔断卡死。新增
  `LLMCircuitBreaker.on_probe_aborted` 清占位（不计成功/失败），`_call_llm` 包 try +
  `except asyncio.CancelledError` 调用它后原样上抛。

**验证**：每项独立 commit（`Fixes #N`）+ 补测试；mypy/ruff/typos 全绿（PostToolUse + pre-commit）；
pytest 1035 passed（新增 `test_entity_redaction.py` +6 / `test_producer_cancel.py` +1 /
`test_circuit_breaker.py` +3）。pipeline 套件 209、llm 套件 139 全过。
（环境缺失：cv2 未装致 `test_slide_rectify` 收集失败、DeepSeek 路径未配致 3 个 ocr 用例失败，
均与本次改动无关、修复前同样失败。）

**遗留**：#11–#22 共 12 项待修（Milestone 跟踪）。代码模式 `_redact_code_headers` 的检测失败
fail-closed 未纳入本次（结构不同：头部仅本地 redact，是否经 code_refine 外发待单独评估）。

## 2026-06-04（续）- code-review 第 2 批：持久化韧性（#14/#15）

**背景**：接 #10/#8/#9 后的第 2 批，拣关联紧、风险可控的持久化两项，一个分支收掉。

**修复**：
- **#14（HIGH，persistence）**：`load_persisted_tasks` 的 try/except 只裹 `get_results`，
  `TaskStatus(row.status)` / `get_task`（含 config JSON 解析）/ `fromisoformat(created_at)` 全裸奔 →
  一条损坏/旧版行抛 ValueError 冒出分页循环、该行之后所有任务重启后从 UI 消失。把单条 row
  解析整体包进 try/except，失败 log + `continue` 跳过坏行。
- **#15（MEDIUM，persistence）**：`_persist_results` 先 `update_status`(commit) 再
  `insert_results`(commit) 两次独立事务，崩溃落中间 → "completed 但零结果"不可恢复。`database`
  加 `complete_task_with_results`（单事务 UPDATE + INSERT 一次 commit），`_persist_results` 改走它；
  抽 `_normalize_results` 复用。

**验证**：每项补测试（`TestLoadPersistedResilience` 坏行先于好行仍装回好行；`complete_task_with_results`
原子写 / 空结果各 1）；mypy/ruff/typos 全绿；pytest 1038 passed。合并后 issue 随 dev→main 关闭。

**遗留**：#11/#12/#13/#16/#17/#18/#19/#20/#21/#22 共 10 项待修。

## 2026-06-04（自查补全）- #25 代码模式 fail-closed（#10 残留）

**背景**：#10 合并后自查发现只覆盖文档 / PPT，**代码模式漏了**——`_redact_code_headers` 实体检测
失败时只 log 退化成 regex + 自定义词（header 人名/机构名未脱），且不返回失败信号，随后
`_code_pipeline` 把 `src.merged_text` 经 `code_refine / repair / audit` 送云端、无闸门。建 issue
**#25** 跟踪并当场补全。

**修复**：`_redact_code_headers` 改返回 `block_cloud: bool`（检测已尝试且失败 + flag 真）；
`_code_pipeline` 在 refine 闸门加 `and not pii_block_cloud`，跳过整段云端 refine/repair/audit
（退化为不精修的本地输出）。

**验证**：`tests/pipeline/test_code_pii_header.py::TestRedactCodeHeadersFailClosed` +4
（检测抛错→True / flag 关→False / 检测成功→False / 无 refiner→False）；全量 1039 passed。

## 2026-06-04（续）- code-review 第 3 批：API 生命周期/传输（#11/#16）

**背景**：#10/#8/#9 + #14/#15 + #25 全合并 dev 后继续推进；本批拣 API 侧两项清晰、低风险的
（shutdown 数据丢失 + WS 终结帧丢失），独立文件、两个 commit 分别闭环。用户决定 review bug
全修完再合 main。

**修复**：
- **#11（HIGH，api）**：`cleanup_all_sessions`（shutdown 调用）无条件 rmtree 所有 upload_dir，
  而 TTL 清理专门跳过被引用目录 → 重启把已持久化任务的源图擦掉。加可选 `referenced` 参数
  跳过被引用目录；`app.py` shutdown 传 `manager.collect_referenced_image_dirs()`（含所有终态任务）。
- **#16（MEDIUM，api）**：终结帧只 publish 一次、无订阅者时不缓存，`subscribe_progress` 建空
  Queue 不回灌 → 客户端在终结帧后订阅则 `q.get()` 永久阻塞。订阅时若 `task.progress` 非空即
  `put_nowait` 回灌一帧，晚到订阅者立刻拿到终态并退出。

**验证**：`test_upload.py`（被引用目录保留/未引用删除）+ `test_task_manager.py`（终结后订阅 →
队列已 seed 终结帧）；全量 1044 passed。

**遗留**：原始 15 项里待修 8 项：#12 #13 #17 #18 #19 #20 #21 #22。

## 2026-06-04（续）- code-review 第 4 批：OCR worker 传输（#18/#19）+ #17 重判

**修复**：
- **#18（MEDIUM，ocr）**：DeepSeek init 另起协程 readline 同一 `process.stderr`，与基类 stderr
  drain 并发读 → `StreamReader` 抛 RuntimeError、init 进度静默失效（潜在 drain 死亡 → pipe 写满
  挂死）。改单一读者：drain 加可选逐行 `on_line` hook（`_dispatch_stderr_line` 转 `_stderr_line_hook`），
  `_send_init_command` 装进度解析 hook、finally 摘除；删死代码 `_stream_stderr_progress`。
- **#19（MEDIUM，ocr）**：`_send_ocr_batch_all` 按 `enumerate(items_raw)` 建 results，worker 返回
  项数 < chunk 时缺页被悄悄省略 + `ocr_batch` 末尾 `if p in results` 又静默丢 → 返回页列变短、
  下游索引错位。加 `len(results)==len(chunk)` 硬校验（缺页抛错）+ `ocr_batch` 缺页即抛兜底。

**#17 重判（NOT a clean bug）**：原审"resync 复用 OCR 超时致下一页冻结数分钟、建议改短超时"——
深读 `_send_command` 发现 `_pending_resync` 是在"命令已发、worker 正在处理"时被取消才置位，残留
响应会在 worker 完成那次 OCR 时到达；**短超时会过早重启正在干活的 worker**，原建议是错的。当前
"用 OCR 超时 drain，超时才 restart"是可辩护的权衡（复用热 worker vs 重启 reload 成本）。已在 issue
#17 记录重判，倾向 wontfix 或仅加可配置中等超时，待用户定。

**验证**：`tests/ocr/test_worker_transmission.py`（drain 逐行 hook / 批量短响应抛错）；全量 1046 passed。

**遗留**：原始 15 项里待修 6 项：#12 #13 #20 #21 #22（+ #17 待定）。

## 2026-06-04（续）- code-review 第 5 批：实体输出消毒（#13）+ heading 去重连续性（#20）+ #17 关闭

**#17 关闭**：用户采纳 wontfix（方案 A），已 `gh issue close 17 --reason "not planned"` 并附重判说明。

**修复**：
- **#13（HIGH，privacy）**：LLM 实体输出未消毒即全局子串替换 → 整篇被打碎。
  - 检测侧 `cloud.py` 新增 `_coerce_str_list` 类型守卫：`person_names`/`org_names` 必须是
    `list[str]`，裸字符串 `"Alice"` 不再 `list()` 逐字符拆成 `['A','l','i','c','e']`；顶层非
    dict 按检测失败 fail-closed 抛 `RuntimeError`。
  - 替换侧 `redactor.py` `_replace_entities` 加最小长度(≥2)+非纯标点守卫(`_is_safe_entity`)，
    跳过单字"的"/纯标点幻觉；异常高频(>50)/超长(>64)实体告警仍执行。
- **#20（HIGH，processing）**：heading 去重 `_should_merge` 的 `truncated_prefix` 路径用子序列
  总和判定（散点子序列在短文本+共享词时极易达 0.9）→ 误删内容不同的同标题节。追加连续性
  闸门 `contiguous_anchor_ratio=0.5`：要求存在一段足够长的【连续】匹配块作截断锚。实测真截断
  连续块占比 0.727、散点仅 0.083，0.5 闸门两侧裕度充足；旧 0.9 子序列阈值保留不变。

**验证**：`test_cloud_truncation.py`（+裸串不拆字/混类型过滤/顶层数组抛错/`_coerce_str_list` 单测）
+ `test_redactor.py`（+`_is_safe_entity` 阈值/单字·纯标点跳过/2 字名仍替/高频告警）
+ `test_heading_dedup.py`（+散点子序列两节都保留）；全量 1061 passed（3 个 deepseek 失败为环境
缺 worker 路径，基线即失败）。

**遗留**：原始 15 项里待修 3 项：#12 #21 #22（#17 已 wontfix 关闭）。

## 2026-06-04（续）- code-review 第 6 批（收官）：取消竞态/行号误读/symlink 预览（#12/#21/#22）

**背景**：原始 15 项最后 3 项，修完即满足"全修完再 dev→main"。

**修复**：
- **#12（MEDIUM，pipeline）**：cancel_task 与 run_task（成功/取消/异常）多路并发写终态、无
  "终态即终"守卫 → "已取消任务最终 COMPLETED"或"已完成被标 FAILED"。新增单一真相源
  `_finalize(task_id, status)`（锁内重检，已终态则放弃返回 False），五处终态写全部改走它；
  抽 `_handle_unexpected_failure` 降 run_task 圈复杂度；cancel_task 在 `_finalize` 失败后重读
  状态区分 COMPLETED（取消失败）vs FAILED（取消已生效）。
- **#21（MEDIUM，processing）**：`code_assembly:144` `sorted(key=int(text))` 行号 88 误读成 8 会被
  排到列顶。复核发现 `NUMERIC_RE=^\d{1,4}$` 已挡 ValueError，真正未修的是误读重排。新增
  `_ordered_line_numbers`：排序键改 y_top（物理真相，同 ledger `_y_monotonic_outliers` 前提）+
  int() try/except 兜底 + 单调性修正（读数 ≤ 前值即误读 → prev+1 + inferred）。
- **#22（HIGH，api）**：`get_source_image` 先 `resolve()` 跟随 symlink 再校验；`_stage_files` 用
  `symlink_to` 把外部源图软链进 stage 目录 → resolve 落到 image_dir 之外 → 包含校验 False →
  整个服务端源图预览 404。改为对【未跟随 symlink】的词法拼接路径做越界校验，再用 `is_file()`
  跟随软链确认目标存在。

**验证**：`TestFinalizeRace`(#12) + `_ordered_line_numbers` 两用例(#21) +
`test_serves_symlinked_staged_image`(#22)；全量 1067 passed（cv2/deepseek 环境模块除外），
mypy --strict + ruff + typos 全绿。

**收官**：原始 15 项全部闭环（11 项已修 + #17 wontfix + 自查新增 #25 已修）。dev 满足"全修完"
条件，下一步合并 dev→main，`Fixes #N` 在默认分支自动关闭 issue。

## 2026-06-05 - 修复：新建任务完成后预览空白（需切历史再回来才出）

**现象**：新建任务跑完，结果区卡在"暂无可用结果"，进一次历史详情再切回才刷出预览。

**根因**：前端时序竞态。`TaskResult` 用"挂载时一次性吃 props + `key={taskId}` 重挂载"模式刷新，
而 `useTaskRunner` 完成路径先 `setStatus("completed")`（App 立即挂载 `TaskResult`，此刻
`allResults` 仍为 `[]`）再 `await fetchResult`；taskId 不变 → 迟到的结果永远进不来。切历史会卸载
组件，回来时以已填充数据重挂载，故"绕一圈能好"。

**修复**：`useTaskRunner` 两条完成路径（轮询 + WS 关闭兜底）统一改为「先 `await fetchResult` 再
`setStatus("completed")`」。历史详情 `TaskDetail` 走 reactive `docResults`，本就不受影响、未改。

**验证**：用户确认修复生效；`npm run typecheck` + `eslint` 全绿。详见 known-issues.md 同名条目。

**遗留**：无（`useTaskRunner` 完成时序无现成测试桩，为一行排序修复新搭 WS+fetch mock 属过度工程，
未加）。

## 2026-06-05（续）- 代码模式预览加载提速（A+B+C+D 两阶段）

**问题**：代码模式打开上千行源文件要好几秒。根因不在 fetch（纯 text()），而在 CodeViewer
只读视图整文件一次性渲染、每个语法 token 一个 `<span>`、分词写在 render 里无缓存 →
1000 行 ≈ 1.5w DOM 节点一次性 layout/paint。设计见 `docs/zh/frontend/code-viewer-perf.md`。

**Phase1（commit 2ca4e16，低风险无布局变更）**：
- A：`useMemo` 缓存整文件分词（key=content+language+path）；
- B：`visibleDiagnostics` 仅编辑态计算（只读不再 O(N·D) 空转）；
- C：`.code-line` 加 `content-visibility:auto`，浏览器跳过视口外行 layout/paint。

**Phase2（commit 688c7dd，行级虚拟化）**：
- 纯函数 `computeLineWindow`（features/task/lineWindow.ts）算可视行区间 + overscan，8 例单测；
- 固定行高 + 上下 spacer 只渲染可视窗口；page 锚点改绝对定位 overlay 与窗口解耦，
  始终在 DOM 供 `useScrollSync` 量测；rowH 挂载实测纠偏；切文件回顶；rAF 节流 + ResizeObserver；
- jsdom 无 ResizeObserver → 新增 `tests/setup.ts` 桩 + vitest `setupFiles`；
  CodeViewer 新增虚拟化用例（2000 行只渲染 <100 行 + 大 spacer）。

**验证**：typecheck + eslint 全绿；vitest 76 passed（仅 `TaskForm.test.tsx` 1 例为**既有无关失败**，
clean HEAD 同样失败：查询 `#code-mode-toggle` 在 mock source 前置下不渲染）。

**遗留**：① Phase2 视觉跟手/锚点对齐需用真实代码模式任务人工复核（无代码模式测试数据，jsdom 无布局）；
② 既有 `TaskForm` 用例 `#code-mode-toggle` 失败待单独排查（与本次无关）。

## 2026-06-05（续）- 修复文档/PPT 预览同步滚动水合后失效（commit 26ed7a3）

**现象**：文档/PPT 模式"原图↔markdown"左右同步滚动不跟随，代码模式正常。用户疑为前端虚拟化
回归——已排除（文档/PPT 不挂载 CodeViewer，CSS 仅作用 .code-*，同步链路文件零改动）。

**根因（后端持久化缺口）**：同步滚动靠 markdown 的 `<!-- page -->` 标记→右栏 `[data-page]` 锚点。
`document.md` 落盘按设计剥除 marker（下载版），带 marker 版只在内存。任务从 DB 水合（重启/历史）
时从磁盘剥除版重读 → 锚点全失。代码模式锚点来自 files-index.json（持久化）故不受影响。
证据链：merged_raw(10)→reassembled(9)→final_refined(9)→document.md(0)。

**修复**：`Renderer.render` 额外落带 marker sidecar `.document.anchored.md`（PPT 同路径覆盖）；
水合 `_read_hydration_markdown` 优先读 sidecar 回退 document.md；编辑保存同步刷新 sidecar；
下载/assets 不含 sidecar。详见 known-issues.md 同名条目。

**验证**：新增 6 例单测（renderer 落 sidecar/strip + 水合优先/回退/空）；tests/output+pipeline+api
全量 363 passed。用真实产物 backfill：3/6 文档子目录逐字符等价回填成功，3 个 mismatch 安全跳过、
PPT 无 final_refined 源——这几个需重跑。

**遗留**：① 老任务无 sidecar，需重跑或 backfill（PPT 无 backfill 源）；② 后端已水合在内存的任务需
重启才会经新 reader 读到 backfill 的 sidecar。

## 2026-06-06 - PII 脱敏链路审计 + 新增凭据/token 检测器

**审计**：逐链路确认"上云端 LLM 精修前脱了什么"。结构化（手机/邮箱/身份证/银行卡）与自定义词由
producer 逐页 `redact_regex_only` 入队前脱掉，✅ 不上云、连 debug/cache 都干净。人名/机构名靠 LLM
检测，⚠️ 检测调用本身 + 早窗口/短文档段精修会外发（"完全脱敏不现实"，用户认可）。详见
known-issues.md「PII 脱敏链路审计」。

**小修**：新建任务表单重排（commit f6ce85b）——模式选择置顶、LLM 精修开关与配置相邻；浏览器截图
验证布局正确。

**主修（commit 1e4a68a）**：补上密码/用户名/账号/token 的检测空缺——`redact_structured_pii` 新增
step-0 凭据检测器（label 锚定 KV + URL 内联 user:pass@ + sk-/ghp_/AKIA/JWT），走 producer 正则层
→ 上云前、落盘前即抹掉。偏向宁多勿漏、技术正文不误伤，`redact_credential` 默认开可关。
表驱动重构 `redact_structured_pii` 降圈复杂度。

**验证**：新增 9 例凭据单测（正例 + 误报守卫）；`redact_regex_only` 端到端验证（密码/URL 凭据/
sk-key/用户名/账号/自定义词全中，token bucket/CONFIG_X=Y 不误伤）；tests/privacy+pipeline+output+api
全量 406 passed。

**遗留**（known-issues 已记）：人名/机构名云端曝光（需本地 NER/先检测后精修）；代码模式正文未脱；
debug/cache 实体脱敏前留底（landmine B）。均另排期。

## 2026-06-06（续）- PII 早窗口防泄漏（commit 51f0b38）

**问题**：流式/PPT 在实体词表（人名/机构名）建好前就把早窗口分段/页送云端精修 → 前 ~5 页
（或 ≤5 页短文档全部）的人名/机构名在脱敏前外发。

**修复**：开 PII 且要求实体脱敏时（新增 `_entity_redaction_pending` 门控），词表就绪前只攒页不送
云端，就绪后一次性追平：
- 文档 `_stream_process`：循环内按门控跳过 `_try_extract_and_refine`，检测后（含短文档兜底）补一次
  `try_extract` 追平（drain 全部已切分段，幂等）。
- PPT `_ppt_pipeline`：抽 `_finish_page` 闭包，早窗口页入 `pending` 缓存，阈值/短文档检测后统一追平；
  fail-closed 检测失败则推迟页退原文。

**附带收益**：分段送云端前已脱敏 → `reassembled/final_refined` dump 与 `.llm_cache` 对早窗口段也不再
留人名明文。磁盘留底只剩 `merged_raw/cleaned`（检测输入，默认 debug 才落盘）。

**验证**：红绿——禁用门控时 doc+ppt 用例均失败（refine 收到原始人名），开启则过；新增 3 例
（门控单测 + 文档/PPT 早窗口集成）。tests/pipeline+privacy+output+api+llm 全量 555 passed。

**遗留**：人名/机构名检测调用本身仍上云一次（LLM 检测固有，需本地 NER 才能免）；代码模式正文未脱。

## 2026-06-06（续）- 代码模式正文 PII 脱敏（commit 21f72cc）

**问题**：代码正文（行级 bbox 组装，绕过 producer 的 cleaned_text 脱敏）里的敏感信息会随
code_refine/repair/audit 外发云端；此前只脱前导注释 header。

**修复**：`_redact_code_headers` 改名 `_redact_code_pii`，分 header/body 差异化处理——header 走
`redact_snippet`（regex + 实体 lexicon + 自定义词），正文走 `redact_regex_only`（结构化 PII +
凭据/token + 自定义词，**不做实体脱敏**以保 import 路径/namespace/标识符不被误伤，AGE-50）。
无 header 文件也照常脱正文；fail-closed 不变。

**验证**：端到端实测——硬编码 `password="..."` / `sk-token` / URL 内联凭据 / 正文邮箱 / 电话均脱掉，
`#include` 路径与 `Zhang_counter` 等 name-like 标识符不动。`test_code_pii_header.py` 翻转正文邮箱
断言 + 新增正文凭据/标识符用例；全量 835 passed（cv2 模块除外）。

**取舍/遗留**：凭据 KV 在正文里可能误伤 `password=<expr>` 右侧表达式（宁多勿漏，`redact_credential`
可关）；正文注释里的人名/机构名仍可能上云（正文不做实体脱敏）。

## 2026-06-06（续）- OCR 退化重复行致 token 爆炸 + 尾页消失（修复）

**现象**：处理约 150 帧拍摄的内部文档（含 GDB 内存 dump 截图）时精修烧掉 28M+ token、后端刷屏
`finish_reason=length` + `段 6 截断递归到达上限`，且 100+ 张插图最终只剩 6 页 12 张、尾页整段丢失。

**根因（单一）**：OCR 把内存 dump 字节串（`pui8Src=0x... "..."`）识别成 `wm`/`nt`/`mu` 等 1–4 字符
短单元重复成百上千次的退化行（清洗后单行 8093 字符）。这一行既让 LLM 陷入重复生成直到截断、触发
depth-3 二分递归重试（→ token 爆炸），又因截断吞掉段内后续 page marker 导致 `reassembled.md` 只剩
8 个 marker（07564–07570）、`merged_raw.md` 仍有全 152 个 → 尾页 07571–07715 连内容带图全丢。
旧清洗漏掉：`remove_repetitions` 只比段落级相似度、`remove_garbage` 只删非可读字符，`wm` 全字母放行。

**修复（root cause）**：`OCRCleaner` 新增 `collapse_degenerate_runs`，逐页清洗最前折叠短单元超长重复
（`(.{1,4}?)\1{8,}` + 60 字符阈值），giant line 进 merger/segmenter/refine 前消失。守卫：纯分隔符单元
（`-=*#_~|+.`）的 `====`/`####`/`----` 不折叠、短于 60 字符不动；线性匹配无灾难性回溯。

**验证**：真实垃圾页 18217→2225 字符（最长行 8093→227，dump 上下文保留）；7 例红绿单测；性能 128K
退化行→47 字符 5.6ms；processing+pipeline 511 passed、全量 1091 passed（3 个 DeepSeek 失败为本机
未配 python 路径的预存环境问题，与改动无关）。

**遗留（另排期）**：折叠只覆盖"短单元重复"；非重复超长单行（minified JS/base64）仍可能触发同款尾页
丢失，彻底兜底需 segment 层按字符硬切 + reassemble 做"页 marker 数 merged_raw vs reassembled"守卫。
本样本 giant line 唯一来源即退化重复（次长行仅 229 字符），故折叠已完全覆盖，硬切守卫不急做。

## 2026-06-06（续）- user@host + 内部 URL 纳入上云前脱敏

**背景**：排查上一条 OCR 退化 bug 时顺带发现样本含 `scp ... qiangming@30.21.162.200`（用户名@IP）
与内部平台 URL `aliyuque.antfin.com/theadiotsw/...`（作者 handle + 文档 ID），既有脱敏链路漏掉。
用户确认纳入；两处决策：内部 URL=私有 IP 自动脱 + 可配置域名后缀；user@host=IP 与主机名都脱。

**实现**（`privacy/patterns.py` 新增两个结构化检测器，接入 `redact_structured_pii` 表驱动 steps；
文档/代码模式上云前的 `redact_regex_only` 自动获得）：
- `_HOST_TARGET_RE`（`redact_host`，`[主机地址]`）：`user@IPv4` + `user@单 label 主机名`，user
  含人名一起脱。**只接 IP 与无点主机名**，带点 FQDN/邮箱域名交邮箱步骤（lookahead
  `(?![A-Za-z0-9.-])` 拦 FQDN 前缀），避免关 email 时把 `user@a.com` 误切成 `[主机地址].com`。
- `_URL_LIKE_RE`（`redact_internal_url`，`[内部链接]`）：私有/回环 IP 的 URL 零配置即脱；host 命中
  `sensitive_url_domains` 后缀（配 `antfin.com` 覆盖语雀）的整条 URL 脱；公网链接原样保留。
- 顺序：credential → id_card → email → **host** → phone → bank_card → **internal_url**（email 先于
  host 吃带 TLD 的 user@domain.tld；host 先于 url 把 user@IP 整体脱掉）。
- 新增 `PIIConfig`：`redact_host`/`redact_internal_url`（默认开）、`host_placeholder`/
  `internal_url_placeholder`、`sensitive_url_domains: list[str]`（默认空）。

**修了一处自引回归**：host 主机名分支初版收 FQDN，导致关 `redact_email` 时 `user@example.com` 被
切成 `[主机地址].com`，碰挂既有 `test_email_disabled`。改为只接单 label + lookahead 后修复。

**验证**：`TestHostTargetRedaction`（6 例）+ `TestInternalUrlRedaction`（8 例）红绿；误伤守卫
（`@staticmethod`/`@提及`/`@types/node`/`config.json`/`v1.2.3`/公网链接）全过；全量 1105 passed
（排除 cv2 + 预存环境失败的 DeepSeek）。

**遗留**：`user@FQDN` 在关 email 时既不被邮箱也不被 host 脱（边缘，关 email 即选择保留）；裸公网 IP
（非 user@、非 URL 形态）不脱；内部 URL 的公网平台域名需用户显式配 `sensitive_url_domains` 才生效。

## 2026-06-06（续）- 文档模式正文区自动裁剪（content_crop，S1–S3 后端 MVP）

**需求**：屏摄文档照片含左导航 / 右大纲 / 顶部 UI，文档模式无行号锚定时这些会污染正文 OCR，
用户此前靠人工裁剪规避。要自动化裁剪 + 兼容已裁剪图。设计文档 `docs/zh/doc-content-crop.md`。

**关键验证（先验证再实现）**：用真实样本证伪了"复用 PPT slide_rectify"——它找最大亮矩形，
对白底文档只会框整屏含左右栏；代码暗色 IDE 0 检出。真实需求是"正文区检测裁剪"（版面分析）。
`crop_compare` 金标准（模板匹配定位）显示人工裁剪正文列约 x∈[750,2500]。

**算法（S1，commit 84e090d）**：`detect_content_lr`——自适应二值化 + 横向膨胀连行 +
中带垂直投影 + **去边缘伪影** + 低阈值取文本列（正文连续、与侧栏间深沟壑~2-3% 被分隔）+
取**包含图像中心**的连续列段（正文居中先验，规避左导航密集导致峰值误锚）。实测各张稳定排除
左右栏（左界~930 右界~2400），偏保守（宁窄不切正文），剩余微差交前端微调。

**跳过判据（S2）**：`compute_crop_box`——框宽占比 >0.9（无侧栏/已裁剪）或 <0.2（误检）→ 跳过
恒等放行，兼容用户已人工裁剪的历史图。

**接入（S3，commit 53c23a8）**：`crop_page` 异步入口（仿 slide_rectify，任何失败回退原图）；
`ContentCropConfig`（enable 默认开）挂 PipelineConfig；`_ocr_producer` OCR 前 PPT 矫正 / 文档裁剪
二选一；`process_many` 仅文档模式（非 code 非 ppt）启用。

**验证**：合成三栏图排除侧栏 / 铺满图跳过 / crop_page 落盘回退，12 例；全量 1130 passed。

**待续**：S4 后端裁剪框检测 API + S5 前端裁剪预览 + 拖拽微调（用户已定要前端微调，半自动兜底）。
MVP 只做左右边界 + 纵向整高；上下边界精修 / 多平台 / 透视矫正叠加留阶段 2。

## 2026-06-07 - PPT 模式按需矫正：根治 VL 图表碎裂（commit 2f59787）

**现象**：用户实测 PPT 模式同一张幻灯片（509《REME案例》）图被裁得稀碎——一张化学反应图被按单个
分子拆成 3 片；文档模式反而 1 张完整。

**排查链**（真实样本 + 实跑验证）：① 两模式同用 PaddleOCR-VL（`force_pipeline` 只改 pipeline 名），
唯一差别是 PPT 跑**矫正图**、文档跑**原图**。② 实跑 PPT+rectify=OFF → 图恢复完整（509 切 6→4、引用
1 张完整）。③ 但加按需矫正后用 bbox 裁小仍碎 → 进一步实测发现**根因不是 warp，是"把幻灯片裁到占满
画面"**：幻灯片相对尺度被放大后 VL 按更高分辨率把图表/化学结构切碎；保持原图尺寸则完整。

**修复**（`slide_rectify` 按需）：`_quad_skew` 量四边形偏离正矩形程度；偏斜 ≤ `rectify_max_skew_deg`
（默认 8°）= 近正视 → `_mask_surroundings` 只**遮黑屏幕四边形外周边、保原图尺寸、不 warp/不缩放**
（去周边满足用户"保留裁剪"要求，又不改幻灯片相对尺度 → 图完整）；偏斜 > 阈值 = 强透视屏摄 → 仍完整
warp。`PowerPointRestoreConfig.rectify_max_skew_deg`；`_ocr_producer` 透传。

**实测**：这批 4–5° 偏斜走遮黑分支，509 从 3 碎片恢复 **1 张完整图**、503 切图 11→9，处理图保持
1706×1279。新增 5 例单测；全量 1135 passed。

**关键教训**：VL 切图粒度对"内容相对尺度"敏感——裁小/放大内容会触发更细的结构切分。改变 OCR 输入
图（矫正/裁剪/缩放）时要警惕这点。

## 2026-06-08~09 - content_crop S4 后端 API + S5 前端裁剪预览微调（半自动闭环）

**S4（commit ee22b1a）**：`POST /crop/detect`（detect_boxes_for_dir 复用 compute_crop_box 给每张图
建议框，纵向整高 (x0,0,x1,h)，box=null=无需裁剪）；`CreateTaskRequest.crop_boxes`（图名→框）建任务前
`_apply_requested_crop` 就地预裁剪（apply_crop_boxes 覆盖原图，路径穿越守卫）——跑裁剪后的图，
content_crop 已裁剪判据自动跳过不二次裁，**无需把框穿过 pipeline/DB**。`GET /crop/image` 按 image_dir+
名取图供前端预览（SourcePicker 只给路径不给上传会话，故需此端点）。

**S5（commit b0a6082 + e09186b）**：方案 A（OCR 前确认）。`CropEditor` 可拖拽(移动)/8 手柄缩放的框
（原图像素坐标 + 显示等比缩放 + 框外压暗 + setPointerCapture）；`CropPanel` 拉 detect→逐图微调→上报框；
`TaskForm` 文档模式 + 已选源时显示开关 + 面板，提交把框作为 crop_boxes 传后端；useTaskRunner/client
透传 + getCropImageUrl；i18n 三语 crop.*；CSS。修预存失败（模式选择器重排把 #code-mode-toggle 改
#mode-code 测试没同步）。

**验证**：CropEditor 渲染单测 2 例（框百分比定位 + 8 手柄）、detect/apply/路径穿越 3 例；前端 77 passed、
后端全量通过。**交互拖拽的视觉验证需完整栈（后端 + 上传），留待实测**。

**content_crop 全链路收尾**：S1 检测算法 → S2 已裁剪跳过 → S3 producer 自动裁剪（默认开）→ S4 检测
API + 预裁剪 → S5 前端预览微调。文档模式默认自动裁正文区去左右栏污染；用户可手动微调框。阶段 2
（上下边界精修 / 多平台 / 透视叠加）留后续。

## 2026-06-09 - 回退 PPT 按需矫正（2f59787）：mask 净中性，碎图是 VL 固有限制

**起因**：用户实测发现 PPT 按需矫正（近正视只遮黑周边不 warp）虽治好 509，但 503/508 碎得更多。

**逐张三方对比**（document.md 引用图片数，9 张幻灯片合计）：warp(原始)=14、mask(我改的)=14、no-rectify=12
但**漏图**（501 两张插图全丢，OCR 0 切图）。结论：**三种图像变换都治不了碎图，总碎图数恒定 ~14**——
碎图是 PaddleOCR-VL doc_parser 把一张图按单个结构拆成多个 layout 区域的固有行为，VL backend 配置
（`ppocr_vl_backend.yaml` 仅 vLLM 参数）无切图粒度旋钮可调。mask 改动只是把碎图从 509/501 挪到 503/508，
**净中性、非真改善**（我之前只盯 509 验证、没看全局，是疏忽）。

**决策（用户拍板）**：`git revert 2f59787` 回退 mask 改动，恢复原始 warp 行为，碎图当 VL 固有限制接受。
slide_rectify 回到 warp-only（13 passed）；processing+pipeline 539 passed。

**真正的解法（未做，备选）**：后处理**合并空间相邻的切图**（按 grounding 坐标找相邻块、合并成一张、改写
markdown 引用），不依赖图像变换、对所有幻灯片管用，但是独立的中等工程。需要时再起。


## 2026-06-09 - 编辑模式手动重截插图 + 编辑器图片渲染修复

**背景**：PPT/文档模式 OCR 偶有插图被切碎或漏图（VL doc_parser 固有限制，见上节）。给用户一个兜底：
在编辑器里看到坏图时，自己从源图重新框选完整插图、裁出插入。复用 content_crop S5 的 `CropEditor`
（可拖拽/8 手柄缩放框）。用户确认范围**含编辑器图片渲染修复**（之前 Tiptap 编辑视图里图片是 `images/`
相对路径，根本不渲染）。

**三部分**：
1. **后端** `POST /tasks/{id}/crop-figure` {source_filename, box, doc_dir?} → 从源图按框裁块存
   `output_dir/{doc_dir}/images/manual_N.jpg`（`crop_region_to_images`，序号取现存 manual_* 最大 +1 防覆盖），
   返回 markdown 相对引用 `images/manual_N.jpg`。源图解析沿用 get_source_image 的词法包含+跟随 symlink
   校验；doc_dir 走 `_validate_doc_dir` 防穿越；显式动作失败要暴露（读图失败 ValueError→404、写盘
   OSError→500），**不静默回退**（与逐页裁剪契约相反）。
2. **编辑器图片渲染修复**：`MarkdownWysiwygEditor` 加 `taskId`/`docDir` props。`markdown.ts` 新增
   `editorImagesToAssetUrls`（灌入前 `images/`→asset URL，编辑视图才能渲染图；**不**剥 `_OCR/images`、不注
   page-anchor，保证 round-trip 无损）与 `editorAssetUrlsToImages`（保存前逆变换回 `images/`，取 URL 最后一个
   `images/` 起片段、丢 token 与 docDir 前缀、只动本任务 asset）。`valueToHtml`/`htmlToValue` 单点拦截。
3. **前端** `FigureCropDialog`（列源图→下拉选→隐藏图测自然尺寸→CropEditor 框选→确认 cropFigure→回调
   asset_path），编辑器工具栏「🖼 插入截图」按钮 `setImage({src})` 插入（asset URL，保存时逆变换）；
   DocCodePreview 透传 taskId/docDir；client `cropFigure`；schema `CropFigureResponse`；i18n 三语 figureCrop.*；
   CSS 模态对话框。

**验证**：后端 crop_region 单测 5 例（裁块/序号递增/跳过已存在最大/越界夹取/缺图 raise）+ 路由 6 例
（裁剪落 images/、doc_dir 子目录、404 任务/源图、400 穿越源图名/非法 doc_dir）；前端 markdown 正逆变换
8 例（round-trip 无损 + 不剥 _OCR + 丢 docDir 前缀 + 不动外链）+ FigureCropDialog 渲染/确认 3 例。
全栈门禁绿：前端 typecheck/lint/90 测试、后端 mypy --strict + ruff + 相关测试通过。

**注**：交互拖拽/真实插图渲染的视觉验证需完整栈（后端+上传+OCR 产物），留待实测。


## 2026-06-09 - 重截插图自动锚定到光标所在页

**背景**：上节「插入截图」对话框默认选中源图列表**第一张**，用户编辑某页插图坏图时还要手动在下拉里
翻找当前页对应的源图。改为打开对话框时自动锚定到光标所在页的源图。

**实现**：
- `MarkdownWysiwygEditor` 新增 `pageAtCursor(editor)`：从文档头 `nodesBetween(0, selection.from)` 扫到光标，
  取途中最后一个 `pageAnchor` 节点的 `page` 属性（即 `<!-- page: 原图名 -->` 的原图基名）。点「🖼 插入截图」
  时（toolbar 按钮 `onMouseDown preventDefault` 保住选区）捕获该页名存入 `cursorPage` state，传给对话框。
- `FigureCropDialog` 新增 `cursorPage` prop + `matchSourceByPage(sources, cursorPage, docDir)`：源图列表是相对
  `image_dir` 路径（多文档带子目录前缀），页标记只含基名 → 按**基名**匹配；多张同名时优先取 `docDir` 前缀下
  那张；无匹配回退列表首张（即旧行为）。加载源图后据此 `setSelected`，匹配成功置 `autoMatched` 显示提示
  「已自动选中光标所在页的源图，可在上方下拉切换」（三语 `figureCrop.fromCursorPage`），用户手动改选即清除提示。
- 配合 `handleFigureConfirm` 仍在保住的光标处 `setImage` 插入 → 源图与插入位置都落在同一页，真正「锚定到光标所在页」。

**验证**：前端 typecheck/lint 绿；FigureCropDialog 测试 6 例（原 3 + 新增 3：基名匹配自动选中并提示 /
多文档按 docDir 前缀锚定 / 无匹配回退首张且不提示）。视觉/交互验证同上节，需完整栈实测。


## 2026-06-09 - 预览 ↔ 编辑器互切保位（双向）

**背景**：进入编辑模式时编辑器从头开始，丢失预览时所在位置。用户要求双向保位：进入编辑落到
预览所在位置，保存/退出编辑回预览也落到编辑所在位置。

**复用既有锚点机制**：预览（`.markdown-preview`，`injectPageAnchors` 注的 `[data-page]`）和编辑器
（Tiptap `PageAnchor` 节点渲染的 `[data-page]`）共用同一套页锚点。`useScrollSync` 里早有
`getCenterPagePosition`（取视口中心的 `{page key, 区间比例}`）和连续映射数学，只是私有。

**改动**：
- `useScrollSync.ts`：导出 `PagePosition` 类型 + `getCenterPagePosition`；新增 `scrollToPagePosition`
  （把 `{key, ratio}` 落到同 page 同比例居中处）。把原 `getContinuousTargetScrollTop` 的映射逻辑抽成
  共用 `pagePositionToScrollTop`（行为零变化，原 continuous 测试精确值全过）。
- `MarkdownWysiwygEditor`：改 `forwardRef` 暴露 `getPagePosition()` 句柄；加 `initialPagePosition` prop，
  编辑器就绪后双 rAF 等 ProseMirror 锚点布局完，滚到该位置（`editor.view.dom.closest(.wysiwyg-editor-content)`
  反查滚动容器）。无页标记则不动（留在顶部）。
- `DocCodePreview`：`enterEdit` 抓预览 `getCenterPagePosition` → 传 `initialPagePosition`；
  `leaveEdit(restore)`——预览/保存按钮 `restore=true`（抓编辑器位置，预览重挂后双 rAF 落位、用完即清），
  切文档/切代码视图 `restore=false`（语境已变不保位）。

**验证**：前端 typecheck/lint 绿，全量 99 测试通过；useScrollSync 测试 +6（取中心 page+比例 / 按位置落位 /
**跨不同布局容器 round-trip 对齐** / 找不到锚点不动 / 无页标记返回 undefined / 最后一页按剩余比例）。
真实浏览器滚动验证需完整栈 + 多页任务（当前前端 dev server 502、任务列表空），留待实测；纯函数几何
已由单测覆盖（逻辑风险点在此），残留风险仅为真实浏览器里的布局时序（已用双 rAF 兜底）。


## 2026-06-09 - 截图四角校正（旋转 + 透视矫正）

**背景**：人工框选的插图常带旋转 / 透视畸变（屏摄、斜拍）。给「重截插图」对话框加四角校正：
用户放 4 个角点定界，后端透视变换矫正为正视图。**复用 PPT 模式的 OpenCV warp**（slide_rectify）。

**模式（用户拍板）**：保留矩形裁剪（默认）+ 新增四角校正，对话框内开关切换；向后兼容。

**后端**：
- `slide_rectify.warp_quad(image, quad) -> ImageBGR | None`：从 `rectify` 抽出的纯透视变换（**不做
  PPT 专用的顶边上抬**），按给定角点顺序映射到正矩形，退化（任一边 < 16px）返回 None。不改 `rectify`
  避免动 PPT 路径（接受 ~6 行重复）。
- `content_crop.crop_quad_to_images(src, images_dir, quad)`：读图 → 角点夹取到图内 → 按**固定角色顺序
  信任、不几何重排**（否则旋转插图会被重标角点矫正成错向）建 Quad → warp_quad → 存 manual_N.jpg。
  显式动作失败要暴露：读图失败 ValueError→404、退化 `DegenerateQuadError`→400、写盘 OSError→500。
- schemas：`CropPoint{x,y}` + `CropQuad{tl,tr,br,bl}`；`CropFigureRequest.box` 改可选 + 加 `quad`。
- 路由：quad 优先 / box 次之 / 皆空 400；新错误码 `INVALID_CROP_REGION`。抽 `_crop_figure_sync`
  helper 降复杂度（C901）。

**前端**：
- 新 `QuadCropEditor`：图 + 4 个固定角色（tl/tr/br/bl）可拖角点 + SVG 叠加（evenodd 镂空压暗外区 +
  描边四边形），复用 CropEditor 的指针捕获模式。
- `FigureCropDialog`：模式开关；切到四角时用当前矩形框作初始四角（boxToQuad）；按模式发 box 或 quad。
- schemas.ts/client.ts：CropQuad 类型、cropFigure body box?/quad?；i18n 三语 figureCrop.modeRect/
  modeQuad/quadHint + errors.api.invalid_crop_region；App.css quad-editor 样式。

**验证**：mypy --strict（68 文件）/ ruff / 前端 typecheck+lint 全绿；后端 +6 测试（warp_quad 旋转矫正
确定性断言"旋转白方块→正视白方块白占比>0.9" + 退化→None；路由 quad 路径 / quad 优先于 box / 皆空 400 /
退化 quad 400），前端 +5（QuadCropEditor 角点定位 + polygon 点序；对话框切四角模式确认带 quad 非 box）。
透视变换正确性由 warp_quad 单测覆盖（核心逻辑风险）；交互拖拽 / 真实插图视觉验证需完整栈，留待实测。


## 2026-06-09 - 裁剪对话框铺满窗口 + 实时矫正预览

**背景**：裁剪编辑器原限 520px 偏小；矫正效果要确认后才看得到。要求两栏：编辑器铺满 +
右侧实时渲染矫正结果（随拖动更新）。纯前端。

**实时预览（核心）**：四角校正的预览需要真·透视变换，且要实时——用 CSS `matrix3d`（4 点单应性）
让 GPU 变换，免后端往返。
- 新 `features/task/perspective.ts`：经典 4 点投影标定（adjugate / basisToPoints / general2DProjection），
  `quadToRectProjection(tl,tr,br,bl,w,h)` 求把源图四边形映射到正矩形的 3×3 投影矩阵，`matrix3dFromQuad`
  转成 CSS matrix3d 列主序字符串。方向与后端 `warp_quad` 一致（源四边形 → 正矩形）。
- 新 `RectifiedPreview`：按四边形边长算输出宽高 → 等比缩放进预览框 → `<img>` 按源图自然尺寸渲染 +
  `transform-origin:0 0` + matrix3d，容器 overflow:hidden 裁出正视结果。矩形 / 四角共用（矩形传由框
  生成的轴对齐四边形，退化为纯裁剪）。

**铺满 + 两栏**：`FigureCropDialog` 编辑区改 `.figure-crop-stage` 两栏（编辑器 flex:1 铺满 + 右侧
预览），窄屏 flex-wrap 堆叠；对话框 `width: min(640→1080px, 96vw)`；`.figure-crop-canvas .crop-editor/
.quad-editor` 去 520px 上限（只在对话框内覆盖，不动 CropPanel 建任务页）。编辑器 scale 仍按
`naturalWidth/clientWidth`（图 width:100% 不 letterbox，列变宽则换算更细，math 仍成立）。

**验证**：前端 typecheck/lint 全绿，全量 108 测试通过；+6（perspective 4 角精确映射 round-trip /
轴对齐中点 / matrix3d 16 数有限；RectifiedPreview 套 matrix3d+自然尺寸 / 退化空框；对话框加载后出预览框）。
**matrix3d 的 CSS 列主序排布 + 套在自然尺寸 img 上**是最易错处、jsdom 测不了真实渲染，需完整栈眼检；
投影数学本身已由 round-trip 单测兜死。


## 2026-06-11 - 重做裁剪缩放联动：原图随框缩放铺开，移除独立预览窗

**背景**：上一笔"两栏实时矫正预览"被否决——用户要的是**原图随裁剪框缩放、在窗口内铺开**，
而非旁边再开一个窗口渲染结果。交互拍板：拖动中视图稳定，**松手后自动缩放**（平滑过渡）。

**方案**：单一画面 + 缩放视口。
- 新 `features/task/cropFit.ts`（纯几何）：`fitRegion(vw,vh,iw,ih,region)` 算内容层变换——
  整图适配视口的基准尺寸（baseWidth/Height）+ zoom（框铺满视口 78%，下限 1、上限 2 CSS px/源
  像素防模糊放大）+ 平移（框中心对准视口中心，夹取不露空）；`quadBBox` 取四点外接框。
- `FigureCropDialog`：编辑器包进 `.figure-crop-viewport`（固定高 min(58vh,680px)、overflow
  hidden）+ `.figure-crop-zoom` 内容层（宽高=基准尺寸，translate+scale 内联，transition
  0.25s 平滑落位）。图加载 / 切模式 / 拖拽松手 / 窗口 resize 时 refit。
- `CropEditor`/`QuadCropEditor`：加可选 `onDragEnd`（pointerUp 时回调）；指针换算
  `clientWidth` → `getBoundingClientRect().width`（计入外层 transform scale，CropPanel 不受影响）。
- 手柄/框线反缩放：内容层写 CSS 变量 `--crop-zoom`，手柄 `scale(1/z)`、box 边框
  `calc(2px/z)`、quad 描边 `stroke-width calc(2/z)`——放大后视觉尺寸恒定（新增
  `types/css-vars.d.ts` 声明合并允许 style 写 `--xxx`，免 as 断言）。
- **删除** `RectifiedPreview` / `perspective.ts` 及其测试、i18n previewLabel、两栏 CSS（保留
  价值被否决，按减熵原则连根清）。

**验证**：typecheck/lint 全绿，110 测试通过（-8 预览/perspective，+7 cropFit 纯函数：整图
zoom=1 居中 / 小框中心对准 / 封顶 2/s0 / 贴边夹取 / 短维度居中 / 非法尺寸 undefined；对话框用例改
断言视口内出编辑器且无 .figure-crop-preview）。**真实浏览器 Playwright 实测**（真任务 c5cee22a）：
初始 scale=1.300（=0.78/0.6 设计值）→ 收框松手 2.891，框占视口 78%、中心偏差 <0.1px，手柄恒
14/16px；四角模式切换保位、外扩角点松手回落 2.32，描边视觉 ≈2px（calc 与祖先 scale 相乘正确）。


## 2026-06-11 - 编辑模式源图随文本同步滚动

**背景**：预览模式左侧源图栏随 markdown 滚动连续同步（usePreviewScrollSync + data-page 锚点），
编辑模式此前被显式禁用（rightScrollEl 仅预览容器）。要求编辑模式也支持同款图随文滚。

**方案**（复用现有机制，纯接线）：
- `MarkdownWysiwygEditor` 新增可选 prop `onScrollContainerChange?: (el?) => void`：编辑器就绪后
  把滚动容器（`.wysiwyg-editor-content`，内含 PageAnchor 渲染的 `[data-page]` 锚点）回调给外层，
  卸载时无参调用清空解绑。
- `DocCodePreview`：新增 `editorScrollEl` state 接住容器，加第二路
  `usePreviewScrollSync(leftScrollEl, editorScrollEl, editMode && …)`——与预览那路
  （`!editMode && …`）互斥启用，同一锚点连续映射策略，手感一致。进入编辑时
  initialPagePosition 落位的程序化滚动会顺带把源图栏对齐到位。

**验证**：typecheck/lint 全绿，110 测试通过（同步引擎已有 useScrollSync.test.ts 覆盖，本次为
纯接线 + 真浏览器实测）。Playwright 实测（真任务 c5cee22a，两侧各 20 锚点）：编辑器滚 9000→
左栏跟到 3850、滚 20000→7513；反向左栏回 0→编辑器跟到 320；两侧视口中心 page key 一致
（DSC07965.JPG）；切回预览模式原同步回归正常。console 仅既有 files-index 404 探测与 tiptap
duplicate link 警告，与本次无关。


## 2026-06-11 - content_crop 鲁棒化：相对核 + 质量选段 + 歧义守卫

**背景**：用户问"侧栏裁剪对宽度不一/数目不一是否鲁棒，还是只能处理当前样本"。系统性合成
实验（侧栏数目/宽度/沟壑/分隔线/偏心/分辨率/深色）+ 36 张真实原图证实三个样本调校假设：
固定像素核分辨率耦合（低分辨率沟壑被填平，100px 沟壑桥接成 ratio 0.89 误当正文）、硬中心
先验偏拍时选错列可丢整列正文、沟壑分隔线桥接侧栏。用户拍板直接修。

**改造**（`processing/content_crop.py`，详见 doc-content-crop.md §12）：
- 膨胀/平滑核按图宽等比（0.0345w / 0.007w），校准样本 w=3488 上与旧版严格等效——等效单次核
  是 (121,**5**) 不是 (121,3)：旧版 (61,3)×2 迭代纵向也膨胀 2px，核高少 1px 列段碎裂、右边界
  收窄 76–216px（回归实测踩坑）。
- 选段 = 质量(投影积分) × 中心距离衰减，替代硬中心先验+最宽 fallback；修复偏拍选错列。
- 歧义守卫：选中段不含画面中心且有 ≥0.25× 质量更靠中心的竞争段 → None 放行（宁可不裁）。
- **分隔线"二值图减除细竖线"方案已证伪弃用**：真实正文内部靠表格边框维持投影连续，删线拆
  正文（36 张回归 7 张劣化）；已在设计文档记"勿再尝试"。

**验证**：真实 36 张回归 35 张与旧版逐像素一致（±2%w），1 张（DSC07964 稀疏术语页）发现**旧版
真实误裁**——裁到左导航 [70,819] 丢全部正文，新守卫修复为放行（红框可视化实锤）。合成矩阵：
单/双/宽/窄侧栏、0.5×/1×/3× 分辨率、深色、偏拍+稀疏宽栏全 OK；窄沟壑 <5%w 从误裁变安全放行。
新增 TestRobustness 12 例参数化测试；mypy --strict 66 文件 / ruff / typos / 全量 1149 passed。


## 2026-06-11 - 裁剪面板 UI 四连修：入口 / 全黑 / 缩略图选图 / 源图预览

**背景**：用户实测反馈建任务"手动调整正文裁剪框"四个问题：入口不显眼（裸 checkbox 断行）、
打开后整页全黑、所有图全量列出（面板撑到 1.4 万 px）、选服务器图片时不知道选的是哪张。

**根因（全黑）**：`.crop-editor-box` 用 `box-shadow: 0 0 0 9999px rgba(0,0,0,.35)` 做框外
压暗且 `.crop-editor` 无裁剪——34 个编辑器同屏各叠一层全屏 35% 黑罩，(0.65)^34≈0 整页全黑。
**修复演进**：先加 `overflow:hidden` 裁 shadow → 实测裁掉贴边手柄外半（本面板框纵向整高，
上下手柄永远贴边，Playwright 角点命中测试抓到拖拽失效）→ 终版**弃 box-shadow，改框四周
4 块精确遮罩 div**（pointer-events:none），不叠加、不裁手柄。

**其余三项**：
- 入口：改与 LLM 精修/脱敏同款 `pii-section + toggle-switch` 区块（标题"正文裁剪"+开关+说明）；
  i18n `crop.toggle` → `crop.title`/`crop.desc` 三语。
- 面板：CropPanel 重构为"横向滚动缩略图条（lazy 加载）+ 单图编辑器（同屏只挂一个）"，
  面板高度 14517px → 819px；编辑器放宽 min(860px,100%)；显示 `i / N · 文件名`。
- 源图预览：SourcePicker 服务器浏览加右侧预览栏（复用 GET /crop/image），点选文件即渲染
  该图 + 文件名，navigate 时清空；列表+预览两栏 flex，窄屏堆叠。

**验证**（Playwright 全流程实测）：文件点选预览实图加载 ✓；开关区块与精修一致 ✓；面板 34
缩略图 + 单编辑器、图不黑 ✓；缩略图切换 1/34→5/34 换图保框 ✓；拖 e 手柄 363→263 生效、
se 角点 elementFromPoint 命中 handle ✓；重截插图对话框回归（4 遮罩+8 手柄、压暗一致）✓。
typecheck/lint 全绿，110 测试通过。


## 2026-06-11 - 裁剪面板补充：上一张 / 下一张切换键

CropPanel 编辑器上方加导航条：`‹ 上一张`｜`i / N · 文件名`（居中）｜`下一张 ›`，到边界禁用
不回绕；切图（按钮或缩略图）后激活缩略图自动 `scrollIntoView` 滚进可视区。i18n 三语
crop.prev/next。Playwright 实测：首张 prev 禁用、连点 next 至 6/34、回退 5/34、激活缩略图
始终可见；typecheck/lint 0 错，110 测试通过。


## 2026-06-11 - 裁剪面板：侧边切换键 + 任务级删除图片（exclude_images）

**侧边切换键**：上一张/下一张从标题行移到图框左右两侧纵向居中（.crop-stage flex 三列：
‹ 键 | 编辑器 | › 键），到边界禁用；标题行改为"i / N · 文件名 + ✕ 删除此图"。

**删除图片（核心：任务级排除，绝不动磁盘源文件）**：
- 后端：`OCRConfig.exclude_images: list[str]`（相对根 image_dir，与 crop_boxes 同 key 空间）——
  挂 OCR 配置使请求合成（model_copy）/DB 持久化/resume 全部复用现有机制零迁移。
  `resolve_excluded_paths`（拒绝 `..`/绝对路径越界 key；**不 resolve 软链**——stage 目录源图
  是外部软链，resolve 会越界判废，与 scan_images 未 resolve 路径同构比对）；process_tree 根目录
  解析后经 _process_leaf/process_many 下传 `exclude_abs`，`_scan_task_images` 统一扫描+过滤
  （直调场景按本目录自行解析）；剩余为空的叶子目录整个跳过，全空报"图片已全部被排除"。
  `_apply_requested_crop` 跳过被排除图（排除是任务级的，不该就地预裁剪其磁盘文件）。
- 前端：CropPanel 改列出**全部**图（无框图纯预览+提示"不裁剪仍可删除"）；✕ 删除当前图→
  选中位移到下一张（末张前移）；缩略图条下"已排除 N 张（点击文件名恢复）"清单；
  框上报自动剔除被删图。TaskForm `cropExcluded` state → 提交并入 `ocr.exclude_images`
  （exclude 单独存在也会触发 ocr 覆盖块）；关开关清空。client/useTaskRunner 类型透传。

**验证**：后端 mypy --strict/ruff 全绿，新增 tests/pipeline/test_exclude_images.py 5 例（解析
正常/越界拒绝/空、软链身份保持、子目录 key 叶子比对），全量 1154 passed；前端 typecheck/lint
0 错、110 测试。Playwright E2E：侧键与图框纵向居中偏差 0px 且分居两侧；删 2 张→排除清单 2 项、
计数 36→34；恢复 1 张精确回滚选中；拦截建任务 POST 实测 payload `ocr.exclude_images` 只含未恢复
的 1 张、其框已从 crop_boxes 剔除（33=34-1）。


## 2026-06-12 - 缩放视口复用：输入图裁剪面板获得插图重截同款联动

**需求**：把重截插图对话框的"拖框松手 → 原图自动缩放铺开"效果复用到建任务的输入图裁剪面板。

**抽取共用组件 CropZoomViewport**（FigureCropDialog 内嵌视口逻辑外提）：固定尺寸视口 +
内容层 translate+scale（cropFit 纯几何）+ --crop-zoom 手柄反缩放 + resize 自动重算；
命令式 `refit(region)` 供外层拖拽松手/切模式触发；切图由外层换 key 重挂、initialRegion
挂载落位一次。FigureCropDialog 重构为调用方（删内部 view/refit/resize 逻辑，行为零变化）。

**关键几何坑——纵向整高框在 both 模式下 zoom 恒为 1**：正文框纵向取整高（content_crop
MVP），fitRegion 取两方向最小 → 高度项 0.78×vh/(图高×s0) ≡ 0.78 < 1 被 clamp 钉死，复用
后看不到任何放大。解法：fitRegion 加 `axis: "both"|"width"`，面板用**宽度主导**（框宽铺满
78%，纵向溢出由视口裁剪居中）——整高框上下边贴图边本无调整意义，核心操作的 e/w 手柄随
中心对齐始终可见；对话框保持 both 不变。

**验证**：typecheck/lint 0 错，111 测试（+1 width 模式：both 钉 1 vs width 1.95 + 水平中心
对齐 + 纵向夹取）。Playwright 实测：面板初始 scale 2.989（此前恒 1）→ 收框松手 3.456 →
切下一张重挂回 3.01，e/w 手柄视口内可见；对话框回归初始 1.300（0.78/0.6 设计值）→ 收框
1.562 → 切四角校正保位，与重构前行为一致。


## 2026-06-12 - 裁剪面板缩放回调：框完整可见填满窗口（width 模式否决回滚）

**用户反馈**：宽度主导铺开太大，纵向整高框的上下边整个溢出视口无法选中，过犹不及。

**回滚**：fitRegion 删 axis 参数恢复"两方向取最小、框始终完整可见"；CropZoomViewport 删
fitAxis prop；CropPanel 不再传 width。整高框 zoom 钳在 1 = 框纵向 100% 填满视口、水平居中
压暗——**这就是预期行为**，已在 fitRegion 注释 + 单测注释双处记"width 模式已被用户否决勿
重试"（替换原 width 用例为"钳 1 即满窗"断言）。

**验证**：typecheck/lint 0 错、111 测试；Playwright 实测 scale=1、框纵向填满 100% 且完整
可见（boxInVp）、上边手柄可选中向下拖 60px（裁页眉场景）生效。


## 2026-06-12 - 裁剪面板：无框图补人工裁剪选项

**用户反馈**："未检测到侧栏不裁剪"的图也需要人工裁剪入口（检测漏判 / 想顺手去页眉）。

- 无框图预览下方加「手动框选裁剪」：初始化居中 80% 宽 × 整高的框（与检测框同形态）进入
  编辑器+缩放视口，之后与有框图完全同链路（boxes 上报 → crop_boxes）。
- 对称加「不裁剪此图」（标题行，有框时显示）：移除该图框回到不裁剪状态——检测误检或手动
  框后悔都有退路，避免单向门。后端零改动（boxes 状态机天然支持增删）。
- i18n 三语 crop.addBox/removeBox，noBoxHint 改短句。

**验证**：typecheck/lint 0 错、111 测试。Playwright 实测（DSC07963——恰是歧义守卫放行的
稀疏术语页）：点手动框选→编辑器+视口出现、拖 e 手柄 -60px 生效；点不裁剪→回到纯预览+
入口按钮，闭环成立。


## 2026-06-12 - 修复手动裁剪框静默失效：废弃就地预裁剪，改任务级 OCRConfig.crop_boxes

**用户报障**：任务 8caabe3f 手动微调正文裁剪"没生效一样"，笔记本屏幕被当插图截进正文。
取证：14 张 NAS 原图全部仍全尺寸 → 手动框确实没应用。根因：NAS 是 CIFS 只读挂载，旧版
`apply_crop_boxes` 就地覆盖原图时 `cv2.imwrite` 失败返回 False 无检查 → **静默丢失**。

**修复**（详见 doc-content-crop.md §13）：手动框改任务级 `OCRConfig.crop_boxes`，OCR 前由
`crop_page_manual` 裁到任务输出目录，绝不写用户目录；`ImageOverrides`（排除+用户框）根入口
解析整体下传。一并根治：可写目录原图被覆盖毁坏（S4 遗留风险）、stage 软链源框静默跳过、
多目录任务 `_process_leaf` 漏传排除清单的隐藏 bug；手动框上下边（y0/y1）真正生效。
`apply_crop_boxes` 删除。前端零改动。

**验证**：mypy --strict 66 文件 / ruff 全绿，1159 passed（+crop_page_manual 3 例 +
resolve_crop_boxes/ImageOverrides 4 例，-apply_crop_boxes 2 例）。真实 E2E：软链 stage 2 张 +
手动框建任务（关精修）→ `.content_crop/DSC07963_crop.JPG` 精确 1500×1700（上下边生效）、
OCR 目录 `_crop_OCR`、NAS 原图完好 3488×2624。

**遗留说明**：用户报障里"笔记本屏幕被当插图"另一半原因是前景笔记本叠在正文列内、OCR 版面
模型把它判为 image 块——手动框生效后用户可收 y1 裁掉下方笔记本；纯靠算法的"插图块后过滤"
另行排期。

## 2026-06-13 - 堵 g++/gcc 诊断 LFI 残留面：`#import` / `#include_next`（#1d）

**背景**：#1/#1b/#1c 已堵住直接 #include LFI、传递性兄弟头、宏 include、非 UTF-8 头旁路。
ultrareview 复审指出仍剩一个同类高危：C/C++ 中和逻辑只认 `#include`，真实 gcc 同样处理
`#import "/x"` 与 `#include_next "/x"`（按路径读文件并回显内容），sentinel 复现 LFI 仍成立。

**修复**（`processing/code_diagnostics.py`，详见 known-issues.md「#1d」）：把读文件预处理指令集合
从 `include` 扩到 `include | include_next | import`。`_C_INCLUDE_DIRECTIVE_RE` 与 `_C_INCLUDE_RE`
两条正则同步扩展，`\b` 词边界确保 `#define IMPORT` / `#includex` 不误命中。中和策略不变：
绝对/越级字面量中和、非字面量目标一律中和。相对名 `#include_next` 只命中受控影子树 `-I`，非 LFI 升级。

**验证**：mypy --strict / ruff / typos 全绿；`TestUnsafeIncludeNeutralization` 40 例过（含真实 gcc
端到端 import/include_next × c/cpp 无泄漏 e2e），`test_code_diagnostics.py` 60 例全过。临时还原旧正则
复现 4 例失败，证明回归测试有效拦截。

**遗留**：security_audit_2026_06_13 清单里的 High（paddle_python exec / SSRF / output_dir rmtree /
默认放行鉴权 / PII 上云脱敏绕过 / api_key 明文入库）尚未处理，另行排期。

## 2026-06-13 - 安全审查 #35：默认放行鉴权 → fail-closed 自动生成 token + bind 守卫

**背景**：security_audit High #35——未配 `DOCRESTORE_API_TOKEN` 即完全放行，叠加默认
`BACKEND_HOST=0.0.0.0` → 开箱全网未授权可达，放大同批 RCE/SSRF/rmtree/PII 面。这是安全批次
（LFI 已 PR #51 进 dev）的第一项 High 修复，从干净 dev 开 `bugfix/35-fail-closed-auth`。

**设计偏离（用户确认）**：issue 原方案「仅绑 loopback 或拒启」会挡掉「桌面服务 + 手机配对」方向的
手机端（手机需 LAN/远程够到桌面）。改为等价或更强的 fail-closed：默认自动生成持久 device token、
始终强制校验、永不裸奔；loopback-only 仅保留为 insecure 逃生口的约束。详见 deployment.md §3.5。

**改动**：
- `auth.py`：`configure_auth_from_env()` 三选一（显式 token / insecure 逃生口 / 默认自动生成持久
  token，`secrets.token_urlsafe(32)`，跨平台配置目录 + POSIX 0600）；`enforce_bind_safety()`
  insecure+非环回拒启；保留底层 `configure_auth(token)` 不破坏现有测试。
- `app.py::create_app`：接入解析层 + bind 守卫；可选 `DOCRESTORE_CORS_ORIGINS` allowlist（默认空）。
- `start.sh`：默认 host 0.0.0.0 → 127.0.0.1；export `DOCRESTORE_BIND_HOST`。
- 设计文档 `deployment.md` §3.5「鉴权与网络暴露」（三种 token 模式 + bind 守卫 + CORS + 偏离说明）。

**验证**：`tests/api/test_auth.py` 19 passed（+11 新例：token 三来源 + bind 守卫）；`create_app()`
三路集成冒烟过（显式起 / insecure+0.0.0.0 拒启 / 默认自动生成落地）；mypy --strict + ruff + typos
全绿，tests/api 132 passed 9 skipped。详见 known-issues.md「#35」。

**遗留**：High 余项 #32/#33（paddle_python RCE + api_base SSRF，同根因，建议同一 PR）、#34
（output_dir rmtree）、#36（PII 绕过）、#37（api_key 明文）；手机配对完整传输层（二维码 + mesh/
中继）待客户端形态敲定后单独设计。

## 2026-06-13 - 安全审查 #32+#33：请求级覆盖基础设施字段 → RCE + SSRF 根治

**主题**：堵住「创建任务请求体覆盖基础设施字段」这条攻击链——`paddle_python` 任意二进制执行（RCE，
#32）、`paddle_server_url` / `llm.api_base` 指向攻击者/内网（SSRF + 数据外泄，#33）。同根因（请求级
`model_copy` 不区分业务/基础设施字段），一个 PR 修。分支 `bugfix/32-33-request-override-rce-ssrf` off dev。

**改动**：
- `api/schemas.py`：`OCRConfigRequest` 删 `paddle_python` / `paddle_server_url` /
  `paddle_server_model_name`（基础设施字段不再暴露给请求；pydantic 默认丢弃；前端零引用）。
- `api/routes.py`：新增 `_OCR_INFRA_OVERRIDE_DENY` denylist，`_resolve_ocr_config` 合成生效配置时
  二次剔除解释器/worker 脚本/服务地址（防 schema 回归）；`create_task` 对请求级 `llm.api_base` 过
  SSRF 守卫（`to_thread` 包 DNS），失败 `400 LLM_API_BASE_REJECTED` 不建任务。
- `api/url_guard.py`（新）：`validate_outbound_api_base`——仅 http/https；解析 host 全部 IP，私网/
  链路本地(含元数据 169.254)/保留/多播/未指定拒；**环回放行**（本地 LLM）；IPv4-mapped 旁路按内嵌
  IPv4 判定；可选 `DOCRESTORE_LLM_API_BASE_ALLOWLIST` host 白名单逃生口（命中即放行，含内网中转站）。
- `api/errors.py`：新增 `LLM_API_BASE_REJECTED` 错误码；前端 i18n 三 locale 同步。
- 设计文档 `deployment.md` §3.6「请求级配置覆盖安全」+ `DOCRESTORE_LLM_API_BASE_ALLOWLIST` env 表。

**验证**：新增 `tests/api/test_url_guard.py`（21 例）+ `tests/api/test_override_security.py`（12 例）；
tests/api + tests/llm 共 **312 passed 10 skipped**；mypy --strict + ruff + typos 全绿；前端本地
`tsc -b` + eslint 通过（hook 的 npx 工具链误报已用项目本地链复核）。详见 known-issues.md「#32 / #33」。

**偏离说明**：issue #33 写「连环回一起拦」，但本地 LLM（provider=local）合法 api_base 即
`http://localhost:11434/v1`，照搬误杀该功能 → 环回放行（高价值目标元数据/内网横向仍拦），LAN
本地 LLM 走白名单。**残留**：DNS rebinding 未防（需 connect 级 IP pin，过度工程暂不做）。

**遗留**：High 余项 #34（output_dir rmtree）、#37（api_key 明文）、#36（PII 绕过，工作量最大）按序推进，
各开 `bugfix/{N}-...` off 最新 dev；整批安全 issue 进 dev 后用一个 release PR 收口 dev→main。

## 2026-06-13 - #32/#33 自查跟进：覆盖 denylist→allowlist 硬化（PR #54）

**背景**：#32/#33 落地后自查 sink 兜底，发现 denylist `_OCR_INFRA_OVERRIDE_DENY` 不完整——漏列
`paddle_server_host` / `paddle_server_port`（与已覆盖的 `paddle_server_url` 同为 SSRF 出站目标，
`build_default_paddle_server_url()` 用 host:port 拼地址）、`model_path`（vLLM 任意路径加载权重 →
pickle 潜在 RCE）、`paddle_server_api_version`。**当前不可利用**（这些字段不在 `OCRConfigRequest`
schema），但 denylist 的职责正是兜未来 schema 误改，漏列给了「已完整」的假象。

**修复**（`bugfix/ocr-override-allowlist` → PR #54 rebase 合 dev `b37f135`）：
- `routes.py`：`_OCR_INFRA_OVERRIDE_DENY` 翻成 allowlist `_OCR_SAFE_OVERRIDE_ALLOW`，
  `_resolve_ocr_config` 过滤条件反向（`key in allowlist`）。默认拒绝、只放行 5 个业务字段
  （`model`/`gpu_id`/`exclude_images`/`paddle_pipeline`/`paddle_ocr_timeout`），对 schema 漂移免疫。
- `schemas.py`：`OCRConfigRequest` docstring 同步（新增字段须登记 allowlist，否则请求级覆盖静默丢弃）。
- 测试：危险字段清单补全 host/port/model_path 等同类项并断言**均不在 allowlist**；新增 **allowlist 与
  `OCRConfigRequest` 字段集恒等**断言（schema 新增字段忘登记即失败）；`test_override_security` 扩到 18 例。
- 文档：`deployment.md` §3.6 措辞同步 + 补「**设了白名单后环回不再自动放行**」UX 警告；
  `known-issues.md`「#32/#33」记录硬化与教训。

**验证**：`test_url_guard`(21) + `test_override_security`(18) = 40 passed；tests/api + tests/llm
**318 passed 10 skipped**；mypy --strict + ruff + typos 全绿（pre-commit Passed）。

**教训**：allowlist（默认拒绝、只放行已知安全字段）优于 denylist（逐一枚举危险字段）——后者总会漏同类
字段。dev 现领先 main **7 个 commit**（4 LFI + #35 + #32/#33 + 本次硬化）。


## 2026-06-13 - 安全审查 #34：output_dir 无边界 → DELETE 任意目录删除根治

**背景**：六分区安全审查 High 项 #34。`output_dir` 请求级原样带入，删除任务时
`shutil.rmtree(output_dir, ignore_errors=True)`。构造非法 `image_dir`（任务快速进 FAILED 终态）+
`output_dir=/home/user/work`，`DELETE /tasks/{id}` 即递归删掉整棵工作区、静默不可逆；叠加 #35（默认放行
鉴权）= 未授权可达。与 #32/#33 同类：基础设施级请求可控量无边界。

**修复**（`bugfix/34-output-dir-boundary`，新增 `pipeline/path_guard.py`，两道防线）：
- **受信工作根** `resolve_work_root()`：默认系统临时目录（默认输出 `{tempdir}/docrestore_{id}` 的父），
  env `DOCRESTORE_WORK_ROOT` 拓宽（持久化产物逃生口，镜像 #33 白名单 env）。
- **建任务准入** `routes._resolve_output_dir`：空串归一 None 走安全默认；显式值过 `validate_output_dir`——
  `resolve()` 折叠 `..` / 符号链接后须严格落工作根下（≠ 根本身），越界 `400 OUTPUT_DIR_REJECTED` 不建任务。
  抽成 helper 是因内联校验把 `create_task` 圈复杂度顶到 12 > 10（ruff C901）。
- **删除 sink 二次校验** `delete_task`：rmtree 前过 `output_dir_within_root`（TOCTOU 防御：历史越界任务 /
  DB 篡改 / 漏接路径）——越界拒删、绝不触碰目录、任务留列表让用户察觉。
- `errors.py` 新增 `OUTPUT_DIR_REJECTED` + 三语 i18n。

**明确判定**：`image_dir` 不约束（只读输入、全链路从不被删，合法指向 NAS 外部只读路径，加边界反而误杀）；
唯一危险 rmtree sink 就 `output_dir`（`upload_dir` / `stage_dir` 均服务端 `mkdtemp` 天然受信）。

**验证**：新增 `tests/api/test_output_dir_boundary.py`（17 例）+ `test_task_manager.py::TestDeleteTaskBoundary`
（2 例）。tests/api + tests/pipeline 全量 **425 passed 17 skipped** + mypy --strict + ruff + typos 全绿
（pre-commit Passed）。`deployment.md` §3.7 新增 + `known-issues.md` #34 条目。

**教训**：请求级可控量落到 rmtree / 写 / exec 等 sink，先锚定受信根再做 resolve 后严格子路径校验，sink 处
二次校验防 TOCTOU。dev 领先 main **9 个 commit**（前 8 + 本次 #34）；High 余 **#36 / #37**，整批进 dev 后
一个 release PR 收口 dev→main。

## 2026-06-14 - 安全审查 #37：api_key 明文入库 → 落库排除 + 水合回填 + 存量清洗

**问题**：`database.py::insert_task` 把含 `api_key` 的 `LLMConfig` 整体 `model_dump_json()` 落 `tasks.llm`
列，明文凭据随 DB 文件 / 备份 / 快照长期留存（#37，High）。

**修复**（`bugfix/37-api-key-plaintext`，三处）：
- **落库排除**：`insert_task` → `model_dump_json(exclude={"api_key"})`。审计确认 `LLMConfig.api_key` 是
  `config.py` 唯一凭据字段（OCR / PII / code / ppt 无），只锁它。
- **水合回填**：新增 `llm/credentials.py::refill_api_key_from_env`（仅当 key 空才从 `DOCRESTORE_LLM_API_KEY`
  回填，`model_copy` 不覆盖显式 key / 不原地改）；两个水合点 `load_persisted_tasks` / `get_task_async` 统一
  过它 → resume 走的 `task.llm` 运行期可用。环境变量名常量与 `app.py` 启动回填共用。
- **存量清洗**：`initialize` 阶段 `_scrub_persisted_api_keys` 抹历史行明文 key（幂等、容错损坏 JSON）。

**resume 契约**：key 不再持久化 → resume / 重启依赖环境变量回填，运维须把云端 key 配在环境。

**验证**：`test_database.py`（落库 raw 无 key / 清洗存量 / 幂等容错）+ `test_credentials.py`（4 例）+
`test_task_manager.py`（端到端水合回填）。受影响模块全绿（persistence + llm + pipeline 69 passed、
api 189 passed 9 skipped）+ mypy --strict + ruff + typos。`deployment.md` §3.4 补凭据持久化小节 +
`known-issues.md` #37 条目。

**教训**：持久化配置快照必须剔除凭据字段（`exclude`），凭据走环境运行期回填；存量数据补一次性清洗，否则
只修新写入、老泄漏仍在。dev 领先 main **10 个 commit**（前 9 + 本次 #37）；**High 余 #36**（PII 上云前脱敏
被绕过，工作量最大），整批进 dev 后一个 release PR 收口 dev→main。

## 2026-06-14 - 安全审查 #36：PII 上云前脱敏被多路绕过（文档/代码/PPT 全审）

**问题**（#36，High，最后一个 High）：「上云前脱敏」承诺被三条路径绕过，标准部署（启动级 `pii.enable=False`、
前端单次任务开 PII 走**请求级** `pii_cfg`）下用户以为开了 PII 实则多路失效：
- **①** 代码模式 `_redact_code_pii` 把**原始** header 拼接后 `detect_pii_entities(combined)` 裸送云端，结构化
  PII 的 regex 脱敏在该云端调用之后才执行。
- **②** `_finalize_single_doc`(:2177) / `_fill_one_gap`(:3047) 读 `self._config.pii`（默认 False）而非请求级
  `pii_cfg` → gap-fill re-OCR 文本（绕过 producer 逐页 regex 的全新文本）+ 最终输出实体兜底恒不脱敏。
- **③** 代码 prompt 的 `file_path` / `related_snippets`（含外部 `context_root` 片段）/ `path_candidates` /
  `diagnostics`（g++ `summary` 回显源码行）未脱敏随 prompt 外发。

**修复**（`bugfix/36-pii-cloud-redaction-bypass`）：
- **①**：拼 `combined` 前先对每个 header `redact_regex_only`，再送检；人名仍保留供实体检测。
- **②**：`pii_cfg` 一路透传 `_stream_process → _finalize_single_doc → _maybe_fill_gaps → _fill_gaps →
  _fill_one_gap`，:2177 / :3047 改用请求级配置，禁止回落 `self._config.pii`。
- **③**：`_make_regex_redactor(pii_cfg)` 建 `redact_regex_only` 函数下传 `CodeLLMRefiner` /
  `DiagnosticCodeRepairer` / `CodeConsistencyAuditor`；在 `json.dumps` **之前**对四类字段按字段脱敏（先脱后
  序列化 → 占位符引号被 json 转义，绝不破坏 JSON）；snippets / path / 诊断只 regex 不做实体替换（防误伤标识符）。

**PPT 模式经全审为干净**：`_ppt_pipeline` 自 §9 起即正确透传 `pii_cfg` + producer 逐页 regex + 每页精修前
`redact_snippet` + 组装兜底 + fail-closed，无误读，本次不改（已核验，不遗漏）。

**验证**：5 个新增/强化回归测试覆盖 ①②③（含 ② 最终输出「回退 bug 必失败」反验证：临时还原 :2177 为
`self._config.pii` 后该测试确失败）+ 对照「未开 PII 不脱」；PII + 代码模式相关 **140 passed**，全量
**1296 passed 41 skipped**（3 个 DeepSeek 失败为本机未配 OCR python 路径的既有环境问题，stash 后净树同样失败，
与本次无关）。mypy --strict + ruff + typos 全绿（pre-commit Passed）。`privacy.md` §10 新增设计 +
`known-issues.md` #36 条目。

**教训**：「上云前脱敏」须把请求级配置贯穿每个云端 sink（深层 helper 不回落启动默认）、脱敏必须在拼 prompt /
送检之前（顺序写反等于没脱）、覆盖所有进 prompt 的字段（路径 / 外部片段 / 诊断同样外发）；结构化字段脱敏放在
`json.dumps` 之前，序列化层兜住占位符转义。dev 领先 main **11 个 commit**（前 10 + 本次 #36）；**6 个 High
安全 issue（#32–#37）全部修复进 dev**，下一步整批一个 release PR 收口 dev→main（届时 `Fixes #N` 自动关闭）。

## 2026-06-14 — PII 脱敏统一重构 S1（PIIGuard 抽取 + 收口，行为不变）

**背景**：#36 修复后用户提「文档/代码/PPT 三模式统一 PII 路径，不要分模式管理」→ 设计文档
`docs/zh/backend/pii-unification.md`（已确认）：统一到一个 `PIIGuard`；结构化 PII 文档/PPT 前置、代码
header `full` + body `tokens_only`；人名/机构名改本地 NER。分步 S1→S2→S3→S4，分支 `feature/pii-unify`。

**S1a**（commit `5efa655`）：新建 `backend/docrestore/privacy/guard.py::PIIGuard`——`redact_structured`
（= 现 `redact_regex_only`）+ `redact_for_cloud`（= 现 `redact_snippet`），`profile="full"` 逐字节包住现
行为，`profile="tokens_only"` 留 S2（显式 NotImplementedError）。`tests/privacy/test_guard.py` 6 个等价性
单测证明 guard == `PIIRedactor`（逐字节）。

**S1b**（commit `62ea9d5`）：`pipeline.py` 所有脱敏调用点收口到 guard——producer 逐页 / 段精修 / gap-fill /
finalize 输出兜底 / `_redact_code_pii`（header+body）/ PPT 每页；线程态形参 `redactor: PIIRedactor|None`
改名 `guard: PIIGuard|None`（`_try_extract_and_refine` / `_refine_segment_with_cache`）。改完 `pipeline.py`
**零**直接构造 `PIIRedactor` / 直调 `redact_regex_only` / `redact_snippet`。`_make_regex_redactor` 暂留
（S2 删）内部改走 guard；`test_entity_redaction.py` 2 处直调 helper 随形参改名同步更新。

**验证**：脱敏链路 157 + `pipeline`/`llm`/`api` 582 测试全绿；mypy/ruff/typos 干净（pre-commit Passed；
`ocr/ngram_filter.py`+`ocr/preprocessor.py` 3 处 torch Tensor 子类型告警为既有，另排）。**行为零变化**。

**下一步**：S1（收口）完成 → **S2**（代码 header `full` / body `tokens_only` 分档 + 新增 `tokens_only`
正则原语 + 删 `_make_regex_redactor`，**行为变更**：正文不再被全量正则改坏 `password=expr`）；S3 本地 NER
单独里程碑。`feature/pii-unify` 暂未进 dev。

## 2026-06-14 — PII 统一 S2（代码正文降 tokens_only，行为变更）

**变更**：代码**正文 body** 的结构化脱敏从 `full`（全量正则）降为 `tokens_only`——仅高置信密钥 token
（`sk-`/`gh?_`/`AKIA`/JWT）+ 自定义词，**不再跑** KV/手机/邮箱/卡/host/url 全量正则。核心收益：正文不再
被改坏（`password = get_secret()` 右侧不再被 KV 正则吞成 `[凭据]`），硬编码 `sk-`/`AKIA` 密钥仍被拦。
代价（用户「稳一点」取舍）：正文里的非 token PII（邮箱/手机/字面量密码）不再脱。

**范围决策（§9.5，2026-06-14 用户确认「只降正文」）**：仅代码 body 降档；代码**头部注释**仍 `full`；代码
**prompt 字段**（`file_path`/`related_snippets`/`path_candidates`/`diagnostics`）**保持 `full`**——不削弱
#36 vector ③ 的 PII 保护，`_make_regex_redactor` **保留**（不删，原计划的「删/并入」否决）；文档/PPT 正文
不变（仍 `full`）。

**落地**：
- `patterns.py`：新增 `redact_tokens_only_pii`（仅 `_TOKEN_FORMAT_RE`，受 `redact_credential` 开关）。
- `redactor.py`：新增 `PIIRedactor.redact_tokens_only`（token 原语 + 自定义词，无实体替换）。
- `guard.py`：`profile="tokens_only"` 落地（`redact_structured`/`redact_for_cloud` 删 NotImplementedError）。
- `pipeline.py`：`_redact_code_pii` body 行改 `redact_structured(body, profile="tokens_only")`（唯一行为改动）。

**验证**：privacy + 代码 PII + #36 回归 **141 passed**（含 #36 prompt 字段 full 的
`test_redact_masks_prompt_fields` 仍绿，证明 vector ③ 未削弱）+ 新增 tokens_only 单测（patterns/redactor/
guard）覆盖「token 脱 / KV 代码不改坏 / 手机邮箱不脱 / 开关关闭」；`pipeline`/`llm`/`api` **582 全绿**；
mypy/ruff/typos 干净。

**下一步**：S3 本地 NER（LAC+GLiNER benchmark，本地优先）单独里程碑。`feature/pii-unify` 累计
S1a+S1b+S2，暂未进 dev。

## 2026-06-14 — PII 统一 S3 本地 NER 后端（选 spaCy；GLiNER/LAC 弃用）

**方向**：人名/机构名检测从云端 LLM 改本地 NER，兑现「名字不出本机」。动手前调研推翻 §9 原定
「LAC+GLiNER benchmark」——**GLiNER 弃用**（硬依赖 `transformers≥4.51.3` 撞 vllm/DeepSeek-OCR 锁定的
`4.46.3`，装上破坏 OCR 环境）、**LAC 弃用**（2021 停更 + paddle 强耦合）；**选 spaCy CNN**（`zh/en_core_web_md`，
禁 `trf`）零 torch/transformers 不撞 OCR venv。用户决策：双模默认 + 报错→提示→一键环境配置（点确认自动装）。
设计文档 `docs/zh/backend/pii-local-ner.md`（已确认）。

**落地（分支 `feature/pii-unify-s3` off dev）**：
- **S3.1** `privacy/ner.py`：`SpacyEntityDetector`（进程级惰性单例，PERSON/ORG 并集去重）+ `probe_availability`
  廉价探测（`find_spec` 不加载模型）+ `LocalEntityDetector` 协议 + `NERUnavailableError`。
- **S3.2** `PIIGuard.detect_entities` + `PIIConfig.ner_backend(spacy|none)`/`ner_models`。
- **S3.3** Pipeline 5 处检测改 `guard.detect_entities`（去 llm 依赖 + `asyncio.to_thread` 卸载阻塞）；
  `_should_block_cloud` 补 `ner_backend==none` 短路；`_redact_code_pii` 去 refiner 参数。
- **S3.4a** `GET /ner/status` + 任务创建 fail-fast 400 `NER_BACKEND_UNAVAILABLE`（`remediable=true`）。
- **S3.4b** `NERSetupManager` 一键装包子进程（pip/spacy download 进当前 venv 免重启，concurrency 全规范
  + 模型名白名单）+ `POST /ner/setup`/`GET /ner/setup/status`，挂 lifespan shutdown。

**验证**：pipeline+privacy **328 passed**；api **204 passed**（含 ner 端点 15 例）；全量 **1327 passed**
（3 个 DeepSeek 失败为 pre-existing 环境缺失，`git stash` 验证基线同样失败，非本次回归）；mypy/ruff/typos 干净。

**下一步**：S3.5 前端一键配置 UX（截图验证）/ S3.6 `setup_ner.sh` + benchmark 留证（需装 spaCy 跑）/
S3.7 文档收尾 + PR base dev。云端 `detect_pii_entities` 暂留待 S4 清理。

## 2026-06-14 — PII 统一 S3.5 前端 NER 一键配置 UX（commit `b70dd01`）

**落地**：TaskForm 开启隐私脱敏后探测本地 NER 并就地补环境（人名/机构名脱敏依赖本地模型，数据不出本机）。
- `client.ts` 增 `getNerStatus`/`startNerSetup`/`getNerSetupStatus`；`schemas.ts` 两 zod schema（`NerStatusResponse`/`NerSetupStatusResponse`）。
- `TaskForm.tsx`：开 PII → `GET /ner/status`；`available=false` 时内联告警 + 缺失模型列表 + 「一键配置本地 NER 环境」按钮，**并禁止提交**（与后端 fail-fast 一致，名字不裸送云端）。点按钮 → `POST /ner/setup` → 轮询 `GET /ner/setup/status` 显示 spinner + 日志尾行；`done` 复检清告警放行，`failed` 显错误 + 重试，`409` 转轮询；卸载清理定时器。
- 3 语 i18n（`taskForm.ner*` + `errors.api.ner_*`）；`App.css` 告警/进度/就绪样式。

**验证**：新增 `tests/components/TaskFormNer.test.tsx` 4 例（注入 mock，不依赖真实 spaCy）+ 既有 `TaskForm.test.tsx` mock 补 3 函数；前端门禁全绿（vitest **115 passed** / `tsc -b` / `eslint`）。Playwright 对真实后端（insecure 本机）实测：告警态、安装态（流式 pip 下载日志）两屏 live 通过；就绪态由 unit test 覆盖（真实模型走 GitHub release 下载较慢，live ready 截图待下载完成补录）。

**遗留**：S3.6 benchmark（spaCy 模型安装完即可跑）/ S3.7 文档转已落地 + PR base dev。

## 2026-06-14 — PII 统一 S3.6 本地 NER benchmark 留证（commit `3431bc2`）

**落地**：切本地 NER 前的对照证据（pii-local-ner.md §7 三件套）。
- 自建金标 `tests/privacy/fixtures/ner_eval.jsonl`（26 条中英文短句，**非用户数据集**，含人名/机构 + 电话/邮箱/身份证干扰项）。
- `scripts/benchmark_ner.py`：PER/ORG 严格 P/R/F1 + 宽松召回（边界容忍）+ 主进程 CPU 测速 + 可选 `--cloud` 云端银标（失败优雅跳过）。
- `scripts/setup_ner.sh`：幂等装 spaCy(`.[ner]`) + `zh/en_core_web_md`。
- `docs/zh/backend/ner-benchmark.md`：实跑结论。

**实测**（docrestore env，spaCy 已装 zh+en_core_web_md）：人名 PER 召回 **0.92**（严格＝宽松）/精确 0.67；机构 ORG 召回 0.74、宽松 0.87/精确 0.81；单段 **8.8ms** CPU、吞吐 ~4.7k 字符/秒。**判定达标，按计划切本地 NER**——人名（隐私最关键）召回高；机构名缺口由结构化正则 + 自定义词兜底；精确率偏低是 over-redact（多脱敏）方向，对隐私安全。云端银标因 `.env` 网关 key 与 `GLM_API_KEY` 不匹配本次跳过（脚本支持，待补正确 key 复跑）。

**遗留**：S3.7 文档转已落地（pii-local-ner.md 状态、privacy.md/pipeline.md 同步、云端 detect_pii_entities 标死路）+ 整批 PR base dev（用户选「只做 S3.6」，S3.7 待确认）。

## 2026-06-14 — PII 统一 S3.7 收口 + 整批 PR（commit `4077657`/`4393e38`，PR #59）

**落地**（无行为改动，文档 + docstring）：
- 文档转「已落地」：`pii-local-ner.md` 状态、`pii-unification.md`（§5.1 spaCy 取代 LAC/GLiNER 超代记 + §6 S3 已落地/S4 待删）、`privacy.md`（§10.5 本地 NER 接缝 + §4.2 banner）、`pipeline.md`（模块表补 guard/ner + §8.3）、`llm.md`（detect 死路 banner）；en/ 三篇镜像同步。
- 代码 docstring：`guard.py` 修正过期「本类暂不含 detect_entities」；`cloud.py`/`redactor.py` 的 `detect_pii_entities` / `redact_for_cloud(refiner)` 标 `deprecated` 死路待 S4。

**整批 PR**：`feature/pii-unify-s3`（S3.1–S3.7，~14 commit）→ **PR #59 base dev**。门禁全绿：mypy 72 文件 0 错 / ruff / 前端 vitest 115 + tsc + eslint / pytest **1329 passed, 42 skipped**（除 3 个 pre-existing DeepSeek 环境缺失）。

**遗留（S4）**：删云端 `detect_pii_entities`（base/cloud）+ `PIIRedactor.redact_for_cloud(refiner)` 死路代码。PR #59 合 dev 后，整批 S1–S3 随 dev→main 时关 #36 相关。

## 2026-06-15 — PII 统一 S4 删除云端检测死路代码

**落地**（纯删除，无行为变化——删的是 S3 起已不被生产调用的死路；PR #59 已合 dev）：
- 云端实体检测方法链整链删除：`CloudLLMRefiner.detect_pii_entities` + `BaseLLMRefiner.detect_pii_entities` 默认实现 + `LLMRefiner` Protocol 声明；`CloudLLMRefiner` 收缩为 `BaseLLMRefiner` 薄子类（仅作 `provider="cloud"` 选型标识）。
- `llm/prompts.py`：删 `build_pii_detect_prompt` + `PII_DETECT_SYSTEM_PROMPT`。
- `llm/cloud.py`：删私有 helper `_extract_json_payload` / `_coerce_str_list` / `_CODE_FENCE_RE`（`_extract_json_payload` 在 `code_refine.py` 另有独立副本，不受影响）。
- `privacy/redactor.py`：删 `PIIRedactor.redact_for_cloud(text, refiner)`（async）+ 不再依赖的 `LLMRefiner` import；模块 docstring 改「实体词表由外部本地 NER 提供，本模块只按词表替换」。`redact_snippet`/`redact_regex_only`/`redact_tokens_only` 活路保留。
- `scripts/benchmark_ner.py`：删 `--cloud` 云端银标对照（`cloud_agreement` + 3 个 cloud 参数 + `asyncio`/`os` import）。
- 测试：删 `tests/llm/test_pii_detect_prompt.py`（整文件）；`test_cloud_truncation.py` 删 helper/PII 解析用例（保留截断检测）；`test_redactor.py` 删 redact_for_cloud 用例（长度降序覆盖改 `redact_snippet(lexicon)` 版保留）；`test_local.py`/`test_base_semaphore.py` 去 detect_pii_entities；6 个 pipeline fake refiner 的 detect_pii_entities 残桩清零。
- **区分保留**：`PIIGuard.redact_for_cloud(text, lexicon, *, profile)`（sync 活路闸口，与被删 async 同名不同类）全程未动。

**文档**（13 处 zh/en）：死路 banner 翻「已删除（2026-06-15）」+ 删把已删符号当现行 API 的描述——`privacy.md` §3.1 改展示 PIIRedactor 现行方法、§4.2/§7 改本地 NER；`llm.md` Protocol/CloudLLMRefiner 段；`pipeline.md`/`README.md`/`pii-unification.md`/`pii-local-ner.md`/`ner-benchmark.md`/`pipeline-parallel.md`/`architecture.md`/`deployment.md`/`known-issues.md`；保留「本地 NER 取代云端」迁移说明性引用。

**验证**：净删除 16 文件 +34/−548 行（1 文件删）；`mypy --strict` 74 文件 0 错 / ruff / typos（含 docs）/ **pytest 1307 passed, 45 skipped, 0 failed**；grep 确认 `detect_pii_entities` / `build_pii_detect_prompt` / `PIIRedactor.redact_for_cloud(refiner)` 代码零残留（仅余说明性注释）。

**遗留**：PR base dev 待提/合；dev→main 整批 release 时关 #36 相关。

## 2026-06-15 — #67 出云闸口下沉（PII 统一 S5，分支 `feature/s-cloud-egress-gate`）

**触发**：dev→main release 评审揪出两个 PII fail-closed 绕过实证——N1（dup-H2 重试在 `block_cloud` 守卫外，fail-closed 时仍发整篇 markdown 上云）、N2（实体 lexicon 从未线程化到 code 诊断，g++/clang 回显源码行里的人名照样上云）。根因：`pii-unification.md` §3.1「所有云端调用点只走闸口」是**约定**而非结构强制。

**设计**（先行 + 用户确认，落 `docs/zh/backend/pii-cloud-egress-gate.md`）：把 fail-closed 与实体兜底**下沉到全后端唯一出云点 `BaseLLMRefiner._call_llm`**（grep 证实零旁路）。机制经用户拍板选**方案 A / ContextVar**（与 `_call_llm` 既有 `current_profiler()` 惯例一致、task-local 正解并发子目录串味、零 Protocol/调用点签名改动）。关键判断：**闸口只做「仅实体 lexicon 替换」**（精确串替换，对代码标识符/import 路径一律安全），结构化脱敏的 profile 分档继续留字段级上游——彻底化解「共享出口无法分档」与 #36 tokens_only 回归两难。

**落地**：
- 新建 `llm/egress_gate.py`：`CloudEgressPolicy`（block_cloud/lexicon/guard，task 内 mutate）+ `_egress_policy` ContextVar + `egress_scope()`/`update_egress_policy()`/`current_egress_policy()` + `CloudEgressBlockedError`（仿 `LLMCircuitOpenError` 继承 RuntimeError）+ `enforce_egress(kwargs, provider)`（local/无策略短路 → block_cloud 抛错 → messages 非 system + prediction.content 仅实体兜底）。
- `privacy/redactor.py` 暴露 `apply_lexicon`；`privacy/guard.py` 加 `redact_entities_only(text, lexicon)`（仅实体、不跑结构化）。
- `llm/base.py._call_llm` 入口（熔断前）调 `enforce_egress`。
- `pipeline/pipeline.py`：`process_many` 每 leaf 安装 `egress_scope`（task-local 隔离）；doc(`_stream_process` finalize 前)/code(`_redact_code_pii` 改返 `(block_cloud, lexicon)` + `_code_pipeline` 同步)/ppt(两检测点) 三模式 `update_egress_policy`；N1 源头双保险 `if not truncated and not block_cloud`。
- 文档：`pii-unification.md` 补 S5 状态指针 + §3.1「约定→强制」更正注脚。

**测试**：新增 `tests/llm/test_egress_gate.py` 12 例（block 全入口 0 出云 / 实体兜底 / system 不脱 / prediction 脱 / local 不脱不拒 / guard None 不脱 / 幂等 / **并发隔离两 leaf 互不串味**）；`test_code_pii_header.py` 适配元组返回 + 强化 lexicon 回传断言。

**验证**：`scripts/check_quality.sh` 全绿（mypy --strict / ruff / typos / 前端 typecheck+lint / **pytest 1319 passed, 45 skipped**）。#36 的 code/实体回归全过。

**字段级加固（2026-06-16 追加，纵深防御）**：`_make_regex_redactor(pii_cfg, lexicon)` 非空时走 `redact_for_cloud`（结构化+实体）；`_code_pipeline` 传 `code_lexicon`；`build_consistency_audit_context` 经新 `_redact_unresolved_item` 对 `unresolved_items.context/note` 脱敏（**闸口够不到结构化 PII 的唯一缝**——闸口只兜底实体）。+5 测试（unresolved 脱敏 with/without redact、`_make_regex_redactor` lexicon/无 lexicon/未开）。设计文档 §5 标已落地 + 加 PlantUML 出云闸口时序图（`extract_and_compile.sh` 编译 exit 0）。

**遗留**：commit/PR 待用户授权后合 dev；dev→main release 时 `Fixes #67`。

## 2026-06-16 medium 严重度 issue 批量收口（#62 + #38–#50 + #61/#63/#64/#65）

分支 `feature/s-medium-issues`（从 dev 切），按 area 串行逐个有证据闭环，9 个 commit：

- **A #62 安全边界四连（HIGH，同根 #33/#34）**：resume/retry 复用持久化 `api_base` 重过 SSRF 守卫（`routes._revalidate_reused_api_base`）；`output_dir` 边界守卫下沉到写 sink（`update_result_markdown`）与 `resume_task`（`path_guard`）；无鉴权模式无法确认 bind host 时 fail-closed 拒启；device token 改 `os.open(O_EXCL\|0600)` 原子建消除 write→chmod 窗口期。
- **B #63+#61 privacy**：`ner_install._kill_proc` SIGTERM→grace→SIGKILL+`wait()` 回收、清理移入 `finally`（覆盖取消/泛异常）；NER `reset_detector_cache`（装后免重启生效，修 fail-closed 永久停用）；自定义词替换幂等（占位符当保护区）；内链 URL 覆盖 `[IPv6]`。
- **C #38/#39/#40 OCR worker**：ppocr-server stdout drain 提前到 `_wait_server_ready` 前（防 64KB pipe 死锁）；`_read_response` `errors=replace` + 超 buffer 单行捕获 → `_restart_worker`；`ensure()` 快速路径加 `is_switching` 守卫堵 TOCTOU。
- **D #41 persistence**：写事务加 `asyncio.Lock` 串行化（5 个写方法），读不加锁（WAL）。
- **E #42/#65 code_diagnostics**：preexec 增 `RLIMIT_DATA`、二次 `communicate` 带 timeout+kill 兜底；`_neutralize_into` 补 5MB 上限；修正 RLIMIT_CPU 误导注释；中和门改显式登记表 `_LANG_NEEDS_INCLUDE_SANITIZE`（go 显式登记不需中和）。
- **F #43/#44/#45 pipeline**：`cancel_task` 抢赢 `_finalize` 后补发终结帧（WS 不再永挂）；多子目录仅云端流式精修才等冷启动（`_will_stream_refine`，免白等 60s）；精修异常/熔断回退返回 `used_refiner=False`（不污染 RateController 吞吐桶）。
- **G #50 api**：`_resolve_crop_image` 补后缀白名单（与 `crop_figure` 对齐）。
- **H #64 契约**：`PIIConfigRequest` 暴露 `ner_backend`/`redact_person_name`/`redact_org_name`（无 spaCy 可结构化-only 脱敏，抽 `_resolve_pii_config`）；请求级 api_key resume 限制按 issue 验收「或文档化」分支落 known-issues + credentials docstring。
- **I #46/#47/#48/#49 前端**：rehypeRaw 后接 rehype-sanitize 白名单（`markdownSanitize.ts`，保 `data-page` 锚点、去 XSS）；上传预览图 `getUploadFileUrl` 带 token；`fetchResult` 退避重试不静默吞、失败翻 failed；`CreateTaskBody.llm.provider` 显式契约 + pii NER opt-out 字段。

**门禁**：`bash scripts/check_quality.sh` EXIT=0（mypy --strict 75 文件 ✓ / ruff ✓ / typos ✓ / 前端 typecheck+eslint ✓ / pytest 1364 passed 45 skipped；前端 vitest 121 passed）。

**遗留**：本批为 medium + HIGH #62，**不含** cleanup #66（用户确认暂缓）+ 评审0615 #62 之外未列项。待开 `feature/s-medium-issues → dev` PR（合并后这些 issue 随下一次 dev→main release `Closes` 关闭）。

## 2026-06-16 #66 cleanup 合集（评审0615 纯清理项）

PR #69 合入 dev 后，应用户「先做 #66」收尾最后一个评审0615 项。分支 `feature/s-cleanup-66`，9 个子项一个 commit（`refactor(core)`）：

1. `_extract_json` 两份（code_refine/code_repair）合并为 `llm/json_extract.extract_json` + 删悬空 docstring。
2. `app.py _auto_configure_llm` api_key 改走 `credentials.refill_api_key_from_env` 单点回填。
3. `code_diagnostics` 抽 `_is_traversal_or_absolute` 统一三处分段越级/空判定（绝对路径检测各站点口径不同——`Path.is_absolute`/`startswith('/')`/前导点——**故意不收敛**，避免削弱任一处 LFI 检查）。
4. `ner.py spacy.load(enable=["ner"])` 跳过 tagger/parser 提速。
5. `code_diagnostics` 加 run 级 `_MirrorCache`：批量诊断同语言共享 -I 根镜像只镜一次（O(N×M)→O(M)），**按 language 键**防跨语言中和规则错用；per-target body 放置不变（防 basename 碰撞）。
6. `pipeline._fill_one_gap` 改由 `_fill_gaps` 建一次 `PIIGuard` 下传（原每 gap 重建含 NER 初始化）。
7. 三文件 license header 补全为 13 行模板。
8. `test_auth` 加模块级 autouse fixture 还原 `_API_TOKEN`/`_INSECURE_MODE` 防顺序 flaky。

**门禁**：`bash scripts/check_quality.sh` EXIT=0（mypy 76 文件 / ruff / typos / 前端 / pytest 1371 passed 45 skipped）。待开 `feature/s-cleanup-66 → dev` PR。至此评审0615（#61–#66）代码层全部落 dev，仅余 dev→main release 收口关闭 issue。

## 2026-06-18 服务器源选择器对 PDF 放行（小修复）

**问题**：选择服务器路径时只能选目录、不能选单个文件——根因是服务器浏览（`/filesystem/dirs`）与暂存（`/sources/server`）沿用 `routes.py` 的 `_IMAGE_EXTS`（仅 6 种图片，不含 `.pdf`），导致浏览目录时 PDF 文件被 `_build_dir_entry` 过滤掉、直传 PDF 路径被 `_resolve_stage_path` 400 拒。本地上传走 `fileKind.ts`/`upload.py` 的 PDF 口径，故"本地能选单 PDF、服务器不能"。

**改动**（设计见 `docs/zh/pdf-mode.md` §11）：
- `routes.py` 新增 `_BROWSE_FILE_EXTS = _IMAGE_EXTS | {".pdf"}`（与上传 `_ALLOWED_EXTENSIONS` 同口径，源图预览/裁剪仍用窄口径 `_IMAGE_EXTS`）；`_build_dir_entry` 列出 + `_resolve_stage_path` 校验改用之。
- `_stage_files` 加"全图片 xor 全 PDF"互斥（复用 `MODE_CONFLICT`），与闸一（上传）/闸二（建任务）对称，混合早拒。
- 前端 `SourcePicker.tsx`：文件项图标按类型 📄/🖼；PDF 不走 `<img>` 缩略图，用 📄 占位（+`App.css .server-picker-preview-pdf`）。
- i18n `sourcePicker.emptyDir` 三语补"PDF"。

**下游零改动**（已核验）：单 PDF symlink → 临时目录 → 闸二 `_has_mixed_input` 对仅 PDF 放行 → `_expand_pdfs` 用 `iterdir()`+`is_file()`（跟随 symlink、文件名后缀即 `.pdf`）正确识别 → 复用既有 `render_pdf_to_dir`。

**门禁**：后端 `pytest tests/api/test_routes.py -k "Browse or Stage"` 11 passed（含新增 browse 列 PDF / stage 受单 PDF / stage 拒混合 3 例）；前端项目 eslint 干净（hook 内 npx ESLint 10.5.0 与 eslint-plugin-react 版本不兼容报 `getFilename`，非本次代码）、`tsc -b` 通过、vitest 组件 35 passed。

**遗留**：UI 视觉验证（📄 图标 + PDF 占位预览）需后端+前端栈起来 + 目录内有 PDF 才能截图实景，未跑；逻辑由 typecheck/单测覆盖。

## 2026-06-18 文档/PPT 预览数学公式渲染（KaTeX）

**需求**：OCR/LLM 产出的 `$...$` / `$$...$$` 公式在预览界面被当普通文本原样显示，需渲染成数学；文档模式与 PPT 模式都要支持。

**实现**（两模式共用 `DocCodePreview` 一处渲染入口）：
- 新增依赖 `katex@0.16.47`（与 rehype-katex 内置版对齐，避免 CSS/JS 类名错位）+ `remark-math@6` + `rehype-katex@7`。
- `markdownSanitize.ts` 集中导出共享插件链 `PREVIEW_REMARK_PLUGINS=[remarkGfm,remarkMath]` / `PREVIEW_REHYPE_PLUGINS=[rehypeRaw,[rehypeSanitize,schema],rehypeKatex]`，组件与测试共用避免漂移。**顺序关键**：KaTeX 放 sanitize 之后，使其 MathML/带样式 span 不被剥掉；不可信 HTML 已先过 sanitize，KaTeX `trust:false`（默认）输出无 XSS。schema 多放行 `div.className`（让 remark-math 的 `math-display` 占位类存活供 katex 识别）。KaTeX `throwOnError:false`：坏公式渲红字不崩页。
- `DocCodePreview.tsx` 改用共享插件链 + `import "katex/dist/katex.min.css"`。
- `markdown.ts` 加 `normalizeDisplayMath`：把"整行就是一条 `$$...$$`"（OCR 常压成一行）拆成独占行的 display 形式，否则 micromark 退化成行内；`escapeNonHtmlTags` 改为跳过公式区，避免 LaTeX 里的 `<`/`>` 被误转义。接入 `preprocessMarkdown`。

**验证**：前端全量 vitest 145 passed（含新增 KaTeX display/inline/容错/sanitize 共存、normalizeDisplayMath、escape 跳过公式区用例）；tsc -b ✓；`npm run build` ✓（KaTeX 字体/CSS 正确打包）；Playwright 实渲用户原始公式截图——能渲染、display 居中、无 katex-error。

**遗留/边界**：
- 用户示例公式渲染成功但矩阵塌成单行——根因是 **OCR/LLM 抽取质量**（`\ ` 反斜杠空格当成空格未换行应为 `\\`、`\mathbf{1}{m×m}` 缺下标 `_`、`\operatorname{L o w e r T r i}` 字母被拆带空格），非渲染问题。需在 LLM 精修 prompt / OCR 侧治本，不做脆弱的字符串硬替换。
- Tiptap 编辑器（编辑模式）暂不渲染公式（marked→HTML 不识别数学），公式以纯文本编辑；如需所见即所得需加自定义 math 节点，单独排期。
- 单 `$` 行内公式：remark-math 默认把 `$...$` 当行内数学，文档里字面 `$`（价格）可能误判，技术文档场景可接受。

## 2026-06-18 OCR LaTeX 抽取治本（精修 prompt）+ 编辑器公式渲染设计

承接上一条（预览侧 KaTeX 渲染）：渲染管线 OK，但用户公式矩阵塌成单行是 **OCR/LLM 抽取
质量**问题，治本在精修 prompt。

**#2 已实现**（`backend/docrestore/llm/prompts.py`，3 套独立 prompt 中改 2 套）：
- 文档分段 `REFINE_SYSTEM_PROMPT`：原本**完全没提公式**，新增"## 数学公式 LaTeX 规范化"小节
  （规则 18–21）：保数学含义不变（禁求值/化简/臆造），只修 OCR 语法错误——矩阵/方程组环境内
  `\ `（反斜杠+空格）/裸空格行分隔还原 `\\`、合并 `\operatorname{}`/`\mathrm{}` 内被拆标识符
  （`L o w e r T r i`→`LowerTri`）、补漏标的下标/上标、配平括号。
- PPT 按页 `SLIDE_REFINE_SYSTEM_PROMPT`：规则 3 由"公式原样保留、不得改写"改写为"保留含义
  +修 OCR 语法错误"，化解"原样保留 vs 修语法"的张力。
- 整篇 `FINAL_REFINE_SYSTEM_PROMPT`：**不动**（纯跨段去重职责；公式已在分段级修好，加进去会
  破坏其单一职责）。
- 不做脆弱字符串硬替换（`\ ` 是合法 LaTeX 控制空格，盲替误伤）；靠 LLM 理解语义来修。
- 改 prompt 自动使 LLM 磁盘缓存 fingerprint 变化（旧结果失效、重精修），符合预期。
- 测试 `tests/llm/test_prompts.py` 加 `test_both_prompts_have_latex_normalization_rule`；
  既有断言（跨页/不做跨页去重/复制代码）不受影响。`pytest tests/llm/` 149 passed 1 skipped。

**#1 已出设计**（`docs/zh/frontend/editor-math-design.md`）：文档模式 Tiptap 编辑器公式渲染。
现状：编辑器走 `markdownRoundtrip.ts`（marked/turndown）另一条链路，公式不渲染且 round-trip
可能被 `_`/`\` 转义破坏。设计分两期：①保真（round-trip 把公式当原子保护 + turndown 规则，
**先做、最痛**）②渲染+交互（接 Tiptap 官方 `@tiptap/extension-mathematics` 或自定义 Math 节点，
`data-latex` 为唯一真相，KaTeX NodeView + 双击编辑）。**待用户确认分期与方案 A/B 后实现**。

## 2026-06-18 编辑器公式渲染 阶段1（round-trip 保真，方案 B）

用户确认：分两期、方案 B（自定义 Math 节点 + KaTeX，与预览同栈）。先做阶段 1 保真。

- 现状/动机：文档模式 Tiptap 编辑器走 `markdownRoundtrip.ts`（marked/turndown）另一条链路，
  公式不渲染且 round-trip 会被 `_`/`\` 转义破坏（marked 当强调、turndown 转义）。
- 实现：
  · `mathNodes.ts`（新）：`MathInline`(inline atom)/`MathBlock`(block atom)，原始 LaTeX 存
    `data-latex`（唯一真相），atom 节点以源码态 `$...$` 显示——本期不渲染但不被改坏。
  · `markdownRoundtrip.ts`：`mathToPlaceholders` 在 marked 前把 `$$..$$`/`$..$` 抽成
    `data-math-display`/`data-math-inline` 占位（先块后行内避免 `$$` 被拆；占位非空避开
    turndown blank 丢弃；latex 进 data-latex 属性，marked/turndown 都不解析）；turndown 加
    `mathInline`/`mathBlock` 规则只读 data-latex 还原 `$..$`/`$$..$$`。
  · `MarkdownWysiwygEditor.tsx` 注册两节点；`App.css` 加源码态样式。
- 验证：`tests/features/task/mathRoundtrip.test.ts` 10 用例（string 两端 + **经 Tiptap 全链路**
  幂等，覆盖矩阵 `\\`、下标 `_`、`\alpha` 命令）全过；前端全量 vitest 155 passed；tsc -b ✓；
  项目 eslint ✓；`npm run build` ✓。
- 阶段 2（待实现）：MathInline/MathBlock 加 KaTeX NodeView + 双击编辑 + 输入规则。设计见
  `docs/zh/frontend/editor-math-design.md` §9。

## 2026-06-18 编辑器公式渲染 阶段2（KaTeX NodeView + 双击编辑）

承接阶段 1（保真）。给 `MathInline`/`MathBlock` 加 KaTeX NodeView，实现编辑器内所见即所得 +
双击编辑。

- `mathNodes.ts`：`createMathNodeView(displayMode)` —— `katex.render` 渲染 latex（throwOnError
  false / strict false，坏公式渲红字不崩）；双击进编辑（block=textarea / inline=input，回车提交、
  Esc 取消、block Shift+回车换行），`setNodeMarkup` 写回 data-latex。NodeView **只影响编辑态显示，
  不参与序列化**（getHTML 走 renderHTML 仍输出 data-latex）→ 阶段 1 round-trip 保真零回归。
  事件用 addEventListener（联合元素类型经 HTMLElement 引用调用规避类型重载丢失）。
- `MarkdownWysiwygEditor.tsx` 引入 `katex/dist/katex.min.css`（CSS 不进 mathNodes 以免污染单测
  导入图）；`App.css` 公式样式改为渲染态 + hover/selected/编辑框。
- 验证：`mathNodeView.test.ts` 6 用例（渲染 .katex/.katex-display、双击弹源码框、回车提交更新
  属性+重渲染+序列化同步、坏公式不抛错）；前端全量 vitest 161 passed；tsc -b ✓；项目 eslint ✓；
  build ✓；**Playwright 实测真实 Tiptap 编辑器**：块矩阵+行内公式 KaTeX 渲染、双击块公式弹出
  完整 LaTeX 源码框，两张截图通过。
- 至此编辑器公式两期均落地。未做（可选）：输入规则 / 工具栏「插入公式」（主场景是编辑 OCR
  已产出公式）。设计见 `docs/zh/frontend/editor-math-design.md` §9。
