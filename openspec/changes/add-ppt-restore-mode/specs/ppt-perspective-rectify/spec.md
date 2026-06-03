## ADDED Requirements

### Requirement: 幻灯片四边形检测

系统 SHALL 从屏摄照片中检测幻灯片屏幕的四边形（LED 屏亮区）：经 Otsu 阈值取亮区、提取最大轮廓、`approxPolyDP` 近似为 4 个角点。检测失败时系统 MUST 返回空结果以触发回退，MUST NOT 抛异常中断流程。

#### Scenario: 成功检测到四边形

- **WHEN** 输入一张含清晰幻灯片屏幕的屏摄照片
- **THEN** 系统返回含 4 个角点（左上/右上/右下/左下顺序）的 Quad

#### Scenario: 检测不到四边形

- **WHEN** 输入一张屏幕边界模糊或被严重遮挡下边缘的照片
- **THEN** 系统返回空结果（None），不抛异常，供上层回退原图

### Requirement: 透视矫正为正视图

系统 SHALL 用检测到的四边形对原图做 `warpPerspective` 透视变换得到正视图，并 SHALL 按配置比例（默认 0.2）上抬顶边，以补回常被吊顶或暗色标题栏遮挡的区域。

#### Scenario: 矫正强透视照片

- **WHEN** 输入一张透视倾斜的幻灯片照片及其 Quad
- **THEN** 系统输出矫正后的正视图，幻灯片内容占满画面且顶部标题栏完整

### Requirement: 矫正证据落盘

当 `rectify_save_debug=True` 时，系统 SHALL 将矫正前后对照图落盘到 `{output_dir}/.rectified/{stem}_before` 与 `{stem}_after`，作为 S2 验收证据；该 `.rectified/` 目录 MUST 默认排除在下载打包之外。

#### Scenario: 落盘 before/after 对照

- **WHEN** 启用 `rectify_save_debug` 并矫正某一页
- **THEN** `.rectified/` 下生成该页的 before 与 after 两张对照图

### Requirement: 矫正失败兜底

系统 SHALL 在四边形检测失败或矫正异常时回退使用原图路径继续下游 OCR；系统 MUST NOT 因单页矫正失败而使整个任务失败。

#### Scenario: 回退原图继续

- **WHEN** 某页四边形检测失败或矫正抛出异常
- **THEN** 系统以原图路径喂给 OCR，流程继续，并在质量报告中标记该页未矫正
