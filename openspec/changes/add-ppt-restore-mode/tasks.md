## 1. S2 透视矫正（AGE-86）

- [x] 1.1 确认 OpenCV(cv2) 在 `pyproject.toml` 依赖，缺则补齐
- [x] 1.2 新增 `processing/slide_rectify.py`：`Quad` / `detect_slide_quad` / `rectify` / `rectify_page`
- [x] 1.3 `rectify_page` 落盘 `.rectified/` before/after 对照 + 失败回退原图路径
- [x] 1.4 OpenCV 阻塞调用走 `asyncio.to_thread`，不阻塞事件循环
- [x] 1.5 单测：正常矫正 + 检测失败回退 + 落盘证据（真图 before/after，断言从输入派生）

## 2. S3 识别与裁图（AGE-87）

- [ ] 2.1 PPT 模式 OCR 配置确保 `vl` pipeline（不走 `_ocr_config_for_code_mode`）
- [ ] 2.2 验证 VL `doc_parser` 裁图覆盖化学结构/分子模型（真图证据）
- [ ] 2.3 公式 LaTeX 兜底验证（无法识别回退图片/原文）
- [ ] 2.4 实测单页阅读序是否可靠，决定是否需要 region bbox 排序（Open Question）

## 3. S4 组装与合并（AGE-88）

- [ ] 3.1 新增 `output/ppt_renderer.py`：`render_ppt_document`
- [ ] 3.2 单页保序组装 + 多页按文件序合并 `document.md` + 页分隔线/锚点
- [ ] 3.3 复用 `renderer.py::_rewrite_and_copy_images` 做两阶段图片引用
- [ ] 3.4 可选 LLM 轻润色（保护公式与图片引用）
- [ ] 3.5 磁盘版去锚点 / 内存版留锚点
- [ ] 3.6 单测：保序、不去重、图片引用有效、润色不破坏公式（断言从输入派生）

## 4. S5 配置/API/Pipeline/前端接入（AGE-89）

- [ ] 4.1 `config.py` 新增 `PowerPointRestoreConfig` + `PipelineConfig.ppt`
- [ ] 4.2 `schemas.py` 新增 `PowerPointRestoreConfigRequest` + `CreateTaskRequest.ppt`
- [ ] 4.3 `routes.py` 合成 `ppt_cfg` + 模式互斥校验（`mode.conflict`）
- [ ] 4.4 `task_manager.py` `Task.ppt` + 持久化/hydrate + DB `ppt` 列 migration
- [ ] 4.5 `pipeline.py` `process_tree`/`process_many`/`_stream_pipeline` 签名加 `ppt`
- [ ] 4.6 `pipeline.py` 分支点加 `elif ppt_cfg.enable` → `_ppt_pipeline`；`ocr_effective` 用 `vl`
- [ ] 4.7 `_ocr_producer` 加矫正 hook（PPT 模式逐页先 `rectify_page`）
- [ ] 4.8 新增 `_ppt_pipeline` 消费者（收齐保序 → render → 可选润色）
- [ ] 4.9 前端 `TaskForm` radio 三选一互斥 + PPT 润色开关 + `onSubmit`/`useTaskRunner` 透传
- [ ] 4.10 i18n keys（en / zh-CN / zh-TW）
- [ ] 4.11 下载打包默认排除 `.rectified/`
- [ ] 4.12 错误 i18n：`mode.conflict`（前后端链路）

## 5. S6 E2E 验证与收尾（AGE-90）

- [ ] 5.1 `test_images/PPT` 全量真图 E2E：`document.md` 页序正确 + 文字/公式/图片三类齐全 + 图片引用有效
- [ ] 5.2 断言从输入派生（不写死数据集文件名/正文关键词）
- [ ] 5.3 质量门禁 `bash scripts/check_quality.sh` 全绿（mypy/ruff/typos/前端/pytest）
- [ ] 5.4 更新 `docs/zh/progress.md` + 模块文档 + 项目 memory
- [ ] 5.5 英文文档 `docs/en/` 同步排期
