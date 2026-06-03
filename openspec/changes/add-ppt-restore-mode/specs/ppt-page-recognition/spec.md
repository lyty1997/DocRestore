## ADDED Requirements

### Requirement: 单页版面识别

系统 SHALL 用 PaddleOCR-VL-1.6 `doc_parser`（`vl` pipeline）对矫正后的单页幻灯片做版面分析与识别，产出按阅读顺序排列的 markdown 文本写入 `PageOCR.raw_text`。PPT 模式 MUST 使用 `vl` pipeline，MUST NOT 走代码模式的 `basic` 强制路径。

#### Scenario: 识别单页文字

- **WHEN** 输入一张矫正后的幻灯片正视图
- **THEN** 系统产出含该页文字、按阅读顺序排列的 markdown

#### Scenario: PPT 模式不被强制 basic

- **WHEN** PPT 模式请求进入 OCR producer
- **THEN** OCR 配置保持 `vl` pipeline，不被代码模式的 `_ocr_config_for_code_mode` 改写为 basic

### Requirement: 公式转 LaTeX 及回退

系统 SHALL 将页面中的数学公式识别为 LaTeX；当公式无法可靠转 LaTeX 时系统 SHALL 回退为图片裁剪或原样文本，MUST NOT 产出破坏 markdown 结构的损坏内容。

#### Scenario: 公式成功转 LaTeX

- **WHEN** 页面含可识别的数学公式
- **THEN** 输出 markdown 内对应位置为 LaTeX 表达式

#### Scenario: 公式回退

- **WHEN** 公式过于复杂无法可靠识别
- **THEN** 系统回退为图片或原样文本，不产生损坏的 LaTeX

### Requirement: 图形区域裁剪为图片

系统 SHALL 将化学骨架式、分子模型、反应路径图、数据表/图表、示意图等图形区域裁剪保存为图片到 `PageOCR.regions[].cropped_path`；系统 MUST NOT 把化学结构或分子模型强行转写为文字。

#### Scenario: 化学结构裁成图片

- **WHEN** 页面含化学骨架式或分子模型
- **THEN** 该区域被裁成图片文件，markdown 用图片引用而非文字转写
