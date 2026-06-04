## ADDED Requirements

### Requirement: 单页保序组装

系统 SHALL 将每页 `PageOCR` 的识别结果按 VL 输出的阅读顺序组装为单页 markdown 片段。

#### Scenario: 组装单页

- **WHEN** 给定一页的 PageOCR
- **THEN** 产出该页阅读序的 markdown 片段

### Requirement: 多页按原序合并且不去重

系统 SHALL 按输入照片的文件顺序（= 原 PPT 页序）将各单页 markdown 合并为单个 `document.md`，逐页分节、页间用 markdown 分隔线分隔，并在每页正文前插入 `<!-- page: {filename} -->` 锚点标记。系统 MUST NOT 对 PPT 页做跨页去重。

#### Scenario: 多页保序合并

- **WHEN** 输入 N 张照片对应 N 页幻灯片
- **THEN** `document.md` 含 N 个分节，页序与输入文件序一致

#### Scenario: 不跨页去重

- **WHEN** 相邻两页含相似版式或重复元素
- **THEN** 两页内容均完整保留，不被去重删除

### Requirement: 两阶段图片引用

系统 SHALL 复用文档模式两阶段图片引用：将 `{stem}_OCR/images/N.ext` 复制到输出 `images/{stem}_N.ext` 并改写 markdown 引用，使 `document.md` 内图片引用有效可渲染。

#### Scenario: 图片引用重写

- **WHEN** 某页含裁剪图片
- **THEN** 图片被复制到 `images/` 且 `document.md` 引用指向有效路径

### Requirement: 可选 LLM 轻润色

系统 SHALL 在 `llm_polish=True` 时对合并文档做轻润色，且 MUST NOT 改动公式与图片引用；`llm_polish=False`（默认）时跳过 LLM。

#### Scenario: 默认不润色

- **WHEN** `llm_polish` 为 False
- **THEN** 直接输出组装后的 `document.md`，不调用 LLM

#### Scenario: 开启润色保护公式与图片

- **WHEN** `llm_polish` 为 True
- **THEN** 润色后的公式与图片引用与润色前保持一致

### Requirement: 磁盘版去除页锚点

系统 SHALL 在写盘的 `document.md` 中去除 `<!-- page: -->` 注释标记，但在返回前端的内存版 markdown（`PipelineResult.markdown`）中保留，用于前端滚动定位。

#### Scenario: 磁盘版无锚点注释

- **WHEN** 写盘 `document.md`
- **THEN** 文件内不含 page 锚点 HTML 注释，而内存版仍含
