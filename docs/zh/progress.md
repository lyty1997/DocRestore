# 开发进度

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

**遗留**：S2 校准决策（confirm-only=9 / weak+行号桥接=8 / 全局降 θ）待用户定；之后 S3（AGE-82）。详见 memory
[[code_mode_fragmentation_diagnosis]] / [[linear_workspace]]。

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
