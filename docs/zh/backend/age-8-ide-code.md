# AGE-8 设计：IDE 代码照片 → 源文件还原

**状态**：设计阶段，等用户确认后进入开发  
**Linear**：[AGE-8](https://linear.app/axiom-mind/issue/AGE-8/ide-代码照片-源文件还原)  
**输入**：`test_images/Chromium_VDA_code/`（273 张 IDE 截图，VSCode 暗色主题）  
**期望输出**：每个源文件一份 `.cc`/`.h`/`.gn`/`.py`/... 源文件（不再是单一 markdown）

## 1. Spike 实测的问题

跑 8 张图过现有 pipeline（`output/age8-baseline/`），暴露 8 类问题，分四层：

### 1.1 OCR 层
| # | 问题 | 实测证据 |
|---|---|---|
| O1 | **IDE UI 元素混入正文** | `BUILD.gn — src [SSH: 11.164.75.72]`、tab bar `Openmax_video_decode_accelerator.cc 4 × ...`、breadcrumb `>gpu >openmax >...` 全被识别为正文/标题 |
| O2 | **侧边栏文件树识别失败** | 图标 OCR 成 `田田田田`、文件名串成长行 |
| O3 | **底部 terminal/状态栏混入** | `PROBLEMS 15 TERMINAL PORTS` 当正文 |
| O4 | **IDE 视觉行号混入代码** | `16 #include "media/gpu/macros.h"` 数字 16 是 IDE 行号 |

### 1.2 Cleaner / Merger 层
| # | 问题 | 实测证据 |
|---|---|---|
| C1 | **多文件无分隔** | 一张图含 2 个文件（左 .cc 右 .gn），output 全部串成一段 |
| C2 | **跨页重复未去** | `#include "omxil/vsi_vendor_ext.h"` + `# Copyright 2024` 在多页拍照时重复 |

### 1.3 LLM 层
| # | 问题 | 实测证据 |
|---|---|---|
| L1 | **代码块语言识别太粗** | LLM 把所有代码丢进 ```text` 围栏，没区分 C++/Python/GN |
| L2 | **代码内容连续性丢失** | 每行代码间被插入空行，破坏原 IDE 排版（缩进未保留信息密度）|

### 1.4 渲染层
| # | 问题 | 现状 |
|---|---|---|
| R1 | **输出单 markdown** | 现在是 1 张图 → 1 段 markdown，但用户想要 → N 个源文件 `.cc/.h/.py/.gn/...` |

## 2. 设计目标

按用户优先级（CLAUDE.md 第二优先级）+ Linear 描述：
1. **识别代码语言和文件结构** → 自动识别每段属于哪个文件 + 什么语言
2. **保持缩进** → 输出可直接编译/运行的源文件
3. **处理 IDE UI 干扰**（行号、侧边栏、tab bar、terminal）→ 全部剥掉
4. **LLM 防幻觉** → 输出代码必须忠于原图，不能 LLM 自由发挥补全

## 3. 整体架构

```
[N 张 IDE 截图]
   ↓
[OCR 引擎] —— 现有，但改 OCR 之前/之后多一层 IDE-UI 剪裁
   ↓
[IDE-UI 剪裁]（新模块）—— 按视觉分区切掉 sidebar/tab/terminal/breadcrumb
   ↓
[代码区分栏检测]（新）—— 识别每张图里的多个代码文件区域
   ↓
[per-file OCR + cleaner]（复用 + 新规则）—— 每个区独立 OCR + 剥行号 + 缩进保留
   ↓
[文件归类]（新）—— 跨张图聚合：`tab 名 + breadcrumb` 聚到同一文件
   ↓
[per-file LLM 精修]（改：用代码专用 prompt + 限制幻觉）
   ↓
[per-file 输出]：output/<task>/
                ├── files/
                │   ├── media/gpu/openmax/openmax_video_decode_accelerator.cc
                │   ├── media/gpu/openmax/openmax_video_decode_accelerator.h
                │   ├── media/gpu/openmax/BUILD.gn
                │   └── ...
                ├── files-index.json   # 文件列表 + 来源页 + 置信度
                └── document.md        # 兼容旧 UI：把所有文件包成一份 markdown
```

## 4. 关键模块设计

### 4.1 IDE-UI 剪裁（`processing/ide_ui_strip.py`，新建）

**输入**：原图 PIL.Image  
**输出**：裁剪后的"纯代码区"图像（list[Image]，可能多个分栏）+ 元数据 `{tab_name, breadcrumb, language_hint}`

**实现策略**（保守 + 可重复）：
- 用 OpenCV 边缘检测找出 IDE 的"分隔线"（垂直 + 水平的暗灰色直线）
- 阈值：宽度 < 20px 的连续暗带视为分隔条
- 顶部裁掉前 N% 高度（典型 tab + breadcrumb 占 5-8%）
- 底部裁掉 terminal 区域（典型占 15-25%，按底部水平分隔线定位）
- 左侧裁掉文件树宽度（典型 15-20% 宽度，按第一条垂直分隔线定位）
- 中间剩下的代码区可能再分 2-3 列（VSCode split editor）

**风险与回退**：
- 拍照透视/反光导致分隔线不直 → fallback 到固定比例裁剪（顶 8%、底 20%、左 18%）
- 用 OCR 输出辅助：跑一遍 OCR 后用 `BUILD.gn — src` / `TERMINAL` 等 IDE 关键词锚定区域边界

**配置**（OCRConfig 新增）：
```python
class IDEUIConfig(BaseModel):
    enable: bool = False  # 默认关，task 配置打开
    detect_strategy: str = "hybrid"  # "geometric" | "ocr_anchored" | "hybrid"
    fallback_top_ratio: float = 0.08
    fallback_bottom_ratio: float = 0.20
    fallback_sidebar_ratio: float = 0.18
```

### 4.2 多分栏检测（`processing/code_columns.py`，新建）

**输入**：剪裁后的代码区图  
**输出**：`list[Column]`，每个 Column 含独立子图 + 大致 OCR 区域

**实现**：
- 用代码区**中位行**位置 + 行内空白分布，找垂直白带（VSCode split editor 之间是约 8-12px 的暗灰分隔条）
- 一栏 vs 多栏的判定：垂直白带覆盖 ≥ 80% 高度 + 宽度 ≥ 4px

### 4.3 跨张文件归类（`processing/code_file_grouping.py`，新建）

**核心问题**：同一个文件可能被拍 1-N 张图（连续滚动），不同文件拍 1-N 张图，需要把"同一文件的多张图"聚成一组。

**signal 来源**：
1. **tab/breadcrumb OCR**：每张图顶部 tab bar 含当前文件路径（如 `BUILD.gn — src`）
2. **文件路径 hint**：breadcrumb 含 `gpu > openmax > openmax_video_decode_accelerator.cc`
3. **代码内容连续性**：相邻两张图代码末/首相同 → 同一文件连续滚动

**算法**：
1. 每张图先 OCR 顶部 100px 拿 tab/breadcrumb，正则提取 `<filename>` 和路径
2. 按路径分组（`media/gpu/openmax/openmax_video_decode_accelerator.cc` → 1 组）
3. 同组内按相邻代码内容是否衔接判断"哪几张图是连续的同文件"

**fallback**：tab/breadcrumb OCR 不可读时，用图片 EXIF/拍摄时间戳 + 简单序号启发式

### 4.4 行号 + 缩进保护（强化 cleaner）

**改 `OCRCleaner` 加 `strip_ide_line_numbers()` 步骤**：
- 复用 `markdown_polish.strip_code_block_line_numbers` 的同款 regex（要求 ≥ 3 行单调递增）
- 但场景不同：在**单页 OCR 直出 + 还没拼回**时就剥，因为 IDE 行号确定是噪音
- 缩进保护：剥行号后要保留前导空白；现有正则已支持

### 4.5 LLM prompt（新 `prompts.py::CODE_REFINE_SYSTEM_PROMPT`）

针对代码专用的精修规则，硬约束：
1. **绝不臆造代码**：LLM 只能修复明显的 OCR 错（如 `0` ↔ `O`、`l` ↔ `1`），不能补全缺失的行
2. **缩进必须保留**：不准把 4 空格改 2 空格，不准 tab → 空格自动转
3. **代码片段必须用 ``` 围栏 + 语言标签**：根据文件后缀（.cc → cpp，.h → cpp，.py → python，.gn → gn）
4. **遇到无法识别的字符**：保留原文 + `// OCR-Q: <猜测>` 注释让人工核查
5. **禁止合并/拆分原 IDE 中明显的连续行**：保持行结构

**输入格式**：每个文件的多张图按顺序拼接的 OCR 文本块 + 文件路径 + 语言 hint  
**输出格式**：纯代码（不带 ``` 围栏，因为是直接写文件）+ 末尾追加 `### OCR-Q 待核查` 列表

### 4.6 输出渲染（`output/code_renderer.py`，新建）

- 给每个识别出的源文件写到 `output/<task>/files/<relative-path>`
- 写 `files-index.json`：
```json
[
  {
    "path": "media/gpu/openmax/openmax_video_decode_accelerator.cc",
    "language": "cpp",
    "source_pages": ["DSC06835", "DSC06836", "DSC06837"],
    "confidence": 0.85,
    "ocr_unresolved_count": 3
  }
]
```
- `document.md` 兼容旧 UI：拼所有文件 + 围栏

## 5. 与现有 pipeline 的关系

| 现有阶段 | AGE-8 复用 | 改动 |
|---|---|---|
| OCR 引擎 | ✅ 复用 PaddleOCR-VL | 在 OCR 之前加 IDE-UI 剪裁；OCR 之后加多分栏 |
| OCRCleaner | ✅ | 加 `strip_ide_line_numbers()` |
| PageDeduplicator | ⚠️ 部分复用 | 跨张文件归类是上层逻辑，dedup 仅做单文件内连续滚动重复 |
| Streaming Pipeline | ✅ | 新走 `process_ide_code()` 入口，复用 OCR 队列模型 |
| LLM Refiner | ⚠️ | 加新 prompt + 改输出后处理（不带围栏） |
| Renderer | ❌ | 新 `code_renderer.py`，不沿用 markdown renderer |

## 6. 配置入口

```python
class CodeRestoreConfig(BaseModel):
    """AGE-8 IDE 代码照片还原"""
    enable: bool = False
    ide_ui: IDEUIConfig = Field(default_factory=IDEUIConfig)
    column_detect: bool = True
    file_grouping_strategy: str = "tab_breadcrumb"  # "tab_breadcrumb" | "content_only"
    output_files_dir: str = "files"  # 输出子目录名


class PipelineConfig(BaseModel):
    ...
    code: CodeRestoreConfig = Field(default_factory=CodeRestoreConfig)
```

API 层：`POST /tasks` 加 `code: CodeRestoreConfig | None`，前端可勾选"识别 IDE 代码"模式。

## 7. 测试策略

### 单测（每模块独立）
- `tests/processing/test_ide_ui_strip.py`：用合成几何图 + 真实 1-2 张照片测分隔线检测
- `tests/processing/test_code_columns.py`：单栏 / 双栏 / 三栏 / 无分隔合成图
- `tests/processing/test_code_file_grouping.py`：mock OCR 输出，验证 tab/breadcrumb 分组逻辑
- `tests/llm/test_code_prompt.py`：验证 prompt 不诱导 LLM 补全代码（用 mock LLM）

### 集成测试
- `tests/pipeline/test_age8_e2e.py`：用 8 张 spike 子集跑全流程，断言：
  - 至少识别出 N 个不同源文件（N≥3）
  - 输出 `.cc` 文件包含 `#include` 但不含 IDE UI 字符串（`PROBLEMS`、`TERMINAL` 等）
  - `files-index.json` 含全部预期文件路径

### 实测
- 全 273 张过一遍，看 quality_report 触发哪些信号
- 抽 5 个识别出的源文件人工 diff 与公开 Chromium 源（对得上 ≥ 80% 算成功）

## 8. 分阶段交付（按用户确认调整：先做多栏视觉切分）

### Phase 1：视觉切分基础（3-5 天）

**核心**：直接上多栏识别，把 IDE 截图切成"干净代码区"图，**不动 OCR/LLM**。

- [ ] `processing/ide_ui_strip.py`：IDE-UI 剪裁
  - 几何检测（OpenCV 找垂直/水平暗灰分隔线）
  - fallback：固定比例（顶 8% / 底 20% / 左 18%）
- [ ] `processing/code_columns.py`：多栏切割
  - 检测代码区中央的垂直分隔条（高度 ≥ 80% + 宽度 ≥ 4px 的暗灰带）
  - 单栏 / 双栏自动判定，>2 栏暂不支持（spike 没见到）
  - fallback：固定 50/50 切
- [ ] 输出每张图剪裁后的预览图到 `output/<task>/columns/<page>_col{N}.png`
- [ ] CLI 工具 `scripts/preview_ide_columns.py` 批量跑 + 出 PNG 给人眼检验

**验收**：273 张里 ≥ 90% 切出干净的"每栏一图"，肉眼无 UI 干扰（顶 tab、底 terminal、左 sidebar 都剥掉）。

**这一步做不好后面全错** —— 视觉切分的准确率是上限，所以独立验证 + 调参，再进入 Phase 2。

### Phase 2：OCR + 单文件归类（5-7 天）

**核心**：每栏独立 OCR，按 tab/breadcrumb 跨张聚到对应源文件，输出独立 `.cc/.h/.py/.gn` 文件。

- [ ] 每栏独立喂现有 OCR 引擎（PaddleOCR-VL）
- [ ] `processing/ide_meta_extract.py`：从每栏图顶部 50-100px OCR tab + breadcrumb，正则提取
  - 文件名：从 tab 标题（`BUILD.gn — src` → `BUILD.gn`）
  - 路径：从 breadcrumb（`> media > gpu > openmax > foo.cc` → `media/gpu/openmax/foo.cc`）
- [ ] `processing/code_file_grouping.py`：按"文件路径"分组每栏
  - 同路径 → 聚到同一 source file
  - 同源 file 内按"末尾代码 ↔ 下一张开头代码"重叠去重（沿用现有 `merge_two_pages` 思路，只看 50% 末尾 ↔ 50% 开头）
  - **明确不跨文件合并**：哪怕同张图的左右两栏内容相邻，输出仍各自独立文件
- [ ] cleaner 加 `strip_ide_line_numbers()`：复用 `markdown_polish.strip_code_block_line_numbers` 的"≥ 3 行单调递增数字前缀"判定，但在 OCR 直出阶段就剥
- [ ] `output/code_renderer.py`：写到 `output/<task>/files/<relative-path>` + `files-index.json`

**验收**：8 张 spike 子集 → 还原 ≥ 3 个独立源文件，路径正确（与 chromium 源 grep 对得上），OCR 字符还原 ≥ 80%（按肉眼对照）。

### Phase 3：编译级精修（1-2 周）

**核心**：LLM 字符级修复 + 缩进/语法兜底 + 前端原图↔代码对照 + 编译验证集成。

- [ ] `llm/prompts.py::CODE_REFINE_SYSTEM_PROMPT`：
  - 允许的字符级修正（白名单）：`O↔0`, `l↔1`, `I↔l`, `rn↔m`, `""↔" "`, 全角标点 → 半角等
  - 禁止：加/删整行、改函数签名、补全省略部分、重命名变量
  - 输入：单文件的多张图按顺序拼接的 OCR 文本 + 文件路径 + 语言 hint
  - 输出：纯代码（不带围栏）+ 末尾追加 `### OCR-Q 待核查` 列表
- [ ] LLM 必须输出"修改了哪几行 + 修改前/后内容"的 changelog（写到 `files-index.json` 的 `llm_corrections` 字段）
- [ ] 编译验证脚本 `scripts/age8_compile_check.py`：
  - 调 gcc/clang/python -m py_compile 跑还原文件，记录通过率
  - 失败的文件标 `compile_failed=true` + 错误日志
- [ ] 前端：扩展现有"原图 ↔ markdown 同步滚动"机制 → "原图 ↔ 还原代码对照"
  - 复用 page marker 的滚动锚点
  - 代码侧用 monaco editor 或 pre + syntax highlight

**验收**：
- 至少 3 个还原文件能通过编译（gcc -fsyntax-only / python -m py_compile）
- LLM 字符级修正不引入语义错（人工抽查 5 个文件 + diff 公开 chromium 源）
- 前端 UI 能左右对比着核验

## 9. 用户已确认的关键决策

| # | 决策 | 答案 | 实施位置 |
|---|---|---|---|
| 1 | 输出粒度 | **每文件独立输出 `.cc/.h/.py/.gn` 等源文件**，最终验收要求能通过编译 | Phase 2 落地，Phase 3 加编译验证 |
| 2 | LLM 幻觉容忍度 | **允许字符级 OCR 错纠正**（白名单），结果靠"原图 ↔ 代码"前端对照核验。**禁止**加/删/改语义结构 | Phase 3 prompt + 前端 |
| 3 | 多语言混排（同图含 C++ + GN） | **按"是否同一份文件"分组**，同图但分栏 ≠ 同文件 → 必须分开输出，绝不合并两份文件源码 | Phase 2 file_grouping 强约束 |
| 4 | 跨张归类 fallback（tab/breadcrumb 不可读时用 EXIF 时间戳）| **暂不做**。tab/breadcrumb 通常清晰可读；如真发现 OCR 拿不到 tab 的图 → 加 quality 信号 `code.tab_unreadable`，由人工补正再处理 | 不实施 |
| 5 | Phase 1 范围 | **直接做多栏识别**（覆盖实际场景），单栏作为多栏检测时的退化情况自动处理。视觉切分能力优先，OCR/LLM 后置 | Phase 1 改为"视觉切分基础" |

## 10. 不做的事（明确边界）

- **不还原 IDE 配置 / 设置**（用户拍的可能是 settings.json 截图，但 AGE-8 只关心源文件）
- **不识别 git diff / merge conflict 视图**（这些是 IDE 特殊 UI 模式）
- **不还原图标 / 字符画**（OCR 看到图标就剥，不尝试还原）
- **不主动联网验证代码**（不去 GitHub 搜匹配，避免数据泄漏 + 复杂依赖）
- **不实施 EXIF 时间戳 fallback**（决策 4，等真出现 tab 不可读再加）

## 11. 风险与未知

| 风险 | 影响 | 应对 |
|---|---|---|
| IDE 主题变化（亮色 / 高对比度）导致几何检测失败 | 中 | 配置切换，OCR-anchored 路径作 fallback |
| 拍照透视严重，分隔线不直 | 中 | 几何检测前先做透视矫正（如效果不佳，Phase 1 验收阶段评估） |
| 273 张里有 IDE 弹窗 / 命令面板 / 设置页面截图 | 中 | quality_report 标记 `code.unrecognized_layout`，跳过这种图 |
| 同名文件跨多个目录（如多个 BUILD.gn） | 低 | 用 breadcrumb 路径区分，不仅看文件名 |
| Phase 1 几何 fallback 对某些图比例错 | 低 | 先识别"明显是 IDE 截图"的特征（暗背景 + 等宽字体），否则跳过 AGE-8 流程走旧 markdown 流程 |
| LLM 字符级修正引入语义错（如把变量名 `O1` 改成 `01`） | 中 | Phase 3 LLM 必须输出 changelog；编译验证不过的文件回退到未精修版 + 标 `compile_failed` |
