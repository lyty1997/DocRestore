## Context

PPT 屏摄还原是 DocRestore 第三个还原模式。现有流式 Pipeline 是 producer/consumer 架构：`_ocr_producer` 逐页 OCR 推入 `page_queue`，消费者分支按 `*_cfg.enable` 分流（文档模式 `_stream_process` 跨页去重 + 流式精修；代码模式 `_code_pipeline` 跨页归类成源文件）。本变更新增 PPT 模式 `_ppt_pipeline` 分支。S0 选型（AGE-84）已确认 PaddleOCR-VL-1.6 + 透视矫正必需。约束：本机 RTX 4070 SUPER 12G、单引擎切换。**完整设计真相源：`docs/zh/ppt-mode.md`**（含 17 处接入点 文件:行、函数签名草案、架构/流水线图）。

## Goals / Non-Goals

**Goals:**
- 屏摄 PPT 还原为保序单 `document.md`：文字 + 公式 LaTeX + 化学结构裁图
- 最大化复用现有 producer/consumer 骨架、`PageOCR`、`PipelineResult`、两阶段图片引用、前端多文档展示
- 与文档/代码模式互斥三选一，行为隔离、零回归

**Non-Goals:**
- 不做 SMILES 文本抽取（化学结构保持图片）
- 不引入 MinerU / dots.ocr（S0 已剔除）
- 默认不做 region bbox 显式排序（信任 VL 阅读序，留 S3 实测回退口）
- 不改文档/代码模式既有行为

## Decisions

1. **独立第三分支 `_ppt_pipeline`**（而非复用文档模式 + 开关）。理由：OCR 用 `vl`、不去重、有矫正前处理三点都与文档模式不同，独立分支边界清晰。Alt：复用文档模式分支 → 屏摄特判混入文档模式，边界糊。
2. **透视矫正放 `_ocr_producer` 逐页前处理 hook**。理由：矫正是 CPU(OpenCV)、OCR 是 GPU，producer 内最小侵入。Alt：独立矫正 producer → 多一层队列，过度工程。
3. **直接复用 `PageOCR`，不建 region 中间结构**。理由：VL markdown 已是阅读序。Alt：建 region 列表 + bbox 排序 → 过度工程；仅 S3 实测阅读序错乱时回退。
4. **不跨页去重**。理由：PPT 每张照片 = 一张独立幻灯片。Alt：复用 `PageDeduplicator` → 误删重复版式。
5. **LLM 轻润色默认关 + 前端可开**。理由：VL 单页质量高；用户视真图 OCR 效果决定是否润色。
6. **DB `ppt` 列同 `code` 列机制**。理由：复用现有快照/hydrate，nullable 列老任务无需手动迁移。

## Risks / Trade-offs

- [四边形检测在遮挡/反光下失败] → 回退原图；VL 对轻微透视仍有 ~30% 基线；落盘 before/after 便于排查。
- [VL 单页阅读序在多栏/化学密集版式错乱] → S3(AGE-87) 真图实测；错乱则回退 region bbox 排序（见 Open Questions）。
- [OpenCV(cv2) 未在 `pyproject.toml`] → 接入第一步确认并补齐。
- [前端 radio 改造动到代码模式现有 toggle UI] → 同步回归测试代码模式提交链路。

## Migration Plan

- DB 加 `ppt` JSON 列，nullable；老任务 `ppt=NULL` 退回默认（不启用 PPT），无数据迁移。
- 分阶段落地：S2(AGE-86) → S3(AGE-87) → S4(AGE-88) → S5(AGE-89) → S6(AGE-90) E2E。
- 回滚：`ppt.enable` 默认 False，未启用即无行为变化；可整分支 revert。

## Open Questions

- VL `doc_parser` 单页 markdown 阅读序在真图多栏/化学版式下是否可靠？（S3/AGE-87 实测决定是否引入 region bbox 排序）
- LLM 轻润色对 VL 输出是否有正收益？（按真图 OCR 效果决定默认是否开）
- OpenCV 是否已在依赖？（接入前确认）
