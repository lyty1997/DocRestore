## ADDED Requirements

### Requirement: PPT 模式配置

系统 SHALL 提供 `PowerPointRestoreConfig`（挂 `PipelineConfig.ppt`），含 `enable` / `rectify` / `rectify_save_debug` / `llm_polish` 等字段；当 `enable=True` 时系统 SHALL 启用 `_ppt_pipeline` 消费者分支。

#### Scenario: 启用 PPT 模式

- **WHEN** `PipelineConfig.ppt.enable` 为 True
- **THEN** pipeline 走 `_ppt_pipeline` 分支，而非文档或代码分支

### Requirement: 请求级配置覆盖

系统 SHALL 提供 `CreateTaskRequest.ppt`（`PowerPointRestoreConfigRequest`），按 `req.ppt`（非空字段）> `defaults.ppt` 用 `model_copy(update=exclude_none)` 合成，机制与 `code` 一致。

#### Scenario: 请求覆盖默认

- **WHEN** 请求带 `ppt.llm_polish=true`
- **THEN** 合成后的配置 `llm_polish` 为 true，其余字段沿用后端默认

### Requirement: 模式互斥校验

系统 SHALL 保证文档、代码、PPT 三模式互斥；当请求同时把 `code.enable` 与 `ppt.enable` 置真时，系统 MUST 返回业务错误 `ApiBusinessError(code="mode.conflict")` 且 MUST NOT 创建任务。

#### Scenario: 拒绝模式冲突

- **WHEN** 请求同时启用 `code` 与 `ppt`
- **THEN** 返回 `mode.conflict` 业务错误，任务不创建

### Requirement: 任务持久化与恢复

系统 SHALL 在任务快照中持久化 ppt 配置到 DB `ppt` 列，并在 hydrate 与重试时回填，使断点续跑复用原 ppt 配置。

#### Scenario: 持久化并恢复 ppt 配置

- **WHEN** 创建一个 PPT 模式任务后将其恢复或重试
- **THEN** ppt 配置从 DB 回填，任务仍走 PPT 模式

### Requirement: 前端模式三选一互斥

前端 SHALL 用单一「模式」选项（radio）在文档、代码、PPT 间三选一互斥（默认文档）；选中 PPT 时前端 SHALL 透出 LLM 润色开关（默认关）。

#### Scenario: 三选一互斥

- **WHEN** 用户选中 PPT 模式
- **THEN** 代码与文档模式自动取消选中，提交 `ppt.enable=true`

#### Scenario: PPT 润色开关

- **WHEN** 用户选中 PPT 模式
- **THEN** 界面显示 LLM 润色 toggle，默认关闭
