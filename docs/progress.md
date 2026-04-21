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

# DocRestore 开发进度

## 2026-04-21 五补：任务列表单项删除 + 批量清理终态任务

### 背景

生产环境积累了 100+ 历史任务，但之前删除入口仅存在于 TaskDetail 页（需要先点进详情再删除），
对"我只想把旧任务全扫掉"的场景非常不友好，用户反馈"既不能访问也不能清理"。

### 落地

**后端**
- `TaskManager.cleanup_tasks(statuses) -> (deleted_ids, errors)`：内存 + DB 合并去重，
  逐个复用已有 `delete_task` 的状态机校验；`_collect_cleanup_targets` 拆出以压 C901 复杂度
- `POST /tasks/cleanup` 路由：仅接受 `completed`/`failed` 两种状态（校验在路由层做，
  兜底防止运行中任务被误删）；响应 `{deleted, failed, deleted_ids, errors}`

**前端**
- `SidebarTaskList.tsx` 结构从 `<button>` 单项 → `<div.stl-item-row>` 包住选择 `<button>`
  + 删除 `<button>`（嵌套 button 是无效 HTML，必须拆开）；悬停露出 "×"
- 头部渲染 "清理已结束" 按钮（当本地已加载任务中含终态任务时），一键调用 cleanup 接口
- 删除后通过 `onDeleted(tid)` 冒泡，`App` 若选中的正是被删任务则回到新建模式
- 双确认走复用的 `ConfirmDialog`，提示消息通过 i18n 带上 `{id}`/`{count}` 占位符
- i18n 三语补齐 `taskList.deleteItem` / `deleteConfirmMessage` / `clearFinished` / `clearFinishedMessage` / `clearFinishedResult` / `cannotDeleteRunning`

**测试**
- `tests/api/test_task_actions.py::TestCleanupTasks` 四用例（空入参 400、非终态状态 400、
  只清 completed+failed、无匹配 noop）

### 关键不变式

1. cleanup 永远只清 completed+failed；非终态状态 → 400（路由层先校验，manager 层也再过滤）
2. 已在运行的任务：单项删除按钮 `disabled`，批量清理走不到
3. 删除是逐个 `delete_task`，某条失败不影响其他（失败收集在 errors 数组返回）

### 验证

tsc + eslint + vitest（5 通过）前端全绿；mypy --strict + ruff + 23 个任务操作测试后端全绿。

---

## 2026-04-21 四补：TaskDetail 实时进度 + resume/retry 自动切新 task

### 背景

resume/retry 成功后创建了**新 task_id**，但前端：
- `handleResume/handleRetry` 只 refresh 列表 + 重拉**旧 task** 的信息，用户仍停在 failed 态详情页
- TaskDetail 没有订阅 WS 进度，即使用户手动点进新 task 也看不到进度条 — 只有一个 "处理中" badge 在转
- 新建任务页（App.tsx）用 `useTaskRunner` 订阅 WS + 渲染 `<TaskProgress>`；详情页没有复用该能力

用户需求：resume/retry 后也能和新建任务一样看到主进度 + 子目录分轨进度。

### 落地

**新 hook** `frontend/src/features/task/useTaskProgress.ts`
- 精简版"按 taskId 订阅 WS + 轮询降级"（80% 复制 `useTaskRunner` 的 connectWs/startPolling/cleanup 逻辑，但不涉及创建任务、不拉结果）
- 入口先 REST 查一次状态：已 terminal 直接触发 `onTerminal` 不开 WS；否则建 WS + 5s 连接超时 + 降级轮询
- `taskId` / `enabled` 变化时自动重订阅；本轮 onTerminal 只触发一次；`onTerminal` 用 ref 存，不触发无谓重订阅

**TaskDetail 改造**
- 新 prop `onSelectTask(taskId: string)`：resume/retry 成功后调 `onSelectTask(resp.task_id)`，App 的 `selectedTaskId` 切新值 → `key={selectedTaskId}` 让 TaskDetail 重挂载 → 新 taskId 自动 fetch + 订阅
- 头部 status badge 下方插 `<TaskProgress>` 段落，仅 `pending/processing` 态渲染
- `onTerminal` 回调重拉 `fetchTaskInfo + fetchResults + onTaskListRefresh`，UI 自动从"进度条"切到"结果预览"
- `.task-detail-progress` 加 margin 与新建任务视觉对齐

**App.tsx** 把 `handleSelectTask` 作为 `onSelectTask` 透传给 TaskDetail。

### 不变式

- `useTaskRunner` 完全不动（新建任务流程和旧版一致，降低回归风险）
- TaskDetail `key={selectedTaskId}` 保证切 task 时所有 state 重置
- 已终态任务挂载时不开 WS：先 REST 查一次，若 completed/failed 直接跳过 connectWs（后端本就会对终态立即 close，但省一次 WS 握手更干净）

### 验证

- `tsc --noEmit` + `eslint src/` + `vitest`：全过
- 浏览器级 E2E 未做（需真 OCR 跑起来才能触发 pending/processing 态）

## 2026-04-21 三补：部分失败可见（TaskDetail 多文档状态 UI）

### 背景

resume / retry 能让用户继续失败任务，但：
- `process_tree` 用 `asyncio.gather`（无 `return_exceptions`）— 任一子目录失败整个 task 直接 FAILED，已成功 doc 的结果**丢失**（`task.results` 留空）
- `/tasks/{id}/results` 强制要求 `status=completed`，failed 态一律 404
- 前端失败态只能看"整体 error"，看不到哪几篇子文档完成了、哪篇为什么失败

用户需求：打开历史任务后看到"各子文档进度 + 失败原因 + 已完成子文档可分别预览"。

### 落地

**后端**
- `PipelineResult.error: str = ""` — 子文档级错误字段
- `Pipeline.process_tree` 改 `asyncio.gather(..., return_exceptions=True)`；子目录异常转成 `PipelineResult(doc_dir=..., markdown="", error="{ExcType}: ...")` 占位；`asyncio.CancelledError` 仍一路传播（shutdown/用户取消语义不变）
- `TaskManager.run_task`：process_tree 返回后按 `r.error` 聚合判定：
  - 全成功 → `COMPLETED`
  - 任一失败 → `FAILED`，`task.error = "N/M 子文档失败: ..."`（`_summarize_failed_docs`），但 **`task.results` 保留所有结果**（含成功部分 + 失败占位）
- 新 `_persist_results(results, status, error)` 统一持久化 completed / failed 两态
- `/tasks/{id}/results` 放宽：只要有 results 就返回；透传 `error` 字段
- `TaskResultResponse.error: str = ""`
- zip 下载按 `r.error == ""` 过滤 doc_dirs，失败 doc 不参与打包

**前端**
- `TaskResultResponseSchema` 加 `error: z.string().default("")`（兼容老后端）
- `TaskDetail.tsx`：
  - 顶部汇总条："已完成 X/N，Y 个失败"
  - 多文档 tab 加 ✓/✗ 徽章 + hover tooltip 显示 error
  - 选中失败 tab → 显示 `<pre>{error}</pre>` 面板 + 提示"点击继续复用已完成部分"
  - 失败 tab 自动禁用编辑/保存按钮组
- i18n 三语：`taskDetail.docSummaryAll` / `docSummaryPartial` / `docFailedTitle` / `docFailedHint`
- App.css：`.btn-resume` / `.doc-tab--failed` / `.doc-tab-badge` / `.doc-summary` / `.doc-failed-panel`

**测试**
- `tests/pipeline/test_process_tree.py::TestProcessTreePartialFailure` — 一个子目录失败不影响其他；顺便加 `_fast_cold_start` autouse fixture 把 `RateController.COLD_START_TIMEOUT_S` 从 60s 缩到 0.5s（mock refiner 跑太快凑不齐 3 样本，每个测试原本都要等 60s 超时 fallback）
- 全量 `pytest tests/ --ignore=...`：536 passed / 31 skipped

### 不变式

- 外层 `asyncio.CancelledError`（shutdown/用户取消）**不吞**：`process_tree` 在 gather 结果里遇到 CancelledError 直接 raise，保持原有语义
- 部分失败的 task 仍保留 output_dir 下成功 doc 的 `document.md` + `images/`；resume 按钮复用它们继续跑失败部分

### 遗留

- 历史任务的 **per-doc 进度历史**（如"doc_A 在 OCR 70%，doc_B 已完成 final_refine"）仍无持久化：`task.progress` 只存最后一帧，WS 结束后查不到；本次按"终态显示已完成/失败"做到够用，未上 per-doc 进度历史（需改 Task 数据模型 + DB schema）
- warmup leaf（最长子目录）失败时冷启动超时等 60s — 不在本次 scope

## 2026-04-21 再补：LLM 精修断点续传

### 背景

B 方案让 resume 复用了 `output_dir` → OCR 层借 `{stem}_OCR/result.mmd` 自动跳过已完成图，但 LLM 精修没有任何缓存，resume 还是会把段级 / 整文档级精修全部重跑一遍。云端 LLM 是整个流水线的大头，这一段不省意义就小。

### 落地

- **新模块 `backend/docrestore/llm/cache.py::LLMCache`**：内容寻址的磁盘缓存
  - key = sha256(kind | model | api_base | prompt 字面量 | 输入文本) 前 32 字符
  - 段级（`seg_`）/ 整文档级（`final_`）独立命名空间
  - **truncated=True 的结果永远不写**（put 内部过滤）— 避免 resume 永久沿用半截输出
  - 异常 fallback 不写缓存（put 只在 refine 成功分支后调用）
  - 原子写（tmp → rename）防止 Ctrl+C 留半截文件
  - 目录创建失败自动降级为 `enabled=False`；json 损坏静默视作 miss
- **Pipeline 接入**（流式路径）：
  - `Pipeline._refine_segment_with_cache` helper 返回 `(result, used_refiner)`；`used_refiner=False` 时**不喂 `controller.record_llm`**（避免缓存命中的"伪时延"污染 RateController 的 L* 估算）
  - `_stream_process` 入口按 `{output_dir}/.llm_cache/` + `llm_cfg.enable_cache` 构造 cache
  - `_try_extract_and_refine`（主循环）/ `_stream_process`（末尾段）/ `_do_final_refine` / `_final_refine` 都接 cache + llm_cfg 参数
- **`LLMConfig.enable_cache: bool = True`**（默认开）
- **zip 打包**：`_build_result_zip_bytes` 只走 `document.md` + `images/`，不会扫 `.llm_cache`（验证过）

### 不变式

- 同一输入文本 + 同 model + 同 api_base + 同 prompt 字面量 → 必然命中
- 改 prompt（`prompts.py` 修改）→ fingerprint 自动变 → 旧缓存 miss（测试覆盖 `test_prompt_change_invalidates`）
- 截断 / 异常 → 不写，下次 resume 一定重试

### 验证

- 新增测试：`tests/llm/test_cache.py`（10 用例，覆盖命名空间隔离、disabled 无副作用、损坏 json、prompt 失效）+ `tests/pipeline/test_llm_cache_integration.py`（6 用例，覆盖 helper tuple 契约 + controller 不喂脏时延）
- `mypy --strict backend/docrestore` + `ruff check`：全绿
- `pytest tests/ --ignore=tests/pipeline`：417 passed

### 遗留

- gap_fill / pii_detect / doc_boundary 的 LLM 调用暂未纳入 cache（这些通常单次任务只调一两次，性价比低；后续有需要再加）
- 缓存不限大小 / 不 TTL：用户删除整个 output_dir 就清空；单次任务段数有限不担心撑爆磁盘

## 2026-04-21 补：断点续 OCR（B 方案：resume 路由）

### 背景

OCR 引擎层早已有"`{stem}_OCR/result.mmd` 存在即 load"的跳过逻辑（paddle_ocr.py / deepseek_ocr2.py），但 task 层只有 `retry_task`，复制原 config 的时候**不复用 output_dir**，所以重试会全量重跑，OCR 缓存形同虚设。

### 落地

- **`TaskManager.resume_task(task_id)`**（task_manager.py）：镜像 `retry_task`，唯一区别是把 `task.output_dir` 一起传给 `create_task`。状态校验同 retry（仅 FAILED 可继续；cancel 会把状态设为 FAILED 所以"用户取消"也能走 resume）
- **`POST /api/v1/tasks/{task_id}/resume`**（routes.py）：镜像 retry 路由，返回 `ActionResponse { task_id, message="已创建续跑任务" }`
- **前端 TaskDetail**：失败态按钮组新增"继续"（`btn-resume`，绿色主操作），位于"重试"之前；两个按钮都带 `title` tooltip 说明差异
  - `api/client.ts::resumeTask`
  - 三语 i18n 新增 `taskDetail.resumeTask / resumeHint / resumeFailed` + `retryHint`
  - App.css 加 `.btn-resume` 样式
- **测试** `tests/api/test_task_actions.py::TestResumeTask`：nonexistent / pending rejected / completed rejected / failed 成功（关键断言 `new_task.output_dir == old.output_dir`）+ retry 测试新增 `new != old` 的反向断言

### 边界

- 只省 OCR 时间：LLM 精修 / PII / dedup 仍然全量重跑
- Task 模型未改（不加 `resumed_from` 血缘字段），避免 DB migration
- 后端进程崩溃后无自动恢复；用户需要手动点"继续"

### 验证

- `mypy --strict` + `pytest tests/api/test_task_actions.py`：19 passed
- 前端 `tsc --noEmit` + `eslint`：通过

## 2026-04-21：GPU 设备选择去硬编码 + 自动探测

### 背景

用户拔掉 GPU0 (NVIDIA A2) 后仅剩 RTX 4070 SUPER，启动 ppocr-server 报 `NVMLError_InvalidArgument`。根因：项目多处 hard-code `PPOCR_GPU_ID=1` / `OCRConfig.gpu_id="1"` / 前端下拉写死 `"0"/"1"` 标签带 "A2"/"RTX 4070 Super"；换机器或改硬件即失效。

### 落地

1. **新模块 `backend/docrestore/ocr/gpu_detect.py`**
   - `list_gpus()`：pynvml 优先（带进程级缓存），失败回退 `nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap --format=csv,noheader,nounits`
   - `pick_best_gpu()`：按 `memory_total DESC, memory_free DESC, index ASC` 排序取第一
   - `GPUInfo(index, name, memory_total_mb, memory_free_mb, compute_capability)`

2. **OCRConfig.gpu_id: `str = "1"` → `str | None = None`**（`None` = 自动）。`OCRWarmupRequest.gpu_id` 同步。

3. **EngineManager**
   - `ensure()` 入口处 `config.gpu_id is None` 时调 `pick_best_gpu() or "0"` 并 `model_copy` 落地，保证下游 `_is_matched` / `_start_ppocr_server` / `CUDA_VISIBLE_DEVICES` 都基于具体值
   - 新增 `current_gpu_name` 属性（借 `list_gpus()` 缓存反查）
   - `_start_ppocr_server` / `paddle_ocr.py` / `deepseek_ocr2.py` 都加了 `gpu_id or pick_best_gpu() or "0"` 兜底，以防 pipeline 直连 `create_engine` 未落地

4. **新路由 `GET /api/v1/gpus`** → `GPUListResponse { gpus, recommended }`；`/ocr/status` 响应新增 `current_gpu_name`；`/ocr/warmup` 对 `gpu_id=None` 的请求先 `pick_best_gpu()` 再传给 `ensure()`

5. **app.py `PPOCR_GPU_ID`** 覆盖仅在非空时生效，空/未设保留 `None`

6. **前端**
   - `api/client.ts::listGpus` + schemas；TaskForm 挂载时拉列表，下拉首项 "自动（推荐 GPU N - 型号）"，其余动态渲染
   - i18n 三语（zh-CN/zh-TW/en）移除 `taskForm.gpu0/gpu1`，新增 `taskForm.gpuAuto` / `taskForm.gpuAutoWithHint`
   - `warmupOcrEngine` 对空 gpuId 不再写进请求 body，交由后端自动选
   - 状态匹配逻辑：`gpuId=""` 时只要 model 匹配即视为就绪（不再比较 current_gpu）

7. **scripts/start.sh**：`PPOCR_GPU_ID` 默认改空；空值时不导出 `CUDA_VISIBLE_DEVICES`，让 vLLM 自动枚举

8. **文档** zh/en 的 `deployment.md` / `backend/ocr.md` / `backend/api.md` / `backend/data-models.md` 同步新行为，systemd 片段改成可注释的模板

### 验证

- `mypy --strict backend/docrestore`：43 个文件 0 error
- `ruff check backend/docrestore`：通过
- `pytest tests/ --ignore=tests/pipeline`：413 passed / 7 skipped
- 新增测试：`test_warmup_without_gpu_id_uses_pick_best`、`TestGpuListing`（覆盖 `/gpus` + 空列表路径）
- 前端 `tsc --noEmit` + `eslint src/` + `vitest`：全通过
- 线下烟测：`GET /api/v1/gpus` 在当前单卡 4070 SUPER 上返回 `gpus=[{index:"0", name:"NVIDIA GeForce RTX 4070 SUPER", memory_total_mb:12282, ...}], recommended:"0"`

### 遗留

- 现在系统只剩 1 张 GPU，自动选"最好"只有一个候选；多卡机器上的 tie-break / LLM 任务抢占行为需要真实场景再验证
- 如果将来 A2 重新插回，用户可在前端直接选下拉切换，或显式设置 `PPOCR_GPU_ID`

## 2026-04-17 补：PII JSON 解析容错 + 精修截断一律回退原文

### 背景

用户实测两个问题同时出现：

1. **PII 实体检测 JSON 解析失败**：某些 LLM（GLM-5 等）在 instruct 模式下会把 JSON 用 markdown code fence 包裹返回：
   ```
   ```json
   {"person_names": ["..."], "org_names": ["..."]}
   ```
   ```
   `json.loads` 直接对整串解析必然 JSONDecodeError，PII 检测抛 RuntimeError，后续云端 LLM 精修被 `block_cloud_on_detect_failure` 全部跳过。

2. **"段 X 疑似截断（输入 N 行 → 输出 M 行）"只是 warning 没 fallback**：输出不到输入一半的截断段，`result.markdown` 仍是截断内容，直接进入下游 reassemble + final_refine。一旦截断，后半段内容丢失，下游拿到残缺文档。

### 完成内容

**PII JSON 解析容错**（`backend/docrestore/llm/cloud.py`）
- 新增 `_extract_json_payload(raw) -> str`：
  - 纯 JSON → 原样返回
  - ```` ```json\n...\n``` ```` / ```` ```\n...\n``` ```` → 正则剥围栏
  - 无围栏但前后带说明文字 → 取首 `{` 到末 `}` 之间子串
- `detect_pii_entities` 先走 `_extract_json_payload` 再 `json.loads`；错误信息保留原始 raw（前 200 字）便于调试

**截断段一律回退原文**（`backend/docrestore/pipeline/pipeline.py::_refine_segments`）
- 统一 `finish_reason=length`（refiner 自报）与行数比例启发式为单一分支
- 任一判定 truncated=True → `markdown = seg.text`，`gaps = []`（截断后 LLM 返回的 gap 坐标不可信），保留 `truncated` 标记供 `_collect_warnings` 产生 warnings
- 日志从"疑似截断"改为"疑似截断（...），回退到原文"

**整篇精修截断回退**（`backend/docrestore/pipeline/pipeline.py::_final_refine`）
- `result.truncated=True` → 返回原 `doc`（不用被截断的 `result.markdown`）
- 非截断路径照常用精修结果

### 测试

- `tests/llm/test_cloud_truncation.py`（+9 用例）：
  - `TestExtractJsonPayload`(5)：纯 JSON / ```json / ```/ 前后空白 / 前后说明
  - `TestDetectPiiEntitiesJsonParse`(3)：code fence 复现用户场景 / 裸 JSON / 完全非 JSON RuntimeError
- `tests/pipeline/test_truncation.py`：
  - 调整 `test_short_output_gets_flagged_truncated` / `test_refiner_self_reported_truncation_falls_back`：断言 markdown 回退 `input_text` 且 gaps 清空
  - 新增 `TestFinalRefineFallback`(2)：truncated 回退原 doc / 未截断使用精修结果
- `tests/pipeline/test_warnings_e2e.py::test_all_three_warning_types_aggregate`：因"truncated 清空 gaps"语义调整，改为 `max_chars_per_segment=30` 强制按标题分段，段 1 启发式截断 + 段 2 产生 gap + final_refine 截断，三种警告独立产生

### 回归

- pytest: 521 passed + 8 skipped（原 511+8，新增 10 个）
- mypy --strict: 108 文件 0 错误
- ruff check: 全部通过

### 语义变化（需要注意的向下兼容风险）

- 旧行为：截断段的 `RefinedResult.markdown` 是 LLM 截断输出；下游得到半截的"精修"markdown
- 新行为：截断段的 `RefinedResult.markdown` 是**原文**；下游得到原始 OCR 结果（未精修但信息完整）
- `_collect_warnings` 仍会告知用户该段截断；用户看到 warnings 时输出的正确读法是"段 X 未精修，按原文拼接"
- 如果有外部消费者依赖 "truncated=True 时 markdown 非空且是 LLM 输出"，需要同步调整（本仓库内已全部同步）

---

## 2026-04-17 补：ppocr-server 孤儿进程四层兜底清理（atexit/PDEATHSIG/SIGHUP/启动扫描）

### 背景

上一轮（同日）shutdown 链路加固仅覆盖 async lifespan 能完整执行完的退出路径；实测发现用户在 "任务 OCR 超时 → lifespan shutdown 卡住 → Ctrl+C 两次" 场景下，uvicorn force-quit 直接 `sys.exit`，`_stop_ppocr_server` 根本没机会跑，残留现场：

```
PID 2708324  PPID=1         paddleocr genai_server ...   ← ppocr-server 被 init 收养
PID 2708805  PPID=2708324   VLLM::EngineCore             ← 7758MiB 显存占用
```

手工 `killpg(2708324, SIGTERM)` 一次性带走整个进程组（ppocr-server + vLLM + resource_tracker）—— killpg 机制本身正确，问题是根本没被触发。

### 根因

async lifespan 清理路径在下列退出场景全部失效：
- **uvicorn force-quit（两次 Ctrl+C）**：第二次 SIGINT 设 `server.force_exit=True`，跳出 main loop，lifespan shutdown 的 `await` 被截断
- **SIGTERM/SIGHUP 默认行为**：Python 默认对 SIGTERM/SIGHUP 是 terminate，不跑 atexit；uvicorn 接管 SIGINT/SIGTERM，不管 SIGHUP
- **SIGKILL / OOM killer**：任何软件层机制都救不了；vLLM 作为 ppocr-server 子进程未继承 PDEATHSIG

### 完成内容

四层兜底清理机制（`backend/docrestore/ocr/engine_manager.py` 模块级）：

**A. atexit hook** —— 主防线
- `_kill_pgid_sync(pgid, grace_seconds=3.0)`：同步 SIGTERM → 轮询探活 → SIGKILL
- `_track_pgid(pgid)` / `_untrack_pgid(pgid)`：幂等注册/取消 atexit 回调
- `_start_ppocr_server` 启动成功后 `_track_pgid(proc.pid)`；`_stop_ppocr_server` 开头 `_untrack_pgid(pid)` 避免重复 kill
- 覆盖：uvicorn force-quit / sys.exit / Python 主线程正常结束

**B. PDEATHSIG(SIGKILL)** —— kill -9 兜底
- `_prctl_set_pdeathsig()`（preexec_fn）：Linux only，ctypes 调 libc `prctl(PR_SET_PDEATHSIG, SIGKILL)`；fork 子进程里 logging 不安全，异常静默吞掉
- 传给 `asyncio.create_subprocess_exec(..., preexec_fn=_prctl_set_pdeathsig)`
- 覆盖：docrestore 被 SIGKILL → 内核自动 SIGKILL ppocr-server（vLLM 孤儿仍依赖 D 下次启动兜底）

**C. SIGHUP handler** —— 终端关闭 / SSH 断开
- `_sighup_handler`：同步 killpg 所有 tracked pgid + 转发 SIGTERM 给自己（让 uvicorn 走正常 shutdown）
- `_install_signal_handlers()`：幂等安装，SIGINT/SIGTERM 交给 uvicorn
- 覆盖：uvicorn 不接管的 SIGHUP

**D. 启动时扫描清理** —— 上次遗留
- `cleanup_stale_ppocr_servers()`：遍历 `/proc`，按 cmdline 匹配 `paddleocr` + `genai_server`，解析 `/proc/<pid>/stat` 字段 4（pgid）去重后批量 `_kill_pgid_sync`
- `lifespan` startup 开头 `asyncio.to_thread(cleanup_stale_ppocr_servers)`
- 覆盖：上次 kill -9 残留的 vLLM 孤儿

### 测试（`tests/ocr/test_engine_manager.py`）

新增 14 个用例：
- `TestKillPgidSync`(3)：SIGTERM 生效 / SIGKILL 兜底 / ProcessLookupError 早退
- `TestPgidTracking`(4)：注册 atexit+signal / 幂等 / untrack / 未 track 的 pgid noop
- `TestSighupHandler`(1)：killpg 所有 tracked + 转发 SIGTERM
- `TestExtractPaddleocrPgid`(3)：正向解析 / 非 paddleocr / cmdline 读失败
- `TestCleanupStaleServers`(3)：pgid 去重 / 非 Linux / listdir OSError

### 回归

- pytest: 511 passed + 8 skipped（原 497+8，新增 14）
- mypy --strict: 108 源文件 0 错误
- ruff check: 全部通过
- 手工验证：`killpg(2708324, SIGTERM)` 一次带走整个进程组包括 vLLM

### 残余风险

- docrestore 被 SIGKILL（kill -9 / OOM killer）时 atexit 不跑，PDEATHSIG 能杀 ppocr-server 但 vLLM 仍孤儿 —— 依赖下次启动 D 扫描兜底
- 非 Linux 平台（macOS/Windows）PDEATHSIG 和 /proc 扫描都不可用（本项目本就要求 Linux + CUDA，已接受）

---

## 2026-04-17 修复 OCR worker 假死/shutdown 孤儿进程（vLLM EngineCore）

### 背景

用户反馈：任务处理多篇子文档，第 5 篇 OCR 响应超时触发 `_restart_worker` 重试，worker 没能重启而是卡住，用户 Ctrl+C 退出后，`VLLM::EngineCore` 成为孤儿进程（ppocr-server 随之残留）。

### 根因

四层叠加：

1. **A — `base.py::shutdown` 无短超时**：graceful `{"cmd":"shutdown"}` 命令的响应等待继承 `paddle_ocr_timeout=300s`，worker 假死时雪崩。
2. **B — `engine_manager.py::_shutdown_current` 清理顺序脆弱**：`_stop_ppocr_server` 不在 finally，`engine.shutdown` 抛 `CancelledError` 时直接跳过，ppocr-server（独立 session leader，`start_new_session=True`）连同内部 vLLM EngineCore 永远无人清理。
3. **C — lifespan shutdown 不 cancel 运行中任务**：OCR 任务仍在 `_send_command` 里读写 worker stdin/stdout 时，`pipeline.shutdown()` 并发调用 `engine.shutdown` → 两个协程争抢同一 StreamReader，行为未定义。
4. **D — `_restart_worker` 走 graceful 路径**：worker 已假死仍先发 shutdown 命令属于无用操作（A 修好后影响变小，但语义应该直接）。

### 完成内容

**OCR 引擎改动**（`backend/docrestore/ocr/base.py`）
- 新增类常量 `SHUTDOWN_COMMAND_TIMEOUT_SECONDS = 3.0`
- `WorkerBackedOCREngine.shutdown(*, force: bool = False)`：
  - `force=False` 默认：`asyncio.wait_for(_send_command({"cmd":"shutdown"}), timeout=3.0)`，TimeoutError/Exception 立刻跳到 `_terminate_process`
  - `force=True`：跳过 graceful 命令，直接 terminate
- `_restart_worker` 改为 `await self.shutdown(force=True)`

**EngineManager 改动**（`backend/docrestore/ocr/engine_manager.py`）
- `_shutdown_current` 改 try/finally 结构：`_stop_ppocr_server` 始终在外层 finally，内层 `engine = None` 也在 finally → engine.shutdown 成功/失败/CancelledError 都不影响 ppocr-server 清理

**TaskManager 改动**（`backend/docrestore/pipeline/task_manager.py`）
- 新增 `async def shutdown(self)`：cancel 所有 `_running_tasks` + `asyncio.gather(return_exceptions=True)` 等退出，记录非 CancelledError 的异常

**lifespan 改动**（`backend/docrestore/api/app.py`）
- cleanup 顺序调整：`warmup_task.cancel` → `manager.shutdown()` → `cleanup_task.cancel` → `pipeline.shutdown()`
- 保证 pipeline.shutdown 运行时没有并发的 OCR 任务抢 worker 流

### 测试

- `tests/ocr/test_paddle_ocr.py`（+3 用例）：
  - `test_shutdown_fast_when_worker_unresponsive` — readline 永不返回时 shutdown < 5s
  - `test_shutdown_force_skips_graceful_command` — force=True 零延迟 + 不发 shutdown 命令
  - `test_restart_worker_uses_force_shutdown` — `_restart_worker` 不发 shutdown 命令
- `tests/ocr/test_engine_manager.py`（+1 用例）：
  - `test_shutdown_stops_ppocr_even_if_engine_shutdown_cancelled` — engine.shutdown 抛 CancelledError 时 `_stop_ppocr_server` 仍被调一次
- `tests/pipeline/test_task_manager.py`（+3 用例）：
  - `test_shutdown_cancels_pending_running_tasks` — 挂起任务被 cancel + running_tasks 清空
  - `test_shutdown_noop_when_no_running_tasks` — 空集合快速返回
  - `test_shutdown_swallows_task_exceptions` — 任务非 CancelledError 异常不向外抛

### 回归

- `pytest -q`：497 passed, 8 skipped（新增 7 用例）
- `mypy --strict backend/`：41 files 0 errors
- `ruff check backend/ tests/`：All checks passed

---

## 2026-04-17 process_tree 多子目录并行（asyncio.gather）

### 背景

用户反馈：含 2 个子目录的 image_dir 处理时，subdir 1 进入 PII/LLM 精修阶段后 GPU 空闲（`nvidia-smi` 0%），subdir 2 要等 subdir 1 完全处理完才开始 OCR。

根因：`pipeline/pipeline.py::process_tree` 的 `for leaf in leaf_dirs: await process_many(...)` 是**同步 for 循环**，从 `eed4c8a` 初版就如此。`a778bcc` 的 "Pipeline 级并行" 优化覆盖的是**跨 API task** 并发 + LLM API 全局限流，**不覆盖单 task 内多子目录**。

### 完成内容

**Pipeline 改动**（`backend/docrestore/pipeline/pipeline.py`）
- `process_tree` 多子目录分支：`for` 循环 → `asyncio.gather(*[_process_leaf(...) for ...])`
- 抽出 `_process_leaf(index, leaf, ...)` 协程：负责单个叶子目录的 `process_many` 调用 + `doc_dir` 补全
- 异常语义保持：`asyncio.gather` 默认 fail-fast，任一 subdir 失败即整个 task FAILED
- profiler 兼容：`current_profiler()` 依赖 ContextVar，asyncio.Task copy 时自动继承父 context → 多子目录共享同一根 profiler，事件按时间戳合并无污染

**并发模型**（已由锁机制天然保证）
- OCR：`gpu_lock` 串行（峰值 ≤ 1），防 GPU OOM
- LLM：`llm_semaphore` 限流（默认 3），多 subdir 的 refine/fill_gap/doc_boundary 可并发
- PII regex / dedup / reassemble / render：纯 CPU/IO，真正并行

**前端进度分轨展示（配套）**
- `TaskProgress` dataclass + `ProgressResponse` pydantic 模型新增 `subtask: str = ""` 字段；`_wrap_progress` 除原有 message 前缀外，把 `dir_label` 写入 `p.subtask`
- `useTaskRunner` 把 `progress: TaskProgress | undefined` 改为 `progresses: Record<string, TaskProgress>`，WS / polling 收到帧时按 `subtask` 作为 key 分桶更新
- `TaskProgress.tsx` 重构：主进度条（key = ""）+ 虚线分隔 + "并行处理 N 个子文档" + 每个 subtask 一条直接展开的进度条（不折叠），含 subtask label / stage / counts / percent / message
- 三语 i18n 加 `taskProgress.subtasksLabel`；`App.css` 加 `.subtasks / .subtask-row / .subtask-label` 样式
- 视觉验证：`screenshots/task_progress_parallel_subtasks.png`（用 `?mock=progresses` URL query 注入 mock 数据截图后回滚，dev 代码未保留）

**测试**
- `tests/pipeline/test_process_tree_parallel.py`（3 用例）
  - `test_subdir_ocr_overlaps_with_prior_subdir_refine`：时间轴观察，断言 subdir 2 OCR start < subdir 1 refine end（跨子目录并行成立）
  - `test_ocr_still_serialized_by_gpu_lock`：OCR 峰值并发 = 1（gpu_lock 仍然兜底）
  - `test_progress_subtask_field_is_populated`：多子目录推送的 progress 帧 `subtask` 都非空且与 leaf 对应
- 回归：`tests/pipeline/` + `tests/api/` + `tests/llm/` 共 263 passed + 2 skipped；`mypy --strict` 41 文件 0 error；`ruff check` 0 issue；frontend `tsc` / `eslint` 0 error

**文档**
- `docs/zh/backend/pipeline.md` §9.3 新增"子目录并行"，原 §9.3 改为 §9.4
- `docs/en/backend/pipeline.md` 同步
- §3.1 调用约定句末补 "多子目录时用 asyncio.gather 并行调用"

### 关键决策

1. **为何不引入 `max_concurrent_subdirs` 配置？** 子目录并发度已由 gpu_lock（OCR 串行）和 llm_semaphore（LLM 限流）隐式约束，再加一个显式上限会让配置面冗余。当前语义清晰：子目录无条件并行，具体"并多少"由底层锁决定。
2. **为何用 `asyncio.gather` 而非 `TaskGroup`？** Python 3.11+ 的 `TaskGroup` 语义更严格但兼容性要求更高；项目 3.12 虽支持，但 `gather` + fail-fast 已经满足需求，不引入语法转换成本。
3. **为何把 `_process_leaf` 抽成实例方法而非内嵌函数？** 便于测试单独 mock（虽然目前没用到），且 profiler stage 的参数单独列出可读性更好。

### 遗留

- AGE-16 流式并行 Pipeline（单 task 内 OCR/LLM 页级流水）仍未实施，设计文档 `docs/zh/backend/references/streaming-pipeline.md` 保留
- 多任务 API 并发入口仍只有 `curl`/MCP，前端 `TaskForm` UX 是"一次一提交"

### 相关提交

- 本次（待提交）：process_tree 多子目录 asyncio.gather 并行

## 2026-04-17 OCR 引擎按需预热接口与前端预加载按钮

### 背景

启动后第一张图必须等引擎冷启动（PaddleOCR-VL ~80 s 装载、DeepSeek-OCR-2 ~40 s）。用户希望：(1) 服务启动后默认引擎自动预热；(2) 切换引擎时能在表单上预先触发，不必占用第一张图的处理时间。

### 完成内容

**后端**

- `api/schemas.py`：新增 `OCRWarmupRequest { model, gpu_id }` 与 `OCRStatusResponse { current_model, current_gpu, is_ready, is_switching }`。
- `api/routes.py`：
  - `_get_engine_manager(request)`：从 `app.state.engine_manager` 取实例，缺失时 500。
  - `GET /ocr/status`：直接映射 `EngineManager` 同名属性。
  - `POST /ocr/warmup`：`ready / switching / accepted` 三态分支；`accepted` 路径用 `manager.pipeline.config.ocr.model_copy(update={...})` 合成完整 `OCRConfig`，`asyncio.create_task(em.ensure(config))` 后台执行，立即返回。
- `api/app.py::lifespan`：构造完 `EngineManager` 后立即 `asyncio.create_task` 预热默认引擎；shutdown 时 cancel 未完成的 warmup task。
- `ocr/engine_manager.py`：新增 `current_gpu` / `is_ready` / `is_switching` 三个只读属性（`is_ready` 同时检查 `_engine and _engine.is_ready`，`is_switching` 复用 `_switch_lock.locked()`）。
- `pipeline/pipeline.py`：暴露 `engine_manager` 只读属性，方便路由层将来按需查询，无新行为。

**前端**

- `api/schemas.ts` + `api/client.ts`：`OcrStatusResponseSchema` / `OcrWarmupResponseSchema` + `getOcrStatus()` / `warmupOcrEngine(model, gpuId)`。
- `components/TaskForm.tsx`：
  - 新增 `EngineStatus = "idle" | "warming" | "ready" | "error"`。
  - 挂载时一次性查询 `/ocr/status`；命中目标且 `is_ready` 直接进入 `ready`。
  - 切换 OCR 引擎或 GPU 下拉框 → 重置回 `idle`。
  - 点击预加载按钮：进入 `warming` → 调 warmup → 启动 3 s 轮询 `/ocr/status`（命中目标即停，60 s 超时 fail-safe）。
  - `useRef<setInterval>` 在卸载时 `clearInterval`，避免泄漏。
- 三语 i18n：`taskForm.engineWarmup` / `engineWarming` / `engineReady` / `engineError`（zh-CN/zh-TW/en）。
- `App.css`：`ocr-warmup-area` flex 容器 + `btn-warmup` 浅色边框风格 + `engine-status--ready/error` 双色状态文案。

**测试**

- `tests/api/test_ocr_endpoints.py`：5 个用例覆盖 status 字段映射、未挂载 EngineManager 返回 500、warmup 三态分支、`accepted` 触发 `em.ensure` 一次且参数为 `model_copy` 后的完整 OCRConfig。
- `tests/api/` 全量 87 用例通过。

**视觉验证（playwright）**

- `screenshots/taskform_warmup_idle.png`：默认状态下"预加载引擎"按钮与 OCR/GPU 下拉同行排布正常。
- `screenshots/taskform_warmup_error.png`：后端未启动时点击 → 红色"加载失败"文字出现，错误处理路径打通。

### 关键决策

1. **为何 lifespan 后台预热而非阻塞启动？** 服务可用性优先：API/上传等接口不应等 OCR 引擎；预热失败也只 warning，不影响后续请求级 warmup。
2. **为何 `accepted` 路径走 `asyncio.create_task` 而不是同步等？** 引擎切换可能耗时数十秒，HTTP 请求不应卡这么久；前端通过 `/ocr/status` 轮询拿终态。
3. **为何 `is_switching` 用 `_switch_lock.locked()` 直接暴露？** EngineManager 的切换语义已经用 lock 表达，重复维护一个布尔 flag 容易和锁状态漂移。
4. **为何前端在 dropdown 切换时重置 `engineStatus`？** 旧的 ready 态对新选项无意义；强制用户重新预热避免误以为已就绪。

### 遗留

- 前端无对应单测（`useEffect + setInterval` 时序较繁，留待后续 vitest fake timers 补齐）。
- 真实 GPU 环境下的端到端预热耗时未做基准记录（依赖具体硬件）。

## 2026-04-17 Pipeline 级并行（多任务并发 + LLM API 全局限流）

### 背景

OCR batch 优化后单任务吞吐已达 0.56 img/s，但 LLM 精修阶段占整体耗时 50%+ 且 API 调用天然异步，单任务内部没有并发度。决定让多个 task 并行跑，同时新增全局 LLM 限流防止把云端 API 打爆。

### 完成内容

**配置层（AGE-pipeline-parallel）**

- `LLMConfig` 新增 `max_concurrent_requests: int = 3`：跨所有 pipeline 共享的 LLM API 并发上限
- 删除 `QueueConfig` 类及 `PipelineConfig.queue` 字段（原 `max_concurrent_pipelines` 迁移到 `LLMConfig`，语义更精确）

**Scheduler 重构**（`pipeline/scheduler.py`）

- `_pipeline_semaphore` → `_llm_semaphore`，构造参数 `max_concurrent_pipelines` → `max_concurrent_llm_requests`
- 属性 `pipeline_semaphore` → `llm_semaphore`
- 模块文档更新：跨任务 GPU 串行 + LLM API 限流

**BaseLLMRefiner 统一限流出口**（`llm/base.py`）

- 构造器新增 `semaphore: asyncio.Semaphore | None = None`（可选，便于单测）
- 新增 `_call_llm(kwargs)`：所有 `litellm.acompletion` 调用的统一出口，先 acquire `semaphore` 再发请求
- refine / fill_gap / final_refine / detect_doc_boundaries 全部改走 `_call_llm`
- `CloudLLMRefiner.detect_pii_entities` 同样走 `_call_llm`
- 子类 `LocalLLMRefiner` 通过继承自动支持（不覆盖 `_call_llm`）

**Pipeline 注入 Scheduler**（`pipeline/pipeline.py` + `api/app.py`）

- `Pipeline.__init__` 新增 `self._llm_semaphore: asyncio.Semaphore | None = None`
- 新增 `set_llm_semaphore(sem)`（在 `initialize()` 之前调用，否则默认 refiner 不带限流）
- `_create_refiner` 从 `@staticmethod` 改为实例方法，传 `semaphore=self._llm_semaphore`
- `api/app.py` lifespan 在创建 Scheduler 后立刻 `pipeline.set_llm_semaphore(scheduler.llm_semaphore)`

**Gap fill 三段锁序（重要）**

- 锁序：`llm_semaphore`（segment refine）→ 释放 → `gpu_lock`（re-OCR）→ 释放 → `llm_semaphore`（fill_gap）
- 非嵌套，无死锁；re-OCR 阶段必须释放 `llm_semaphore`，否则 sem=1 场景下其它任务会被误阻塞
- 集成测试（`tests/pipeline/test_concurrent_tasks.py::test_reocr_releases_llm_semaphore`）验证该不变量

**测试**

- 新增 `tests/llm/test_base_semaphore.py`：3 个用例覆盖 semaphore 注入 / 未注入 / 全入口都走限流
- 新增 `tests/pipeline/test_concurrent_tasks.py`：3 个用例覆盖 2 任务共享 semaphore(1) 峰值 ≤ 1 / Scheduler 共享 / gap fill 锁序
- 更新 `tests/pipeline/test_scheduler.py`：属性名迁移（9 处）
- 更新 `tests/test_config.py`：删 QueueConfig 导入 + 2 处 assertion，改为 `max_concurrent_requests` 默认值/覆盖测试
- 更新 `tests/pipeline/test_local_refiner_integration.py` / `test_local_provider_e2e.py`：`Pipeline._create_refiner` 从 staticmethod 改实例方法后的调用迁移
- 全量测试：396 passed, 1 skipped

**Benchmark 脚本**（`scripts/bench_pipeline_parallel.py`）

- 对比 `serial`（N 任务顺序）vs `parallel`（N 任务并发）wall-time，输出 summary.json + 每任务 profile top-stage 聚合
- 验收标准（设计文档 §8.3）：并发耗时 ≤ 0.6 × 串行（speedup ≥ 1.67×），实际运行由用户自行触发（需 GPU + API key）

**文档同步**

- `docs/zh/backend/pipeline.md` §9.2：`pipeline_semaphore 预留` 章节重写为 `LLM API 全局限流`（含三段锁序说明）
- `docs/en/backend/pipeline.md`：对应英文版更新
- `docs/zh/backend/data-models.md` / `docs/en/backend/data-models.md`：删 §4.6 QueueConfig，LLMConfig 补 `max_concurrent_requests` 字段
- `README.md` / `README.en.md`：配置列表移除 QueueConfig，LLMConfig 列项补全局并发上限说明

### 关键决策

1. **为何删 `pipeline_semaphore` 而不是保留预留位？** 粗粒度 pipeline 计数无法保护 LLM API 限流（一条 pipeline 可能发几十次 LLM 请求），细粒度 per-call 限流语义更精确。
2. **为何构造器注入 semaphore 而不是 ContextVar？** 便于测试直接构造（无需 ContextVar 上下文），同时避免 ContextVar 在非 Task 栈（如直接 await）里的传播歧义。
3. **为何 gap fill 不把 sem + gpu_lock 嵌套？** `sem → gpu_lock` 嵌套会导致：task A 持 sem 等 gpu_lock，task B 持 gpu_lock 等 sem → 潜在死锁。非嵌套三段锁序彻底规避。

### 遗留

- 真实 bench 未运行（需 GPU + GEMINI_API_KEY + 实际图片集，`scripts/bench_pipeline_parallel.py` 已就绪）
- AGE-13 Paddle worker 并发 HTTP 请求仍 pending（本次未触及，独立优化）

## 2026-04-16 OCR 批量推理 + Pipeline 全流程埋点 + GPU 显存监控

### 背景

上午 bench 验证 vLLM 通用参数优化对稳态吞吐无收益，下午切换方向——让 worker 内部用 `asyncio.gather` 把多张图并发喂给 vLLM，直接吃下 continuous batching 红利。顺手把 GPU 显存碎片化监控和 Pipeline 全流程埋点一起补齐。

### 完成内容

**配置层**

- `OCRConfig` 新增 `ocr_batch_size: int = 4` / `gpu_monitor_enable: bool = True` / `gpu_monitor_interval_s: float = 1.0` / `gpu_memory_safety_margin_mib: int = 1024`
- `PipelineConfig` 新增 `profiling_enable: bool = False` / `profiling_output_path: str = ""`（默认关闭避免生产开销；`DOCRESTORE_PROFILING=1` 强制覆盖）

**Profiler 基础设施**（`backend/docrestore/pipeline/profiler.py`）

- `Protocol` 定义统一接口 `stage() / record_external() / export_json() / export_summary_table()`
- `NullProfiler`：禁用时单次 ~50ns no-op，`stage()` 返回共享 context manager 不分配对象
- `MemoryProfiler`：事件收集 + JSON 导出 + 扁平化耗时表（按 `pipeline.total` 计算 share%）
- `ContextVar` 跨 async await 传播：`current_profiler() / set_current_profiler() / reset_current_profiler()`；嵌套调用自动复用外层 profiler，避免 Pipeline 实例并发调用时事件相互污染

**DeepSeek worker**（`scripts/deepseek_ocr_worker.py`）

- 新增 `ocr_batch` JSON-Lines 命令：`asyncio.gather(return_exceptions=True)` 并发处理整批，单张失败不拖垮整批，响应 `{ok: true, results: [...]}` 按序返回
- 后台 GPU monitor task：启动时 `asyncio.create_task`，1s 采样一次 `torch.cuda.mem_get_info() / memory_allocated() / memory_reserved()` 计算 `frag_ratio`，free 低于 `safety_margin_bytes` 时主动调 `empty_cache()`；日志前缀 `[gpu_monitor]` 写到 stderr
- `_build_init_cmd` 透传 `ocr_batch_size / gpu_monitor_*` 给 worker

**DeepSeekOCR2Engine**（`backend/docrestore/ocr/deepseek_ocr2.py`）

- 新增 `ocr_batch()` 覆写：已有 `result.mmd` 缓存直接磁盘加载，其余 pending 送 worker
- `_send_ocr_batch_with_oom_retry`：遇 `_OOMError` 对半降级（`size //= 2`），降到 1 仍 OOM 才抛 `RuntimeError`；不回升避免震荡
- 抽出 `_parse_single_result` 在 `ocr()` 和 `ocr_batch()` 间共享，避免两路径解析逻辑漂移

**Pipeline 主循环埋点**（`backend/docrestore/pipeline/pipeline.py`）

- `_task_profiler` async 上下文管理器：根调用创建 `MemoryProfiler` 并绑定 `ContextVar`，嵌套调用复用上层；退出时导出 `profile.json` + 写 summary table 日志
- 核心阶段埋点：`pipeline.total` / `pipeline.subdir` / `ocr.batch` / `ocr.single` / `cleaner.page` / `dedup.*` / `pii.phase` / `llm.doc_boundary` / `llm.refine_phase` / `llm.refine_segment` / `llm.gap_fill_*` / `llm.final_refine` / `reassemble` / `doc_split` / `render.write`
- `_run_ocr`：`isinstance(engine, WorkerBackedOCREngine)` 且 `batch_size >= 2` 时走 `engine.ocr_batch()`，否则回退逐张 `ocr()`——用 isinstance 而非 hasattr 避免 AsyncMock 测试替身误判

**测试**

- `tests/pipeline/test_profiler.py`：21 项单测覆盖 NullProfiler 零开销 / MemoryProfiler 事件记录 / export_json / export_summary_table / ContextVar 嵌套行为 / stage 异常记录

**Bench 脚本扩展**（`scripts/bench_ocr.py`）

- 新增 `--batch-size` CLI 参数：`>=2` 时整 run 一次 `engine.ocr_batch()`，`1` 走逐张 `ocr()` 对照基线
- `OCRConfig.ocr_batch_size` 随 preset 构造传入

### Benchmark 结果（RTX 4070 SUPER，36 张图 × 2 runs）

| 名称 | 引擎 | 预设 | batch | mean_run(s) | img/s | GPU_util_mean(%) | GPU_util_p95(%) | mem_peak(MiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| deepseek_baseline | deepseek-ocr-2 | baseline | 1 | 119.1 | 0.30 | 52.3 | 73.0 | 9867 |
| deepseek_optimized | deepseek-ocr-2 | optimized | 1 | 118.3 | 0.30 | 56.6 | 71.0 | 11683 |
| **deepseek_batch4** | deepseek-ocr-2 | optimized | **4** | **64.7** | **0.56** | 52.2 | **81.0** | 11843 |

对比 baseline：
- **吞吐 +87%**（0.30 → 0.56 img/s），超过目标 ≥0.45 img/s
- **GPU p95 利用率 +8pp**（73% → 81%），峰值段饱和度明显提升
- 峰值显存 +2GB（9867 → 11843 MiB），仍在 12GB 安全范围内
- 均值 GPU 利用率不变（52%）——批量让 work 更快结束，空闲段占比相对上升抵消了峰值提升

### 关键结论

1. **worker 内 `asyncio.gather` 是最短路径**：不改 Pipeline 粒度、不改 GPU 锁语义，一层并发就吃下 continuous batching 的 87% 吞吐红利
2. **`batch_size=4` 是 RTX 4070 SUPER 的甜点**：显存占用 11.8GiB/12GiB，已经逼近；再加大需配合 OOM halving 才稳
3. **Profiler 的 `isinstance(WorkerBackedOCREngine)` 比 `hasattr` 更稳**：AsyncMock 自动生成属性让 hasattr/iscoroutinefunction 双重误判，测试路径直接跑进 batch 分支返回 mock 对象被 dedup 模块吃不下

### 坑点

- `pytest.raises(ValueError)` 被 ruff PT011 要求加 `match` 参数，否则过宽
- worker `import torch` 之前写 `# type: ignore[import-untyped]` 会触发 mypy `unused-ignore`（torch 已装类型桩），直接去掉
- worker gpu_monitor 日志走 stderr，父进程 `_stream_stderr_progress` 只在 initialize 阶段读 stderr，初始化后停止——导致 bench log 看不到 `[gpu_monitor]` 行。短任务无影响（pipe buffer 够用），长任务需后续补一个 stderr consumer 转 logger

### 相关提交（dev 分支）

- `1524379` OCR 批量推理 + Pipeline 全流程埋点 + GPU 显存监控

### 遗留

- Paddle worker 并发 HTTP 请求（Task #13，延后）——当前 PaddleOCR 路径仍是基类 `ocr_batch`（逐张串行）
- worker stderr 持续读入 logger（gpu_monitor 日志回传父进程）
- Pipeline 级并行（两条 pipeline：一条 OCR 时另一条做 PII/LLM 精修）——下一步

## 2026-04-16 OCR vLLM 优化参数基线对比

### 背景

两款 OCR 引擎（PaddleOCR-VL / DeepSeek-OCR-2）均基于 vLLM 推理。先验证 vLLM 通用优化参数是否能带来吞吐收益，再评估通过 pipeline 并行捕获空闲 GPU 的空间。

### 完成内容

**配置层 —— 5 个 vLLM 通用优化字段透传**

- `OCRConfig` 新增：`vllm_enforce_eager: bool | None` / `vllm_block_size: int | None` / `vllm_swap_space_gb: float | None` / `vllm_disable_mm_preprocessor_cache: bool` / `vllm_disable_log_stats: bool`；另增 `paddle_server_backend_config: str`（YAML 路径透传给 `paddleocr genai_server --backend_config`）。
- `scripts/deepseek_ocr_worker.py`：`AsyncEngineArgs` 改为条件 kwargs 字典，仅当字段非 None/非默认时传入，避免污染 vLLM 默认值。
- `backend/docrestore/ocr/deepseek_ocr2.py::_build_init_cmd()`：5 个字段随 init 命令透传到 worker。
- `backend/docrestore/ocr/engine_manager.py::_start_ppocr_server()`：`paddle_server_backend_config` 非空时追加 `--backend_config <path>`。

**基准脚本三件套**

- `scripts/gpu_sampler.py`：独立进程 wrap `nvidia-smi -lms 500`，SIGTERM/SIGINT 转发 + CSV flush 保证完整数据。
- `scripts/bench_ocr.py`：统一 EngineManager.ensure() → warmup → N 次 run，写 `summary.json` + `per_page.csv` + `gpu_trace.csv`。每次 run 独立子目录避免增量 OCR 命中缓存。
- `scripts/bench_compare.py`：多次 bench 汇总 markdown 表；`_find_prefix()` 用 `startswith` 匹配 nvidia-smi 的 ` utilization.gpu [%]` 列名（带前导空格 + 单位后缀）。

**Benchmark 执行（RTX 4070，36 张图 × 2 runs）**

| 名称 | 引擎 | 预设 | init(s) | warmup(s) | mean_run(s) | img/s | GPU_util_mean(%) | GPU_util_p95(%) | mem_peak(MiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| paddle_baseline | paddle-ocr | baseline | 79.9 | 11.3 | 103.2 | 0.35 | 20.1 | 66.0 | 9131 |
| paddle_optimized | paddle-ocr | optimized | 79.9 | 11.9 | 106.3 | 0.34 | 19.8 | 65.0 | 9131 |
| deepseek_baseline | deepseek-ocr-2 | baseline | 56.6 | 5.0 | 119.1 | 0.30 | 52.3 | 73.0 | 9867 |
| deepseek_optimized | deepseek-ocr-2 | optimized | 41.6 | 5.1 | 118.3 | 0.30 | 56.6 | 71.0 | 11683 |

汇总表：`output/bench/summary.md`；每次 run 的 `summary.json` / `per_page.csv` / `gpu_trace.csv` 保存在对应子目录。

### 关键结论

1. **vLLM 通用优化参数对稳态吞吐无收益**：PaddleOCR 0.35→0.34 img/s、DeepSeek 0.30→0.30 img/s，差异在噪声范围内。
2. **`enforce_eager=True` 反而劣化 PaddleOCR -70%**（2.86 → 4.86 s/img）：PaddleOCR 的 `paddleocr_vl_09b.py` 默认让 vLLM 启用 CUDA Graph，强制 eager 会关闭图优化。据此从 PADDLE 优化预设中移除该参数，保留坏数据作为 `paddle_optimized_enforce_eager_v1`。
3. **DeepSeek 优化仅 init 节省 27%**（56.6s → 41.6s）：主要来自 `gpu_memory_utilization=0.9`（原 0.75）降低了首次预分配的抖动。推理稳态无差异。
4. **GPU 利用率偏低，验证 pipeline 并行空间**：PaddleOCR 均值 20% / p95 66%，DeepSeek 均值 52% / p95 73%。空闲 GPU 足以支撑 pipeline 层并行（OCR ↔ LLM 精修异步流水线、或多请求队列覆盖 idle）。

### 坑点

- vLLM CLI `--block-size` 仅接受 {1,8,16,32,64,128}，DeepSeek 官方脚本里的 256 是走 Python API 的（校验路径不同）；`bench_ocr.py` 将两个引擎的优化预设统一到 128。
- nvidia-smi CSV 列名形如 ` utilization.gpu [%]`（前导空格 + 单位后缀），`DictReader` 必须按前缀匹配而非精确 key。
- `pre-commit` hook 使用 `language: system`，commit 前必须 `conda activate docrestore`，否则 `mypy/ruff` 报 command not found。

### 相关提交（dev 分支）

- `8e16351` 配置层新增 5 个 vLLM 字段 + OCRConfig 透传
- `cc522e1` 新增 bench 脚本 (bench_ocr / gpu_sampler)
- `f238956` 新增 bench_compare 对比脚本
- `14db297` 优化预设 block_size 256 → 128
- `b4e4b9a` PaddleOCR 优化预设移除 enforce_eager
- `a404300` bench_compare 匹配 nvidia-smi 列名单位后缀

### 遗留

- 通用优化无稳态收益 → 下一步应设计 pipeline 层并行（OCR 与 LLM 精修异步流水，或队列式批处理），在空闲 GPU 周期内吸收更多任务。
- PaddleOCR GPU 利用率仅 20%，是首选并行目标。

## 2026-04-15 设计文档全量审查与同步

### 背景

项目长期迭代后，部分文档仍停留在 MVP Draft 阶段。对比代码真实状态后做一次全量审查与同步，并修剪 `README.md` 以提升首屏信息密度。

### 完成内容

**高优先级：过时文档重写**
- `docs/frontend/README.md`：彻底重写（207 → 86 行）。原稿为 AGE-12/AGE-13 Draft，声明 MVP 不做鉴权 / 不持久化 / 不支持取消删除重试，与代码实况严重冲突。新版只做索引 + 功能概览 + 工程结构 + API 对接清单，将细节指向 `tech-stack.md` / `features.md` / `backend/api.md`。

**PII 占位符文档校准**
- `docs/backend/privacy.md` §4.1 / §4.2：将示例占位符从英文 `[PHONE_XXX] / [EMAIL_XXX] / [PERSON_XXX] / [ORG_XXX]` 改为代码实际默认值 `[手机号] / [邮箱] / [身份证号] / [银行卡号] / [人名] / [机构名]`，并附注对应的 `PIIConfig` 字段名。

**后端总览与架构图补全**
- `docs/backend/README.md`：文件列表补齐 `api/auth.py` / `api/upload.py` / `persistence/database.py` / `ocr/engine_manager.py`；依赖图加入鉴权、上传、SQLite 持久化路径；接口表更新为 `Pipeline.process_many`。
- `docs/architecture.md`：分层图标注 Bearer Token 鉴权、分片上传、SQLite 持久化、EngineManager，同步当前 API 路由前缀。

**README 修剪**
- `README.md`：269 → 152 行（约 43% 精简）。去掉大段 Python 配置示例、完整 API curl 列表、完整上传三步示例，改为指向 `docs/deployment.md` / `docs/backend/api.md` / `docs/backend/data-models.md`。保留首屏必需的环境要求 / 安装 / 启动 / 最小 API 示例 / 输出说明。

### 未动区域（已确认与代码对齐）

- `docs/backend/data-models.md` — pydantic BaseModel 迁移已完整反映
- `docs/backend/api.md` — 端点表与 `api/routes.py` 一致
- `docs/backend/ocr.md` / `llm.md` / `pipeline.md` / `processing.md` — 接口与实现一致
- `docs/deployment.md` — 四环境 setup 脚本与 EngineManager 自动管理已同步
- `docs/frontend/tech-stack.md` / `features.md` — Vite 8 / React 19.2 / zod v4 / i18n 已同步

### 遗留

- AGE-16 流式并行 Pipeline 仍为设计文档，代码未实施（`docs/backend/references/streaming-pipeline.md`）
- AGE-26 LLM 截断检测机制（低优先级）
- AGE-33 前端多文档展示优化

### 决策记录

- **AGE-25 FixtureOCREngine 决策保留**（不清理）：生产代码已无引用，仅留在 `tests/support/ocr_engine.py` 作为 CI 离线测试 fixture，避免 pipeline 测试必须依赖 GPU。若未来决定 pipeline 测试必须跑真实 OCR，再重新评估。

## 2026-04-15 硬编码清扫（HIGH 优先级 6 项）

### 完成内容

**OCR 契约常量化**
- `ocr/base.py` 新增模块常量 `OCR_RESULT_FILENAME="result.mmd"` / `OCR_RAW_RESULT_FILENAME="result_ori.mmd"` / `OCR_DEBUG_COORDS_FILENAME="debug_coords.jsonl"`，消除 backend 里多处字面量
- `ocr/deepseek_ocr2.py` / `ocr/paddle_ocr.py` / `processing/cleaner.py` 及相关测试改用常量
- `scripts/deepseek_ocr_worker.py` 跨 conda 环境，保留字面量并加注释说明"需与 backend 常量手动同步"

**Worker 脚本路径可配置**
- `OCRConfig` 新增 `paddle_worker_script` / `deepseek_worker_script`（空串回退类默认）
- `WorkerBackedOCREngine` 新增 `_resolve_worker_script()` 钩子，`_find_worker_script()` 支持绝对路径

**PaddleOCR server URL 拼装去重**
- `OCRConfig` 新增 `paddle_server_host` / `paddle_server_api_version`，`build_default_paddle_server_url()` 方法
- `api/app.py` / `ocr/engine_manager.py` 两处 `http://localhost:{port}/v1` 字面量改为该方法

**ColumnFilter 阈值可配置**
- 新增 `ColumnFilterThresholds` BaseModel（13 个字段，含 chrome_y / min_sidebar_y_spread / left_candidate_* / right_candidate_* / boundary_padding / full_width_threshold / main_content_ratio / content_ratio / coord_range）
- `column_filter.py` 移除类常量，通过 `thresholds` 注入；`paddle_ocr.py` 坐标归一上界用 `coord_range`（替换 999 魔法值）

**ImagePreprocessor 归一化参数可配置**
- 新增 `normalize_mean` / `normalize_std` 参数，经 `init_cmd` 从 `OCRConfig` 透传到 worker

**ngram window 可配置**
- 类常量 `DEFAULT_WINDOW_SIZE=90`；`OCRConfig.ngram_window_size` / `ngram_whitelist_token_ids` 经 `init_cmd` 透传

### 验证

- `mypy --strict` 所有改动模块：0 errors
- `ruff check` 所有改动模块：0 issues
- `pytest tests/ocr/test_column_filter.py tests/ocr/test_config.py`：78 passed, 2 skipped
- `pytest tests/ocr/test_ngram_filter.py tests/ocr/test_preprocessor.py tests/ocr/test_paddle_ocr.py tests/ocr/test_engine_manager.py tests/ocr/test_router.py`：26 passed, 2 skipped

### 后续追加（同日 MEDIUM 清扫与文档同步）

**MEDIUM 批 1 — 超时/轮询配置化（commit `1096ee8`）**
- `OCRConfig` 新增 `paddle_server_shutdown_timeout=10.0` / `paddle_server_connect_timeout=2.0` / `paddle_server_poll_interval=2.0` / `worker_terminate_timeout=5.0`
- `_wait_server_ready` 新增 `connect_timeout` / `poll_interval` 关键字参数
- `api/upload.py` 清理轮询间隔提为 `_CLEANUP_INTERVAL_SECONDS`；`api/app.py` conda 探测超时提为 `_CONDA_DETECT_TIMEOUT_SECONDS`

**MEDIUM 批 2 — 收窄可确定类型的宽 except（commit `0f41284`）**
- `paddle_ocr.py:132` `Exception` → `(TimeoutError, ProcessLookupError, OSError)`
- `engine_manager.py:277` → `OSError`；`engine_manager.py:326` → `(TimeoutError, OSError, asyncio.IncompleteReadError)`
- `deepseek_ocr2.py:320` → `(OSError, asyncio.IncompleteReadError, RuntimeError)`
- `api/routes.py:143` → `(OSError, RuntimeError, ValueError)`
- 其余 10 处保留（LLM 调用、DB 降级、lifespan 最外层等跨异常族兜底，收窄反易漏异常）

**MEDIUM 批 3 — 截断检测 / stdio buffer / 路由魔法数（commit `715786c`）**
- `LLMConfig` 新增 `truncation_ratio_threshold=0.3` / `truncation_min_input_lines=20`，删除 pipeline 同名模块常量
- `OCRConfig` 新增 `worker_stdio_buffer_bytes=16*1024*1024`，替换 `base.py` 子进程 limit 字面量
- `routes.py` 分页上限 `100` / 批量上限 `5000` 提为模块常量 `_PAGE_SIZE_MAX` / `_STAGE_FILES_MAX`

**文档同步（commit `1be897b`）**
- `docs/backend/ocr.md` 补全 `ColumnFilterThresholds` 字段表、PaddleOCR/DeepSeek 新增字段表、修正 `enable_column_filter` 默认值漂移（True → False）、把 ppocr-server 启停描述中的 2s/10s 改为字段引用

### 遗留

- `docs/backend/pipeline.md` / `llm.md` 未同步 `worker_stdio_buffer_bytes` / `truncation_*` 字段（可后续补）
- LOW 优先级条目（重复的 URL 拼接片段、过度防御性 log、未用 import 等）未扫
- `upload.py` 的 `_MAX_FILE_SIZE_MB` / `_SESSION_TTL_SECONDS` 等 API 层字段暂保留为模块常量（进 APIConfig 需新建顶层结构、改动面不匹配收益）

## 2026-04-14 自定义敏感词支持 per-word 代号 + i18n 模块重构

### 完成内容

**自定义敏感词 → 代号映射（feature）**
- `pipeline/config.py` 新增 `CustomWord` dataclass（`word + 可选 code`），`PIIConfig.custom_sensitive_words` 由 `list[str]` 改为 `list[CustomWord]`
- `privacy/redactor.py` `_replace_custom_words` 按 word 长度全局降序替换（跨 code 保证 "张伟强" 先于 "张伟"），每个词使用自己的 code，为空回落 `custom_words_placeholder`；记录按实际 placeholder 聚合
- `api/schemas.py` `PIIConfigRequest.custom_sensitive_words` 同时接受 `list[str]` 与 `list[{word, code?}]`（新增 `CustomSensitiveWord` pydantic 模型）
- `api/routes.py` 新增 `_to_custom_words()` 把 API 输入统一转为 `CustomWord` dataclass 再塞入 `pii_override`
- `pipeline/pipeline.py` `_get_pii_config` 重建 `custom_sensitive_words`：`asdict` 降级出的 dict / 原始 CustomWord / 旧 str 三种形态都兼容
- 前端 `TaskForm` 敏感词输入加"代号"列（可选），tag 显示 `词 → 代号`；`client.ts` / `useTaskRunner.ts` / i18n 全部配套更新
- 文档：`docs/backend/privacy.md` 追加 `CustomWord` 结构与"→ 代号映射"小节

**i18n 模块拆分（顺手修 eslint）**
- `i18n/config.ts` 承载非组件导出：`locales` / `Language` / `LANGUAGE_OPTIONS` / `TranslationFn` / `LanguageContext` / `lookupTranslation`
- `i18n/context.tsx` 只导出 `LanguageProvider`（符合 `react-refresh/only-export-components`）
- `i18n/use-translation.ts` 独立 hook 文件
- `lookupTranslation` 内部对 dict 做 `Record<string, string | undefined>` 断言，消除 `no-unsafe-assignment` error

**vitest jsdom 环境**
- 新增 `vitest.config.ts`（`mergeConfig` + `environment: "jsdom"`），markdown rewrite 测试恢复通过

**顺手清理**
- `useFileUpload.ts` 3 处 `null` → `undefined`（unicorn/no-null）
- `DirectoryPicker.tsx` / `TaskDetail.tsx` `useCallback` 依赖补 `t`
- 后端 3 处无效 `# type: ignore` 注释移除（ngram_filter / preprocessor）

### 测试

- backend：312 passed, 6 skipped（排除 WS 集成预存在失败）
- frontend：vitest 5/5、eslint 0、tsc 0
- 视觉验证：Playwright 截图确认"张伟→化名A"tag 渲染正确、无 code 时回落默认占位符

## 2026-04-14 统一来源选择（本地/服务器）

### 完成内容

**后端扩展**
- `GET /api/v1/filesystem/dirs` 新增 `include_files: bool` 查询参数，支持同时返回目录和图片文件（白名单扩展名 + 附带 `size_bytes`），排序改为大小写不敏感
- 新增 `POST /api/v1/sources/server`：接收绝对路径列表，校验（存在、是文件、图片扩展名、≤5000 项），在 `tempfile.mkdtemp(prefix="docrestore_src_")` 创建符号链接，同名文件自动追加 `_1`/`_2` 后缀，返回 `image_dir` + `file_count`
- `DirEntry` schema 增加可选 `size_bytes`

**前端统一来源选择器**
- 新建 `frontend/src/components/SourcePicker.tsx`（Tab UI，本地/服务器切换）
  - 本地 Tab 复用现有 `<FileUploader>`
  - 服务器 Tab 支持目录浏览（parent/子目录导航 + 路径跳转）+ 单/多文件勾选
  - "使用当前目录" 直接返回路径；"使用已选文件" 调用 `/sources/server` stage 为 symlink 临时目录
  - 确认态显示已选路径，带 reset 按钮
- `TaskForm` 移除原 `mode` 切换（upload/path），统一调用 `<SourcePicker>` 获取 `image_dir`

**i18n 三语言同步**
- zh-CN / zh-TW / en 同步移除 `taskForm.uploadMode` 等旧 key，新增 `taskForm.sourceLabel` + `sourcePicker.*` 全套翻译

**测试**
- `tests/api/test_routes.py` 新增 7 个测试：
  - `TestFilesystemBrowse`（默认过滤文件 / `include_files=true` 同时返回图片+目录+size_bytes）
  - `TestStageServerSource`（symlink 创建 / 空 paths 400 / 非图片 400 / 相对路径 400 / 同名去重）

### 验证
- `mypy --strict` 通过（schemas.py / routes.py）
- `ruff check` 通过
- 前端 `tsc --noEmit` + ESLint 通过
- 后端 13 项 API 测试全通过
- Playwright 视觉验证：Tab 切换、服务器目录浏览、文件勾选、确认态显示均正常

### 遗留
- 无

---

## 2026-04-14 目录图片数量预览

### 完成内容

**后端**
- `DirEntry` schema 新增可选 `image_count: int | None`
- `_count_top_images()` 浅扫描（`os.scandir`），统计顶层图片数；扩展名匹配 `_IMAGE_EXTS`
- 计数上限 `_IMAGE_COUNT_CAP=9999`（防止超大目录拖慢响应），超过时返回上限值
- 仅在 `include_files=True` 时为目录条目计算（列表场景才需要预览）

**前端**
- `DirEntrySchema` 同步加 `image_count`
- `SourcePicker` 目录按钮内新增 `.server-entry-count` 徽章，`image_count > 0` 才渲染；达上限显示 `9999+`
- i18n 新增 `sourcePicker.imageCount`：zh-CN `{count} 张` / zh-TW `{count} 張` / en `{count} images`

**测试**
- 新增 `test_browse_dirs_image_count_preview`：校验 album=2（含子目录里的嵌套图片不计入）、empty=0

### 验证
- mypy --strict / ruff / ESLint / tsc --noEmit 全通过
- pytest：15/15 通过
- Playwright 视觉验证：`screenshots/ — 61 张`、`test_images/ — 11 张` 徽章正确渲染

---

## 2026-04-13 前端国际化（i18n）+ 子进程孤儿清理

### 完成内容

**i18n 国际化系统**
- 新建 `frontend/src/i18n/` 模块：zh-CN（源）、zh-TW、en 三语言支持
- 类型安全：`TranslationKey` 从 zh-CN 推导，其他 locale 用 `Record<TranslationKey, string>` 编译期校验
- `Language` 类型从 `keyof typeof locales` 自动推导，新增语言无需硬编码
- `LanguageProvider` + `useTranslation()` hook，`localStorage` 持久化偏好
- 支持模板插值：`t("key", { param: value })`

**组件翻译完成**
- 所有 13 个组件的用户可见字符串已替换为 `t()` 调用
- 涉及文件：App.tsx, Sidebar.tsx, TaskForm.tsx, TaskDetail.tsx, TaskResult.tsx, TaskProgress.tsx, SidebarTaskList.tsx, FileUploader.tsx, DirectoryPicker.tsx, UploadPreviewPanel.tsx, ConfirmDialog.tsx, SourceImagePanel.tsx, TokenSettings.tsx
- 侧边栏新增语言选择下拉框（`.language-select` CSS）
- 模块级常量重构：`TaskProgress` 的 `STAGE_LABELS`、`SidebarTaskList` 的 `STATUS_LABEL` 等改为组件内 `t()` 动态查找

**子进程孤儿清理**
- `engine_manager.py`、`deepseek_ocr2.py`：`start_new_session=True` + `os.killpg()` 杀整个进程组
- 防止 vLLM EngineCore 子进程泄漏占用 GPU 显存

### 验证
- `tsc --noEmit` 通过
- 三种语言切换截图验证通过（简体中文 / 繁體中文 / English）

---

## 2026-04-13 OCR 引擎按需切换 + 统一子进程架构

### 完成内容

**EngineManager 生命周期管理器**（新建 `ocr/engine_manager.py`）
- 按需切换 OCR 引擎（PaddleOCR ↔ DeepSeek-OCR-2），同一时刻只有一个引擎占用 GPU
- `ensure(ocr_override)` 接口：解析目标模型 → switch_lock 防并发 → gpu_lock 等当前操作完成 → shutdown 旧引擎 → 创建新引擎
- ppocr-server 自动管理：PaddleOCR 需要时自动启动 genai_server 子进程，切走时自动关闭
- TCP 端口健康检查（2s 轮询，可配置超时）
- 优雅关闭：SIGTERM → wait(10s) → SIGKILL

**DeepSeek-OCR-2 子进程化**
- 新建 `scripts/deepseek_ocr_worker.py`：独立 worker 进程，JSON Lines 协议（与 PaddleOCR worker 对称）
- 重构 `ocr/deepseek_ocr2.py`：从进程内 vLLM 引擎改为子进程客户端，移除所有 vLLM/torch 依赖
- 协议失步恢复（`_desync` 标志）：取消操作后自动重启 worker 恢复同步

**Pipeline 集成**
- `pipeline.py`：新增 `set_engine_manager()` + `ocr_override` 参数透传
- `task_manager.py`：`run_task()` 传递 `task.ocr_override`（从前端选择的引擎传到实际 OCR 调用）
- `app.py`：lifespan 创建 EngineManager 注入 Pipeline，引擎延迟初始化（首次任务时按需创建）
- `config.py`：OCRConfig 新增 `deepseek_python`/`deepseek_ocr_timeout`/`paddle_server_*` 字段
- 自动检测 conda 环境 python 路径（ppocr_client/ppocr_vlm/deepseek_ocr）

**架构变化**
- 后端成为轻量协调器，不直接依赖 torch/vllm
- 两个 OCR 引擎统一为子进程 worker 架构，通过 JSON Lines IPC 通信
- 前端已有的 OCR 引擎选择下拉框现在真正生效

### 验证
- mypy --strict 通过（6 个文件）
- ruff check 通过（7 个文件）

### 遗留
- 集成测试需要实际 GPU 环境验证
- 单元测试待补充（EngineManager 切换逻辑 mock 测试）

## 2026-04-12 AGE-30 认证鉴权与错误信息脱敏

### 完成内容

**后端认证**
- 新增 `api/auth.py`：静态 Bearer Token 认证模块
  - `configure_auth()` 应用启动时设置全局 token（env `DOCRESTORE_API_TOKEN`）
  - `require_auth()` HTTP 路由依赖：优先 `Authorization: Bearer` header，备选 `?token=` query param
  - `require_auth_ws()` WebSocket 专用依赖：仅支持 `?token=` query param
  - `hmac.compare_digest` 防时序攻击
  - 未配置 token 时完全放行（向后兼容开发模式）
- `app.py`：lifespan 调用 `configure_auth()`，router 挂载 `dependencies=[Depends(require_auth)]`
- `routes.py`：WS 端点添加 `Depends(require_auth_ws)`

**错误信息脱敏**
- `task_manager.py`：`task.error` 改存 `"{ExcType}: {str(exc)[:200]}"` 摘要
- debug 模式下将完整 traceback 写入 `output_dir/debug/error.txt`
- 服务端日志保持 `logger.exception()` 完整记录

**前端 Token 配置**
- 新增 `api/auth.ts`：localStorage 管理（load/save/clear）+ `getAuthHeaders()` + `appendTokenToUrl()`
- `client.ts`：所有 fetch 调用注入 auth header，URL 生成器附加 `?token=`
- 新增 `TokenSettings.tsx`：Token 配置弹窗（遮蔽显示、保存、清除）
- `Sidebar.tsx`：底部 footer 增加 "API Token" 按钮
- `App.tsx`：渲染 TokenSettings 弹窗
- `App.css`：弹窗 + Token 按钮样式

### 测试
- 新增 `tests/api/test_auth.py`：8 个测试全部通过
  - TestAuthEnabled：无 token/错误 token → 401，正确 Bearer/query param → 200，401 结构化错误体
  - TestAuthDisabled：未配置 token → 完全放行
  - TestErrorSanitization：摘要格式验证、超长消息截断
- mypy --strict 通过（auth.py / app.py / routes.py / task_manager.py）
- tsc --noEmit 通过
- 视觉验证通过（Playwright 截图确认 Token 弹窗正常渲染）

## 2026-04-09 AGE-27/28/29 任务管理 + 文件上传

### 完成内容

**AGE-29：任务历史持久化**
- 新增 `persistence/database.py`：SQLite + aiosqlite，tasks/task_results 两表，WAL 模式
- TaskManager 混合存储：内存（运行中）+ DB（历史），状态变更同步写 DB
- `GET /api/v1/tasks` 列表接口，支持状态筛选和分页
- 前端 TaskHistory 组件：表格展示、状态筛选、翻页、展开详情
- 服务重启自动标记中断任务为 failed

**AGE-28：任务取消/删除/重试**
- cancel：`asyncio.Task.cancel()` + CancelledError 处理
- delete：清理 output_dir + DB 记录，运行中任务需先取消
- retry：用原任务参数创建新任务
- 前端 ConfirmDialog 二次确认，TaskHistory 行内操作按钮

**AGE-27：浏览器文件上传**
- 三步上传流程：`POST /uploads` → `POST /uploads/{sid}/files` → `POST /uploads/{sid}/complete`
- 流式写入（64KB chunks）、文件大小限制、扩展名校验
- 后台会话清理（30 分钟间隔，1 小时 TTL）
- 前端 useFileUpload hook + FileUploader 组件 + TaskForm 双模式（上传/路径输入）

### 测试
- 9 个 DB 测试 + 6 个列表测试 + 9 个操作测试 + 9 个上传测试 = 44 全部通过
- 前端 tsc + eslint + build 通过

### 新增依赖
- `aiosqlite>=0.20`（后端）
- `python-multipart>=0.0.9`（后端）

## 2026-03-28 PaddleOCR 安装部署 + Server 模式集成

主题：新增 PaddleOCR 安装/启动脚本，worker 支持 server 模式，侧栏过滤修复。

完成内容：
- 新增 `scripts/setup_paddleocr.sh`：conda 环境安装脚本（server ppocr_vlm + client ppocr_client），幂等设计
- `scripts/start.sh`：新增 `ppocr-server` 启动模式，支持 `PPOCR_GPU_ID`/`PPOCR_PORT` 配置
- `config.py`：OCRConfig 新增 `paddle_server_url` / `paddle_server_model_name` 字段
- `paddle_ocr_worker.py`：`handle_initialize` 支持 server 模式参数（vl_rec_backend/server_url）
- `paddle_ocr.py`：`initialize()` 传递 server 配置给 worker + 侧栏检测 debug 日志
- `run_e2e.py`：新增 `--paddle-server-url` CLI 参数，自动检测 conda python 路径
- `column_filter.py`：修复跨全宽元素导致左栏验证失败的 bug + 3 个新测试用例
- `docs/deployment.md`：新增 PaddleOCR server 部署章节

遗留问题：
- `PaddleOCRVL` 的 server 模式构造参数名（vl_rec_backend/vl_rec_server_url/vl_rec_api_model_name）基于 CLI 实践推测，需实际验证
- 已有 OCR 输出需清理后重跑才能验证侧栏过滤修复效果

## 2026-03-28 PII 脱敏默认开启

主题：将 PII 脱敏从默认关闭调整为默认开启，提升隐私保护基线。

完成内容：
- `src/docrestore/pipeline/config.py`：`PIIConfig.enable` 默认值由 `False` 改为 `True`
- 注释同步更新为“默认开启，优先保护隐私”
- 运行集成测试：`pytest --tb=short tests/pipeline/test_pii_integration.py`，结果 3 passed

遗留问题：
- 测试过程中存在 1 条 warning（`AsyncMock` 未 awaited），不影响当前功能正确性，后续可单独清理。

## 2026-03-28 文档结构重组

主题：按前后端分离重新组织 docs 目录结构

完成内容：
- 创建新的文档索引：`docs/README.md`
- 系统架构文档：`docs/architecture.md`
- 后端文档目录：`docs/backend/`（包含 data-models/ocr/processing/llm/privacy/pipeline/api）
- 前端文档目录：`docs/frontend/`（包含 tech-stack/features）
- 部署指南：`docs/deployment.md`
- 参考文档：`docs/backend/references/`（deepseek-ocr2/streaming-pipeline）
- 移除旧结构：`docs/modules/`、`docs/design.md`、`docs/module-design.md`
- 更新 `CLAUDE.md` 和 memory 中的文档路径引用

新结构优势：
- 前后端文档清晰分离
- 模块文档按职责归类
- 入口文档提供清晰导航
- 参考文档独立存放

遗留问题：无

## 2026-03-27 修复 PaddleOCR worker 事件循环关闭异常

主题：修复 run_e2e.py 完成后 asyncio 事件循环关闭时的 RuntimeError 异常

完成内容：
- `paddle_ocr.py`：`shutdown()` 中显式关闭 stdin 并等待关闭完成（`stdin.close()` + `wait_closed()`）
- 根因：`asyncio.run()` 退出时关闭事件循环，但子进程传输对象在 `__del__` 析构时尝试清理管道，此时循环已关闭
- 解决方案：在事件循环关闭前显式清理，避免依赖 `__del__`

遗留问题：无

## 2026-03-27 文档聚类功能改进规划

主题：记录文档聚类功能的改进需求和方案

完成内容：
- 创建 `docs/issues/AGE-34-improve-doc-clustering.md`
- 问题：LLM 文档边界检测在某些场景下无法准确识别多篇文档
- 改进方向：优化提示词 / 混合策略（子目录优先 + LLM 辅助）/ Pipeline 内置子目录扫描
- 优先级：低（当前手动分目录可满足需求）

遗留问题：无

## 2026-03-25 OCR 引擎可插拔架构

主题：将 OCR 模块从 DeepSeek-OCR-2 硬绑定改为可配置的引擎插件架构，新增 PaddleOCR 支持。

完成内容：
1. **新增 PaddleOCREngine**：通过 subprocess 调用独立 venv 中的 PaddleOCR，实现环境隔离（避免与 DeepSeek-OCR-2 的依赖冲突）
2. **Worker 脚本**：`scripts/paddle_ocr_worker.py`，JSON Lines 协议通信（stdin/stdout）
3. **配置扩展**：`OCRConfig` 新增 `paddle_venv_python` 和 `paddle_ocr_timeout` 字段
4. **引擎工厂**：`app.py` 的 `_create_ocr_engine()` 支持 `engine="paddle-ocr"`
5. **输出适配**：PaddleOCR 的 `imgs/` 重命名为 `images/`，图片重命名为 `0.jpg, 1.jpg`，markdown 引用同步更新

涉及文件：
- `src/docrestore/ocr/paddle_ocr.py`（新增）
- `scripts/paddle_ocr_worker.py`（新增）
- `src/docrestore/pipeline/config.py`
- `src/docrestore/api/app.py`
- `tests/ocr/test_paddle_ocr.py`（新增）
- `docs/modules/ocr.md`

设计文档：`.claude/plans/ocr-pluggable.md`

使用方式：
```python
config = PipelineConfig(
    ocr=OCRConfig(
        engine="paddle-ocr",
        paddle_venv_python="/path/to/ppocr/.venv/bin/python",
    )
)
```

## 2026-03-25 修复 e2e 三大问题

主题：修复 100+ 张图片 e2e 运行中发现的三个严重 bug。

完成内容：
1. **图片丢失**（Renderer 路径 bug）：多文档渲染时 OCR 子目录在根目录而非子目录。`Renderer.render()` 新增 `ocr_root_dir` 参数，Pipeline 传入根 output_dir。
2. **页面排序混乱**（dedup 策略重构）：废弃"先纯文本合并再回插 marker"的方案，改为"先 prepend page marker 再 merge"。删除 `_find_page_start()` 方法，page marker 行因含唯一文件名不会被误判为重叠。
3. **OCR KV cache 膨胀**：`DeepSeekOCR2Engine` 新增 `reset_cache()` 调用 `vLLM reset_prefix_cache()`，Pipeline 每 30 张图片自动清理一次（`OCRConfig.cache_reset_interval`）。

涉及文件：
- `src/docrestore/output/renderer.py`
- `src/docrestore/processing/dedup.py`
- `src/docrestore/pipeline/pipeline.py`
- `src/docrestore/pipeline/config.py`
- `src/docrestore/ocr/deepseek_ocr2.py`

遗留问题：
- 需重跑 `scripts/run_e2e.py` 端到端验证（需 GPU 环境）

## 2026-03-25 流式并行 Pipeline 设计

主题：将串行 Pipeline 改为 OCR↔LLM 流式并行执行，OCR 边产出，下游边消费，减少总耗时。

设计文档：`docs/modules/streaming_pipeline.md`

设计决策：
- LLM 段间串行（DOC_BOUNDARY 需有序检测）
- 终结化期间继续消费 OCR 队列
- PII：Regex 先行 + 延迟实体检测（5 页后）
- 进度模型单通道不变

核心组件：
- `IncrementalMerger`（增量合并，复用 merge_two_pages）
- `StreamSegmentExtractor`（流式分段提取）
- `DocumentState`（单文档累积状态）
- Pipeline 重构：`_ocr_producer` + `_stream_process` + `_finalize_document`

状态：设计完成，待实施。

## 2026-03-25 LLM 文档聚类（LLM-based Document Clustering）

主题：利用 LLM 精修阶段检测多文档边界，自动拆分并独立输出，替代已移除的启发式聚类方案。

设计文档：`docs/modules/llm_doc_clustering.md`

完成内容：
- `models.py`：新增 `DocBoundary(frozen=True)` dataclass，`PipelineResult` 新增 `doc_title`/`doc_dir` 字段
- `llm/prompts.py`：`REFINE_SYSTEM_PROMPT` 增加规则 10（DOC_BOUNDARY 检测），新增 `_DOC_BOUNDARY_PATTERN` 正则、`parse_doc_boundaries()`（JSON 容错解析）、`extract_first_heading()`
- `utils/paths.py`（新建）：`sanitize_dirname()`（路径穿越防护+截断+折叠）、`dedupe_dirnames()`（重名追加后缀）
- `pipeline/pipeline.py`：
  - 新增 `process_many()` 返回 `list[PipelineResult]`（OCR→merge→PII→refine→reassemble→拆分→每篇独立 gap fill/final refine/render）
  - `process()` 改为兼容包装（调用 `process_many()[0]`）
  - 新增 `_split_by_doc_boundaries()`（解析边界+切分 markdown+分配 pages/images）
  - 新增 `_resolve_split_points()`、`_build_sub_docs()`（拆分降低复杂度）
  - 新增 `_resolve_sub_output_dir()`（单文档=根目录，多文档=子目录）
- `pipeline/task_manager.py`：`Task.result` 改为 `Task.results: list[PipelineResult]`，保留 `result` 属性兼容；`run_task()` 调用 `process_many()`
- `api/schemas.py`：`TaskResultResponse` 新增 `doc_title`/`doc_dir`，新增 `TaskResultsResponse`
- `api/routes.py`：新增 `GET /results` 端点，`_validate_asset_path` 扩展支持子目录，`_build_result_zip_bytes` 支持多子目录打包
- 测试：30 个新测试（sanitize/dedupe/parse_doc_boundaries/extract_first_heading/split 拆分逻辑/图片过滤），全量 236 passed

遗留问题：
- 前端尚未适配多文档 Tab 展示（步骤6，后续迭代）

## 2026-03-25 文档同步：全量对齐代码实现

主题：docs/ 目录文档与当前代码实现全量同步，修正聚类移除后的过时描述并补齐新 feature 文档。

完成内容：
- `docs/modules/models.md`：移除 `DocumentCluster`/`ClusterConfig`，新增 `RedactionRecord`/`PIIConfig`，更新 `Gap`（filled/filled_content）、`RefinedResult`（truncated）、`PipelineResult`（warnings/redaction_records，移除 cluster_title）、`LLMConfig`（provider/enable_final_refine/enable_gap_fill）、`TaskProgress`（stage 补充 pii_redaction/gap_fill/final_refine），更新依赖表
- `docs/modules/pipeline.md`：整体重写，移除聚类/父子任务/pipeline_semaphore，`process()` 返回单 `PipelineResult`，新增 PII 脱敏/缺口自动补充/整篇精修/截断检测/debug 中间产物阶段描述
- `docs/modules/llm.md`：整体重写，移除 overlap-start/end 标记，新增 `_BaseLLMRefiner`/`LocalLLMRefiner`/`fill_gap()`/`final_refine()`/`detect_pii_entities()`/截断检测，更新 prompt 章节
- `docs/modules/api.md`：整体重写，移除 `SubTaskSummary`/`ParentTaskResultResponse`/父子任务字段，简化为单任务模型
- `docs/modules/processing.md`：移除所有 `<!-- overlap-start/end -->` 引用
- `docs/modules/scheduler.md`：标注 `pipeline_semaphore` 为预留接口（当前未接入），更新并发模型描述
- `docs/module-design.md`：整体重写，更新模块结构（移除 clustering，新增 privacy/、llm/local.py、column_filter.py），更新数据流和依赖树
- `docs/design.md`：整体重写，更新架构图/数据流/数据对象/LLM 层/配置/目录结构/版本范围，移除聚类，补齐 AGE-7/14/17/26

未变更（保留）：
- `docs/modules/clustering.md`：保留不删除（可能还有需求）

遗留问题：无

## 2026-03-25 AGE-14：本地 LLM 精修支持（LocalLLMRefiner）

主题：支持本地 OpenAI 兼容服务（ollama/vllm/llama.cpp）进行 LLM 精修，适用于隐私敏感或离线场景。

完成内容：
- `llm/cloud.py`：提取 `_BaseLLMRefiner` 基类（`__init__`/`refine`/`fill_gap`/`final_refine` + `_build_kwargs` 消除 4 处重复 kwargs 构造），`CloudLLMRefiner` 继承并保留 `detect_pii_entities()`
- `llm/local.py`：新建 `LocalLLMRefiner(_BaseLLMRefiner)`，纯继承无额外方法
- `pipeline/config.py`：`LLMConfig` 新增 `provider: str = "cloud"`（`"cloud"` | `"local"`）
- `pipeline/pipeline.py`：新增 `_create_refiner()` 静态方法，替换 3 处硬编码 `CloudLLMRefiner(...)`，PII `isinstance` 检查自然兼容
- `tests/llm/test_local.py`：8 个单元测试（实例化/refine/fill_gap/final_refine/无 detect_pii_entities）
- `tests/pipeline/test_local_refiner_integration.py`：6 个集成测试（_create_refiner/initialize/PII 兼容/精修流程）

验证：mypy --strict ✅ ruff ✅ 全量 pytest 206 passed ✅

## 2026-03-24 AGE-17：隐私内容脱敏（PII Redaction）

主题：在文档发送到云端 LLM 之前完成 PII 脱敏，防止敏感信息外泄。

完成内容：
- `config.py`：新增 `PIIConfig` dataclass（enable/各类型开关/占位符/block_cloud_on_detect_failure），`PipelineConfig.pii` 字段
- `models.py`：新增 `RedactionRecord` dataclass，`PipelineResult.redaction_records` 字段
- `privacy/patterns.py`：结构化 PII 正则检测（手机号/邮箱/身份证/银行卡），处理顺序身份证→邮箱→手机→银行卡，银行卡 Luhn 校验
- `privacy/redactor.py`：`PIIRedactor`（regex + LLM 实体检测）+ `EntityLexicon`（复用词典）+ `redact_for_cloud()`/`redact_snippet()`
- `llm/prompts.py`：新增 `PII_DETECT_SYSTEM_PROMPT` + `build_pii_detect_prompt()`
- `llm/cloud.py`：新增 `detect_pii_entities()` 方法（JSON 解析 + 错误处理）
- `pipeline/pipeline.py`：集成脱敏阶段（merge 后 refine 前），gap fill re-OCR 文本也脱敏，检测失败+block=True 时跳过所有云端 LLM
- 测试：29 个新测试（test_patterns/test_redactor/test_pii_detect_prompt/test_pii_integration），全量 192 通过

遗留问题：无

## 2026-03-24 AGE-7：缺口自动补充（Gap Auto-fill via Re-OCR）

主题：LLM 精修检测到内容跳跃（gap）时，自动对相邻页原图 re-OCR，用 LLM 提取缺失内容并插回文档。

完成内容：
- `models.py`：`Gap` 新增 `filled: bool = False` 和 `filled_content: str = ""`
- `config.py`：`LLMConfig` 新增 `enable_gap_fill: bool = True` 开关
- `deepseek_ocr2.py`：新增 `reocr_page(image_path)` 公共方法（不写文件、不做侧栏过滤）
- `prompts.py`：新增 `GAP_FILL_SYSTEM_PROMPT`/`GAP_FILL_USER_TEMPLATE`/`GAP_FILL_EMPTY_MARKER` + `build_gap_fill_prompt()`
- `cloud.py`：新增 `fill_gap()` 方法（litellm 调用 + "无法补充"标记检测）
- `pipeline.py`：重构 `process()` 降低复杂度，新增 `_maybe_fill_gaps()` / `_fill_gaps()` / `_fill_one_gap()` / `_reocr_cached()` / `_insert_gap_content()` / `_get_refiner()` / `_do_final_refine()` / `_collect_warnings()`
  - Gap fill 在 reassemble 之后、final_refine 之前执行
  - Re-OCR 缓存避免重复调用，GPU 锁保护
  - 单个 gap 失败不影响其他 gap，降级为 warning
  - 未填充的 gap 生成 warning 到 PipelineResult.warnings
- 新增测试：`tests/llm/test_gap_fill_prompt.py`（7 用例）、`tests/pipeline/test_gap_fill.py`（8 用例）
- 全量回归 163 passed

遗留问题：无

## 2026-03-24 AGE-26：LLM 精修截断检测机制

主题：LLM 精修 segment 时输出可能因 token 上限被截断，新增自动检测 + 结构化警告。

完成内容：
- `models.py`：`RefinedResult` 新增 `truncated: bool = False`；`PipelineResult` 新增 `warnings: list[str]`
- `cloud.py`：`refine()` 和 `final_refine()` 检查 `finish_reason == "length"` 设置截断标记
- `pipeline.py`：`_refine_segments()` 循环中增加行数比例启发式检测（输出比输入少 >30% 行且输入 >20 行）；`_final_refine()` 返回截断标记；`process()` 聚合 warnings 到 PipelineResult
- 新增测试：`tests/llm/test_cloud_truncation.py`（4 用例）、`tests/pipeline/test_truncation.py`（7 用例）
- 全量回归 148 passed

遗留问题：无

## 2026-03-23 移除自动聚类，改为手动子目录分类

主题：自动聚类效果不佳，改为用户手动按子目录分类文档照片。Pipeline 只处理单个平面目录，外部遍历子目录逐个调用。

完成内容：
- 删除 `src/docrestore/processing/clustering.py` 和 `tests/processing/test_clustering.py`
- `config.py`：删除 `ClusterConfig`，`PipelineConfig` 去掉 `cluster` 字段
- `models.py`：删除 `DocumentCluster`，`PipelineResult` 去掉 `cluster_title`
- `pipeline.py`：重写 `process()` 返回单个 `PipelineResult`（非 list），移除聚类逻辑、`_process_cluster`、`_safe_dirname`、`_link_ocr_dirs`，提取 `_ocr_and_clean` / `_refine_segments` / `_refine_one_segment` 降低复杂度
- `task_manager.py`：移除子任务机制（`_create_sub_task`、`sub_task_ids`/`parent_task_id`/`cluster_title`），`run_task` 直接赋 result
- `schemas.py`：删除 `SubTaskSummary`、`ParentTaskResultResponse`，`TaskResponse` 去掉子任务字段
- `routes.py`：简化 `get_result()` 和 `download()` 去掉多组逻辑
- `run_e2e.py`：支持子目录遍历，每个子目录调 `process()`
- 前端：`schemas.ts`/`client.ts`/`useTaskRunner.ts`/`TaskResult.tsx`/`App.tsx` 去掉子任务/多组相关代码
- 测试：更新 `test_pipeline.py`/`test_routes.py`/`test_config.py`/`test_models.py`

验证：126 测试全通过，前端 TypeScript 编译通过

## 2026-03-23 聚类算法改造：标题相似度 → 页间内容重叠（已废弃）

主题：聚类边界判断从标题相似度改为页间内容重叠，解决 OCR 不区分文档标题和章节标题导致同一文档被拆分的问题

完成内容：
- `config.py`：`ClusterConfig` 移除 `similarity_threshold`，新增 `overlap_search_ratio=0.5`、`min_overlap_lines=2`
- `clustering.py`：重写聚类逻辑
  - `extract_title()` 恢复为 H1-H6 + 首行 fallback（仅用于组命名）
  - 新增 `_has_content_overlap()` 检测页间重叠（取前页尾部/当页头部，SequenceMatcher.find_longest_match）
  - `feed()` 改为与 `_current_pages[-1]` 做重叠对比（非 `_prev_page`，避免 buffer 内互相重叠导致错误回收）
- `test_clustering.py`：重写 19 个测试用例
  - 新增 `_make_overlapping_pages()` 构造重叠文本序列
  - 核心回归用例：H2 章节标题变化但有内容重叠 → 不拆分
- `test_config.py`：修复 `similarity_threshold` → `overlap_search_ratio` 引用
- 验证：19 聚类测试全通过，全量回归 149/150 通过（1 个预存失败与本次无关）

遗留问题：
- `test_result_delivery.py` 有 1 个预存失败（zip 路径含聚类标题子目录，与本次改动无关）
- 待手动端到端验证（`test_images/Linux_SDK/linux_apt_user/` 12 张照片应归为一组）

## 2026-03-22 侧栏过滤：混合策略（grounding 过滤 + 裁剪重跑 OCR）

主题：检测并过滤文档照片中的侧栏（导航目录、大纲 TOC），避免侧栏内容混入正文干扰聚类和去重

完成内容：
- 新建 `src/docrestore/ocr/column_filter.py`：
  - `ColumnFilter` 类：侧栏检测（grounding 坐标分析）+ 过滤 + 文本重建
  - 左栏检测：x1 < 100 且 x2 <= 220，>= 5 个密集区域
  - 右栏检测：x1 >= 800 且 width < 200，>= 5 个密集区域
  - 混合策略：正文占比正常 → grounding 过滤重建；异常 → 裁剪图片重跑 OCR
- 修改 `src/docrestore/ocr/deepseek_ocr2.py`：
  - `ocr()` 在推理后、grounding 解析前插入侧栏过滤
  - 新增 `_apply_column_filter()` 和 `_reocr()` 内部方法
- 修改 `src/docrestore/pipeline/config.py`：
  - `OCRConfig` 新增 `enable_column_filter: bool = True` 和 `column_filter_min_sidebar: int = 5`
- 新建 `tests/ocr/test_column_filter.py`：29 个测试用例全部通过
- 更新 `docs/modules/ocr.md`：新增侧栏过滤章节

遗留问题：
- `tests/api/test_result_delivery.py` 有 1 个预存失败（与侧栏过滤无关）
- 手动端到端验证待 GPU 环境执行

## 2026-03-22 AGE-18/AGE-16: 流式聚类 + 任务队列实现

主题：OCR 边产出边聚类，检测到完整文档组后立即启动下游 pipeline，GPU 串行、下游并发

完成内容：
- 数据模型：
  - `models.py` 新增 `DocumentCluster(title, pages)`，`PipelineResult` 新增 `cluster_title`
  - `config.py` 新增 `ClusterConfig`（similarity_threshold=0.6, lag_window=3）和 `QueueConfig`（max_concurrent_pipelines=3）
  - `PipelineConfig` 新增 `cluster` 和 `queue` 子配置
- 增量聚类：
  - 新建 `processing/clustering.py`：`IncrementalClusterer`（标题提取 + difflib 相似度 + 滞后窗口）
  - 逐页 feed，连续 N 页标题不匹配触发文档边界，emit 完整组
- Pipeline 重构：
  - `pipeline.py` 的 `process()` 从批量 OCR 改为逐张 OCR → clean → feed → 检测到组后 `asyncio.create_task(_process_cluster())`
  - `_process_cluster()` 对单组执行 dedup → refine → render（受 Semaphore 限流）
  - 返回值从 `PipelineResult` 改为 `list[PipelineResult]`
  - 新增 `gpu_lock` 和 `pipeline_semaphore` 参数
- 全局调度器：
  - 新建 `pipeline/scheduler.py`：`PipelineScheduler`（gpu_lock + pipeline_semaphore）
  - `app.py` lifespan 创建 Scheduler 单例并注入 TaskManager
- TaskManager 子任务：
  - `Task` 新增 `parent_task_id`、`sub_task_ids`、`cluster_title`
  - `run_task()` 为每个 PipelineResult 创建子任务记录
  - 单组退化：父任务 result 直接赋值；多组：父 result=None，通过子任务查看
- API 层：
  - `schemas.py` 新增 `SubTaskSummary`、`ParentTaskResultResponse`，`TaskResponse` 新增 sub_task_ids/cluster_title/parent_task_id
  - `routes.py`：`get_task()` 返回父子任务信息，`get_result()` 多组时返回 ParentTaskResultResponse，`download()` 支持多组 zip 打包
- 前端：
  - `schemas.ts` 新增 SubTaskSummary、ParentTaskResultResponse schema
  - `client.ts` 新增 `getParentTaskResult()`
  - `useTaskRunner.ts` 新增 `taskResult` 状态（single/multi），完成后自动拉取多组结果
  - `TaskResult.tsx` 多组模式展示子任务卡片列表（标题 + 状态 + 下载），点击切换预览
- 文档：
  - 新建 `docs/modules/clustering.md`、`docs/modules/scheduler.md`
  - 更新 `docs/module-design.md`（索引、调用链、依赖图、数据流）
  - 更新 `docs/design.md`（迭代范围、配置表、目录结构）

遗留问题：
- 前端尚未在真实后端环境做端到端手动验证

## 2026-03-22 AGE-12/AGE-13: 后端 WS + assets/download 提交 & 前端单页闭环实现

主题：完成 AGE-12 WebSocket 进度推送后端实现并提交，落地 AGE-13 前端工程全部代码

完成内容：
- 后端（commit `5d761c9`）：
  - `routes.py`：新增 WS `/tasks/{task_id}/progress`、GET assets（路径穿越防护）、GET download（zip 打包）
  - `task_manager.py`：新增 subscribe/unsubscribe/publish_progress 广播机制（Queue maxsize=1 背压）
  - `ws_progress.md`、`result_delivery.md`：模块详细设计文档
  - 测试 5 个全通过：WS 多客户端订阅、断线清理、assets 路径穿越防护、download zip 校验
- 前端脚手架（commit `cfec7fa`）：
  - Vite + React 19 + TypeScript strict + ESLint strict-type-checked + zod v4 + react-markdown
  - `frontend-design.md`、`modules/frontend.md`：前端技术规格与模块设计文档
- 前端实现（commit `2438a0b`）：
  - `api/schemas.ts`：zod schema 运行时校验（与后端 schemas.py 对齐）
  - `api/client.ts`：fetch 客户端 + WS/assets/download URL 构建
  - `features/task/useTaskRunner.ts`：核心 hook（任务创建、WS 实时进度、轮询降级、结果拉取）
  - `features/task/markdown.ts`：图片 URL 重写（images/... → assets 接口路径）
  - `components/TaskForm.tsx`：输入表单（image_dir + output_dir）
  - `components/TaskProgress.tsx`：进度条 + 阶段展示 + WS/轮询状态
  - `components/TaskResult.tsx`：react-markdown 预览 + 下载按钮
  - `App.tsx`：单页闭环整合（idle → processing → completed/failed）
  - `vite.config.ts`：开发代理 /api → 127.0.0.1:8000（含 WS）
  - `scripts/start.sh`：前后端统一启动脚本（支持 all/backend/frontend）
  - 清理模板遗留文件（hero.png、react.svg、vite.svg、postcss.config.js）
- 验证：
  - 前端：TSC strict 通过、ESLint 0 error、vitest 5 passed、Vite build 成功
  - 后端：pytest 89 passed, 3 skipped（GPU OCR 测试，与本次无关）
- 已推送至远程 dev 分支

遗留问题：
- 前端尚未在真实后端环境做端到端手动验证（需 GPU 环境启动后端）
- 前端 Playwright e2e 测试未写（设计文档标注为可选）

## 2026-03-21 AGE-12/AGE-13: WS 多订阅与清理测试加固 + 开始前端开发准备

主题：补齐 WebSocket 进度推送的多订阅（B）与断线清理（C）测试证据，并准备进入前端开发

完成内容：
- `tests/api/test_ws_progress.py`：
  - B：同一 task 两个 WS 客户端都能收到后续进度消息
  - C：WS 断开后 `TaskManager` 订阅者集合能清理归零（subscriber_count 断言）
- `TaskManager` 增加 `subscriber_count()`（测试/诊断用）用于断言资源清理
- 全量测试验证：`pytest --tb=short` 通过（92 passed）

遗留问题：
- 进入 AGE-13 前端工程落地（Vite + React + TS strict，按 `docs/modules/frontend.md`）


完成内容：
- `docs/frontend-design.md`：新增“需求追踪（Linear）”小节（AGE-12/AGE-13 链接 + 优先级/状态）
- Linear：创建后续迭代 issues 用于跟踪 MVP 不做项：
  - AGE-27：浏览器端目录选择与上传接口
  - AGE-28：任务取消/删除/重试能力（API + 前端）
  - AGE-29：任务历史持久化与任务列表接口
  - AGE-30：认证鉴权与错误信息脱敏
  - AGE-31：SSE 进度推送（WebSocket 备选）
- 新增模块详细设计文档：
  - `docs/modules/ws_progress.md`（AGE-12：WebSocket 进度推送）
  - `docs/modules/result_delivery.md`（AGE-13：assets + download zip）
  - `docs/modules/frontend.md`（AGE-13：前端工程/状态/预览与下载对接）
- `docs/module-design.md`：模块索引新增 ws_progress/result_delivery/frontend 三份文档

遗留问题：
- 仍需实现后端 WS、assets、download 与前端工程代码（本文档已给出可落地接口与测试建议）

## 2026-03-21 AGE-12/AGE-13: 前端技术规格文档（Draft）

主题：前端开发前置设计，产出可落地的前端技术规格并同步索引

完成内容：
- 新增 `docs/frontend-design.md`：定义前端单页闭环 IA、状态模型、WS↔轮询降级策略
- 在文档中明确 AGE-13 的两类后端关键缺口（assets 受限访问 + download zip）及接口契约
- `docs/design.md`：在“第二版范围”补充 `docs/frontend-design.md` 索引
- `docs/modules/api.md`：在“MVP 不含”中补充该前端规格文档引用（待落地实现）

遗留问题：
- 需用户评审并确认该规格后，进入后端 WS/assets/download 与前端工程实现

## 2026-03-20 AGE-25: 删除产品侧 FixtureOCREngine + 保持无 GPU 测试

主题：移除产品代码中的测试 OCR 回退实现，但保留“无 GPU 也能跑端到端测试”的能力

完成内容：
- 删除 `src/docrestore/ocr/mock.py`（FixtureOCREngine 不再属于产品代码）
- `app.py`：移除 DeepSeek-OCR-2 依赖缺失时回退逻辑；现在依赖缺失会直接抛出 ImportError（启动时尽早失败）
- 测试侧新增 `tests/support/ocr_engine.py`：提供测试专用 `FixtureOCREngine`，从 `output_dir/{stem}_OCR/` 读取 `result.mmd`/`images/` 来复现后续流程（clean/dedup/refine/render）
- `tests/pipeline/test_pipeline.py`、`tests/api/test_routes.py`：改为注入测试专用 `FixtureOCREngine`，不再依赖产品侧 mock
- 删除 `tests/ocr/test_mock.py`
- `docs/design.md`：目录结构移除 `src/docrestore/ocr/mock.py`，补充 tests/support 说明
- 验证：`pytest --tb=short` 通过（68 passed, 19 skipped）

遗留问题：
- Pyright 仍提示 tests 包相对导入无法解析（不影响 mypy/pytest/ruff，后续如需可单独处理）


主题：测试基础设施改造，去除硬编码依赖，支持自动 OCR 数据生成

完成内容：
- `tests/conftest.py`：新增 `_find_test_image_dir()`、`_find_test_images()`、`_get_test_stems()` 动态扫描函数，新增 `ocr_data_dir` session fixture（有 GPU 时自动跑 OCR）和 `require_ocr_data` fixture
- 6 个测试文件去除硬编码 `_STEMS`，改为从 conftest 导入 `TEST_IMAGE_DIR`/`TEST_STEMS`
- 去掉 class 级 `@pytest.mark.skipif`，改为 `@pytest.mark.usefixtures("require_ocr_data")` fixture 依赖（方案 A）
- `test_integration.py`/`test_pipeline.py`：LLM 配置从 `deepseek/deepseek-chat` + `DEEPSEEK_API_KEY` 改为 `openai/glm-5` + `GLM_API_KEY`
- `tests/` 及子目录新增 `__init__.py`（mypy 需要包识别）
- `test_cleaner.py`：`SAMPLE_OCR_DIR` 改为动态获取，`test_clean_with_sample_data` 通过 fixture 参数跳过
- `test_deepseek_engine.py`：`_TEST_IMAGE` 改为动态获取第一张图片
- 验证：87 passed，5 failed（均为已有问题：GPU 引擎测试、LLMConfig 默认值、API key 无效）

遗留问题：
- `test_config.py` 的 `max_chars_per_segment` 断言值需更新（18000 vs 6000）
- GPU 引擎测试在当前环境仍失败（与本次改造无关）

## 2026-03-19 AGE-11: design.md 同步 + 错误处理加固

主题：同步 design.md 与代码实现的 9 处不一致，修复 3 个关键错误处理缺口

完成内容：
- design.md 同步：overlap 标记残留删除、配置默认值修正（model_path/search_ratio/max_chars_per_segment）、补充缺失配置项（debug/max_tokens/min_crops/ngram_whitelist_token_ids/max_retries）、API llm 字段补充、编程接口示例更新（set_ocr_engine + llm_override）、目录结构补充 schemas.py/mock.py、依赖补充 aiofiles/httpx、debug 落盘说明、DELETE 路由标注后续迭代
- 错误处理修复：cloud.py choices 空列表防护（RuntimeError）、cleaner.py result.mmd 不存在明确报错（FileNotFoundError）、app.py lifespan initialize 失败时先 shutdown 再 raise
- 代码遗留问题全面扫描：无 TODO/FIXME/pass/NotImplementedError，代码干净
- 创建 Linear issue AGE-25 跟踪删除 FixtureOCREngine（GPU OCR 已稳定）

遗留问题：
- AGE-25：删除 ocr/mock.py 及 4 处引用，调整测试策略
- LLM 精修截断检测机制未实现

## 2026-03-19 API 请求级 LLM 配置 + ASYNC240 修复

主题：API 接口支持请求级 LLM 配置覆盖，修复 ASYNC240 lint 警告

完成内容：
- `schemas.py` 新增 `LLMConfigRequest`（model/api_base/api_key/max_chars_per_segment 均可选），`CreateTaskRequest` 加 `llm` 字段
- `routes.py` 创建任务时提取非 None 的 LLM 配置字段传给 TaskManager
- `task_manager.py` 的 `Task` 和 `create_task` 支持 `llm_override`，`run_task` 传给 Pipeline
- `pipeline.py` 的 `process()` 接收 `llm_override`，有值时合并默认 LLMConfig 创建临时 CloudLLMRefiner
- 修复 ASYNC240：`output_dir.mkdir()` 和 `image_dir.iterdir()` 改用 `asyncio.to_thread`
- README 更新 API 完整用法：请求参数表、LLM 覆盖示例、响应示例

遗留问题：
- LLM 精修截断检测机制未实现（当前仅靠缩小分段缓解）
- 需重新跑 pipeline 验证 6000 分段 + 新 prompt 的输出质量

## 2026-03-19 AGE-23: debug 中间结果落盘 + 去除 overlap 标记 + LLM prompt 优化

主题：保留各阶段中间结果用于调试，移除 overlap 标记机制，优化 LLM 精修 prompt

完成内容：
- `PipelineConfig` 新增 `debug: bool = True`，Pipeline.process() 各阶段落盘中间结果到 `output_dir/debug/`
- debug 输出：`{stem}_cleaned.md`、`merged_raw.md`、`segments/{i}_input.md`、`segments/{i}_output.md`、`reassembled.md`
- 通过 debug 产物定位到 LLM 精修截断问题：segment 0 输入 471 行，输出仅 344 行（部分页面内容丢失）
- 根因：`max_chars_per_segment=12000` 导致单段过长，LLM 输出 token 不够截断尾部
- 修复：`max_chars_per_segment` 从 12000 → 6000
- 移除 overlap 标记机制（`<!-- overlap-start/end -->`）：dedup 直接拼接去重文本，segmenter 去掉标记包裹，pipeline._reassemble 简化为直接拼接，renderer 移除标记清理
- LLM prompt 优化：明确 OCR 来源说明、强调代码必须格式化为代码块、标题层级正确、LLM 自行判断去重
- README 补充 LLM 模型配置说明（model/api_base/api_key 示例）

遗留问题：
- LLM 精修截断检测机制未实现（当前仅靠缩小分段缓解）
- 需重新跑 pipeline 验证 6000 分段 + 新 prompt 的输出质量

## 2026-03-19 MVP 验收通过 + API 启动修复

主题：修复 API 服务启动后无法调用真实 OCR 引擎的问题，MVP 验收通过

完成内容：
- `app.py`：`create_app()` 根据 `config.ocr.engine` 自动创建 `DeepSeekOCR2Engine`，不再默认 fallback 到 FixtureOCREngine
- `routes.py`：`create_task` 用 `asyncio.create_task()` 替代 `BackgroundTasks`，POST 立即返回，pipeline 真正后台执行
- `test_routes.py`：`test_create_and_get_result` 改为轮询等待任务完成
- `test_config.py`：修复 `model_path` 断言与 config.py 默认值一致（`models/DeepSeek-OCR-2`）
- 用户验证 MVP 全流程通过

遗留问题：无

## 2026-03-18 setup.sh 安装顺序优化 + 模型权重下载

主题：修复 setup.sh 重复安装/降级 transformers 问题，新增模型权重下载

完成内容：
- `setup.sh`：调整安装顺序（torch → vllm → pip install -e → 降级 transformers/tokenizers 作为最后一步），新增 huggingface-cli 下载模型权重
- `config.py`：`OCRConfig.model_path` 默认值改为本地路径 `models/DeepSeek-OCR-2`
- `.gitignore`：新增 `models/`

遗留问题：pip 依赖冲突警告（vllm 要求 transformers>=4.51 vs 实际 4.46.3）是预期行为，DeepSeek-OCR-2 README 明确说明可忽略

## 2026-03-18 编写项目 README

主题：新增 README.md

完成内容：
- 新增 `README.md`，覆盖：项目简介、环境要求、安装（setup.sh 参数与环境变量）、配置（.env + PipelineConfig）、使用方式（脚本 run_e2e.py + FastAPI REST API）、输出说明、开发测试命令、项目结构
- API key 使用占位符，未泄露真实密钥

遗留问题：无

## 2026-03-18 端到端完整验证通过

主题：26 张图片完整 pipeline 端到端验证

完成内容：
- 新增 `scripts/run_e2e.py`：完整 pipeline 运行脚本
- 26 张图片（示例数据集）→ OCR → 清洗 → 去重合并 → LLM 精修（2 段）→ 渲染输出
- 输出：`output/development_guide/document.md`，16050 字符，0 个 GAP
- 总耗时 382s（模型加载 72s + 处理 310s），NVIDIA A2 GPU
- OCR 吞吐：约 160-170 tokens/s prompt，60-170 tokens/s generation

遗留问题：
- 输出质量需人工审查（OCR 识别准确度、去重合并效果、LLM 精修质量）
- 单 GPU 串行处理，26 张图片约 5 分钟，可接受

## 2026-03-18 环境配置与安装脚本 + GPU 端到端验证

主题：创建独立虚拟环境、安装脚本、DeepSeek-OCR-2 vendor 集成、GPU 端到端验证

完成内容：
- 新增 `scripts/setup.sh`：按官方 README 顺序安装（torch cu118 → vllm whl → 降级 transformers/tokenizers → flash-attn → pip install -e ".[dev,ocr]" → vendor clone）
- `pyproject.toml`：ocr 组仅声明纯 Python 依赖（einops/easydict/addict/numpy），torch/vllm/transformers 由 setup.sh 管理（避免版本冲突）
- `deepseek_ocr2.py`：新增 `_find_project_root()` + `_inject_vendor_path()`，initialize() 先注入 vendor 路径再 import
- `.gitignore`：.venv/ + vendor/ + .cache/ + Python 缓存
- `preprocessor.py`：`load_image` 参数类型 Any → str | Path
- `test_deepseek_engine.py`：`_has_model()` 同时检查 vllm 可 import 且 vendor 路径存在
- GPU 端到端验证通过：71 passed, 21 skipped（含 3 个 GPU OCR 测试），ruff + mypy --strict 全绿
- 关键版本：torch==2.6.0+cu118, torchvision==0.21.0+cu118, vllm==0.8.5+cu118, transformers==4.46.3, flash-attn==2.7.3

遗留问题：
- flash-attn 是 DeepSeek-OCR-2 硬依赖（sam_vary_sdpa.py），编译安装耗时较长
- pyproject.toml 的 ocr_perf 组已无意义（flash-attn 改为必装），可后续清理

## 2026-03-17 Phase 6-8 实现完成：Pipeline + API + 真实 OCR 引擎

主题：完成 Phase 6（Pipeline 编排 + TaskManager）、Phase 7（FastAPI REST API）、Phase 8（DeepSeek-OCR-2 引擎）

完成内容：
- Phase 6：Pipeline.process() 端到端编排（OCR → 清洗 → 去重 → LLM 精修 → 渲染），TaskManager 任务生命周期管理，带真实 LLM 精修的端到端测试通过
- Phase 7：FastAPI REST API（create_app + routes + schemas），POST/GET /api/v1/tasks，GET /api/v1/tasks/{id}/result，httpx 测试客户端 6 个测试全通过
- Phase 8：DeepSeek-OCR-2 引擎封装（deepseek_ocr2.py + preprocessor.py + ngram_filter.py），延迟 import vLLM，ast.literal_eval 安全解析 grounding，去全局变量依赖，GPU 测试标记为可跳过
- 全量 89 passed, 3 skipped（GPU 测试），ruff + mypy --strict 全绿

至此 Phase 0-8 全部完成，MVP 功能齐备。

遗留问题：
- DeepSeek-OCR-2 引擎未在真实 GPU 环境端到端验证（需要部署模型）
- pyproject.toml [ocr] 依赖组的 vllm/torch 版本需根据实际 CUDA 环境调整

## 2026-03-17 实现决策对齐：补齐设计文档缺口

主题：将 4 个实现决策同步到模块文档，消除"边写边设计"的风险

完成内容：
- llm.md：Segmenter 切段时包裹 `<!-- overlap-start/end -->` 标记，说明 max_chars_per_segment 默认 12000 可覆盖，parse_gaps 容错策略（忽略畸形标记）
- pipeline.md：新增 _reassemble() 拼接算法（按 overlap 标记裁剪）、错误处理策略（OCR fail-fast、精修回退、中间产物保留、MVP 错误格式）、并发策略（串行队列 + asyncio.Lock）
- models.md：LLMConfig 用 max_chars_per_segment=12000 替换 segment_max_context_ratio=0.6
- api.md：TaskResponse 增加 error 字段，新增第 7 节 MVP 错误响应说明
- design.md：同步更新配置表和分段策略描述，清除 segment_max_context_ratio 残留引用

交叉验证：
- overlap 标记链路（Segmenter → LLM prompt 规则 3 → _reassemble）三处一致
- max_chars_per_segment 在 llm.md、models.md、design.md 三处一致
- 全文档无 segment_max_context_ratio 残留

遗留问题：无

## 2026-03-17 文档审查与跨文档一致性修复

主题：全量审查 docs/ 文档，修复跨文档不一致和设计缺陷

完成内容：
- design.md：架构图 WebSocket 标注"后续迭代"，POST /tasks 移除 options 加 output_dir，GET result 响应与 api.md 对齐
- design.md：编程接口示例从扁平参数改为嵌套 PipelineConfig(ocr=OCRConfig(...), llm=LLMConfig(...))
- design.md：配置节从 toml 扁平 key 改为嵌套 dataclass 表格格式
- design.md：缺口处理 MVP 改为仅标记不自动补充（9.2 节 + 4.4 节同步）
- design.md："经实测"改为"经手动验证"
- processing.md：OCRCleaner.clean() 改为 async 接口，内部用 aiofiles 读取文件
- processing.md：merge_all_pages() 增加页边界标记 <!-- page: {filename} --> 和图片引用重写 ![](images/N.jpg) → ![]({stem}_OCR/images/N.jpg)
- output.md：Renderer 渲染流程补充完整路径映射逻辑（扫描 {stem}_OCR/images/ 引用 → 复制重命名为 {stem}_{N}.jpg → 重写引用 → 移除页标记）
- llm.md：REFINE_SYSTEM_PROMPT 增加页边界标记说明（规则 4、5、6），LLM 可从 <!-- page: ... --> 提取 after_image
- pipeline.md：阶段 2 清洗改 await，阶段 5 改为仅标记 gaps
- module-design.md：调用链 clean 标注 async，merge_all_pages 补充页标记和引用重写说明，数据流总览同步

设计决策：
- 页边界标记方案：去重合并时在每页头部插入 <!-- page: {image_filename} -->，LLM 据此定位 gap 的 after_image
- 图片引用两阶段重写：合并阶段重写为 {stem}_OCR/images/N.jpg → 输出阶段重写为 images/{stem}_N.jpg
- 缺口处理 MVP 降级：仅标记 gap 到 PipelineResult.gaps，自动补充留后续迭代
- PageDeduplicator.merge_all_pages() 保持同步：纯 CPU 滚动合并，文本量不大无需异步

遗留问题：
- 组合 prompt `Free OCR + grounding` 已手动验证可用，但未在完整 pipeline 中端到端测试
- DocumentSegmenter.max_chars_per_segment=12000 是估算值，需根据实际 LLM 上下文窗口调整

## 2026-03-17 模块接口对齐（OCR / Processing / LLM / Output）

主题：逐模块核对接口设计，确保与 design.md、module-design.md、models.md、pipeline.md 一致

完成内容：
- OCR 模块：`ocr()`/`ocr_batch()` 补充 `output_dir: Path` 参数（ocr.md、design.md、module-design.md、pipeline.md 四处同步）
- OCR 模块：models.md 的 `PageOCR.output_dir` 加注释"OCR 层保证填充，下游可断言非 None"
- Processing 模块：`PageDeduplicator.__init__` 改为接收 `DedupConfig`（与 OCR 引擎风格一致）
- Processing 模块：`merge_all_pages` 补充说明收集各页 regions 到 MergedDocument.images
- Processing 模块：design.md 配置节补充缺失的 `search_ratio`
- LLM 模块：`RefineContext` 统一为 llm.md 版本（`overlap_before/after: str` 替代 `has_overlap_markers: bool`），同步 models.md 和 design.md
- LLM 模块：`Segment` 统一为 llm.md 版本（`text + start_line + end_line` 替代 `heading + overlap_before/after`），同步 models.md 和 design.md
- LLM 模块：`LLMRefiner.refine()` 第一参数统一为 `raw_markdown: str`（design.md、module-design.md、pipeline.md 三处同步）
- LLM 模块：models.md 消费方表移除 `llm/cloud.py` 对 `Segment` 的依赖
- Output 模块：核对通过，无需修改

遗留问题：
- design.md 中 `processing/cleaner.py` 的目录注释未同步更新
- 组合 prompt `Free OCR + grounding` 的实际效果尚未验证
- DocumentSegmenter 的 max_chars_per_segment=12000 是估算值，需根据实际 LLM 上下文窗口调整

## 2026-03-14 模块设计文档拆分

主题：将 module-design.md 拆分为独立模块文档，厘清对接 API

完成内容：
- 将 543 行的 `docs/module-design.md` 拆分为 7 份独立模块文档：
  - `docs/modules/models.md` — 数据对象 + 配置，含各类型的生命周期和消费方
  - `docs/modules/ocr.md` — OCR 层，含 Protocol 接口、DeepSeek 实现、预处理器、ngram
  - `docs/modules/processing.md` — 处理层，含 cleaner 和 dedup 的对外接口和算法
  - `docs/modules/llm.md` — LLM 精修层，含 refiner Protocol、cloud 实现、prompt、分段器
  - `docs/modules/output.md` — 输出层，含 renderer 接口和输出目录结构
  - `docs/modules/pipeline.md` — Pipeline 编排层，含完整编排流程图和编程接口示例
  - `docs/modules/api.md` — API 层，含 REST 路由、请求/响应示例
- 改写 `docs/module-design.md` 为总览索引，含模块间对接 API 总览和依赖关系图
- 每份模块文档统一结构：职责 → 文件清单 → 对外接口 → 依赖的接口 → 内部实现 → 数据流

遗留问题：无

## 2026-03-14 模块详细设计 + OCR-2 参考文档

主题：MVP 模块详细设计文档编写与设计审查修正

完成内容：
- 研究 DeepSeek-OCR-2 源码（vLLM 推理、图像预处理、grounding 解析、NoRepeatNGram）
- 编写 `docs/deepseek-ocr2-reference.md`：OCR-2 部署环境、两种推理方式、配置参数、图像预处理流程、grounding 标签格式与解析、注意事项
- 编写 `docs/module-design.md`：MVP 模块详细设计，覆盖数据对象、配置、OCR 层、处理层、LLM 精修层、输出层、Pipeline 编排层、API 层、模块依赖关系
- LLM 层改用 litellm 统一调用，保留 api_base/api_key 支持中转站
- 设计审查修正 8 项：
  - OCR 接口 prompt 从 config 读取
  - PageOCR 去掉 confidence，加 has_eos/image_size/output_dir
  - 删除独立的 region.py，图片裁剪在 OCR 引擎内部完成（参考官方脚本集成）
  - 每张照片产出独立 `{stem}_OCR/` 目录（result_ori.mmd + result.mmd + images/ + result_with_boxes.jpg）
  - cleaner 简化为页内去重/乱码/空行
  - Gap 标记包含具体原图文件名（after_image）
  - parse_gaps() 独立解析函数
  - 输入支持 jpg/png/jpeg
- 新增 `.claude/rules/tool-failure.md` 工具调用失败处理规则

遗留问题：
- design.md 中 `processing/cleaner.py` 的目录注释未同步更新（仍写着"OCR 输出清洗"，应为"OCR 输出清洗（页内去重、乱码、空行）"）
- design.md 的缺口处理流程描述（第 220-223 行）中 GAP 标记格式未同步更新为 after_image
- 组合 prompt `Free OCR + grounding` 的实际效果尚未验证（design.md 中标注"经实测"，但实际还未在我们的数据上测试）
- DocumentSegmenter 的 max_chars_per_segment=12000 是估算值，需要根据实际 LLM 上下文窗口调整