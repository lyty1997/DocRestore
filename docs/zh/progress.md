# 开发进度

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
