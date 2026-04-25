# AGE-8 设计：IDE 代码照片 → 源文件还原

**状态**：v2 方案 Phase 1.2 已落地（AGE-53 ✅），其余 Phase 1 实施中
**Linear**：[AGE-8](https://linear.app/axiom-mind/issue/AGE-8/ide-代码照片-源文件还原)
**输入**：`test_images/Chromium_VDA_code/`（NAS 全 272 张 IDE 截图，VSCode 暗色主题）
**期望输出**：每个源文件一份 `.cc`/`.h`/`.gn`/`.py`/... 源文件 + `files-index.json`

---

## 0. 设计反转记录（v1 → v2，2026-04-25）

### v1（已废弃）
基于"像素方差几何检测剥 IDE UI + 多栏切割"。已 cancel：AGE-41（ide_ui_strip）/ AGE-42（code_columns）/ AGE-43（preview CLI）/ AGE-44（per-column OCR）。

**根本错误**：
- 假定 sidebar/tab/terminal 占图的固定比例区间。**真实 IDE 任意可拖拽**——分栏宽度可拖、sidebar 折叠/展开、字体可缩放、屏幕分辨率不一，**所有固定阈值都失效**。
- 8 张 spike 实测：7/8 sidebar fallback、1/8 column fallback 硬切，结果不可用。

### 替代方向调研（全部不可用）
- **PaddleOCR-VL `merge_layout_blocks=False`**：实测无效，VL layout 模型对复杂 IDE 直接输出单 content block 是模型能力极限
- **PP-DocBlockLayout 降低 threshold 到 0.05**：永远输出单一 Region 覆盖整图（训练分布外）
- **PP-StructureV3 / MinerU 的 reading order pointer network**：方向**正好相反**——它们优化"多栏论文 → 单列阅读顺序"，会把多栏代码错误合并

### v2（当前）：行号列锚点
**核心**：用 IDE 编辑器的内在不变量——**行号列**——做布局锚点。
- **8 张 spike**：100% 检出 2 个 anchor，单调性 100%
- **全 272 张 NAS 数据集**：100% 成功率（详见 §7）

---

## 1. 总体架构（v2）

```
[整张 IDE 截图]
   ↓
[PaddleOCR PP-OCRv5（basic pipeline，非 VL）]
   ↓ 行级 rec_boxes + texts + scores
[list[TextLine]]
   ↓
[ide_layout.analyze_layout]   ← AGE-53 ✅ 已落地
   ├─ 筛行号：text=^\d{1,4}$ + score≥0.8
   ├─ x1 聚类（bandwidth=20）
   ├─ 单调性筛选（≥60% 升序对）→ LineNumberAnchor 列表
   └─ 区域归类：column_i / above_code / sidebar / below_code
   ↓
[code_assembly.assemble_columns]   ← AGE-54 待实施
   ├─ 行号 vs 代码 line 分离
   ├─ y 排序 + 行号配对
   └─ 缩进保留（按代码字符宽度推算）
   ↓
[ide_meta_extract]   ← AGE-45（沿用 v1 设计）
   tab/breadcrumb 中提取文件名 + 路径
   ↓
[code_file_grouping]  ← AGE-46（沿用，输入改为 column 文本）
   按文件路径跨张归类，同文件按 y 顺序拼接
   ↓
[CodeLLMRefiner（CODE_REFINE_SYSTEM_PROMPT）]   ← AGE-48
   字符级 OCR 修正（白名单），禁语义改动
   ↓
[code_renderer]   ← AGE-47
   写到 output/<task>/files/<relative-path> + files-index.json
```

---

## 2. 关键模块设计

### 2.1 ide_layout（AGE-53，✅ 已实现 + 8/8 spike + 272/272 全集验证）

**位置**：`backend/docrestore/processing/ide_layout.py`

**核心数据类**：
```python
@dataclass
class TextLine:                      # 在 models.py
    bbox: tuple[int, int, int, int]
    text: str
    score: float

@dataclass
class LineNumberAnchor:
    x1_center: int
    x1_min: int
    x2_max: int                      # 代码区起点 = anchor.x2_max
    y_top: int
    y_bottom: int
    line_count: int
    num_range: tuple[int, int]
    monotonic_ratio: float

@dataclass
class IDELayout:
    anchors: list[LineNumberAnchor]
    columns: list[list[TextLine]]    # 每栏内文本行
    above_code: list[TextLine]
    below_code: list[TextLine]
    sidebar: list[TextLine]
    other: list[TextLine]
    flags: list[str]
```

**算法**：
1. 筛"行号 line"：`text 严格匹配 ^\d{1,4}$ + score ≥ 0.8`
2. 按 x1 精细聚类（bandwidth=20px，行号列对齐极紧）
3. 每簇若 `≥ 5 行` 且 `升序对占比 ≥ 0.6` → 一个合格 anchor
4. 栏数 = anchor 数（任意 N 自适应，已实测 1/2/3 栏均 work）
5. 区域归类决策树（按优先级）：
   - `y_max < min(anchor.y_top)` → above_code
   - `x_max < anchor[0].x1_min` → sidebar（无论 y，含 sidebar 文件树底部越界场景）
   - `y_min > max(anchor.y_bottom)` → below_code
   - `x_min ∈ [anchor_i.x1_min, anchor_{i+1}.x1_min)` → column_i
   - 否则 → other

**为什么稳**（IDE 编辑器内在不变量）：
- **字体/缩放无关**：行号 bbox 容差是相对宽度，字体大 bbox 也大
- **栏宽拖拽无关**：栏边界完全由相邻 anchor x 推导
- **sidebar 折叠/展开无关**：sidebar = 最左 anchor 之左
- **屏幕分辨率无关**：完全不用绝对像素阈值
- **任意栏数**：anchor 数 = 栏数

**Quality flags**：
- `code.no_anchor`：未检出（VSCode hide line numbers / OCR 全失败）
- `code.single_anchor` / `code.three_plus_anchors`：栏数 hint
- `code.weak_monotonic`：anchor 单调比 < 0.8（OCR 行号识别噪声）

### 2.2 code_assembly（AGE-54，待实施）

**位置**：`backend/docrestore/processing/code_assembly.py`

**输入**：`IDELayout`
**输出**：`list[CodeColumn]`，每个含 `code_text`（含缩进）+ 行号映射 + bbox

**关键步骤**：
1. **行号 vs 代码分离**：每栏内按 x1 二分——`x1 ≈ anchor.x1_center` 是行号 line，剩余是代码 line
2. **y 排序 + 行号配对**：同 y 区间内（容差 = avg_line_height/2）的行号与代码视为同一行
3. **缩进保留**：`(code_line.bbox.x1 - anchor.x2_max) / char_width` = 缩进字符数。`char_width` 从单字符 line 或 (x2-x1)/len(text) 估算
4. **缺行检测**：行号 num_range 跳号 → 标 `code.line_gap_at_<n>` + 占位注释

### 2.3 OCR pipeline 切换（AGE-55，待实施）

让 `OCRConfig` 支持 `paddle_pipeline: Literal["basic", "vl"]`：
- `vl`（默认）：PaddleOCR-VL（vllm-server 模式），文档场景沿用，输出 markdown
- `basic`：PP-OCRv5（DBNet+CRNN），IDE 代码场景，输出行级 rec_boxes 填充 `PageOCR.text_lines`

**改动点**：
- `OCRConfig.paddle_pipeline` 新字段
- `scripts/paddle_ocr_worker.py` init 分支选 `PaddleOCR(...)` 或 `PaddleOCRVL(...)`
- worker 处理 OCR 命令时，basic 模式额外 dump rec_boxes/rec_texts/rec_scores
- `PaddleOCREngine.ocr` 把 worker 返回的 lines 填入 `PageOCR.text_lines`
- `EngineManager`：basic 模式不需要拉 vllm-server，节省 GPU
- `CodeRestoreConfig.enable=True` 时自动 override `paddle_pipeline="basic"`

**MinerU 借鉴的初始化参数**（`scripts/age8_probe_basic_ocr.py` 已部分用上）：
```python
PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    det_db_box_thresh=0.3,        # 降低检测阈值，密集代码行
    det_db_unclip_ratio=1.8,      # 扩大检测框，行尾不丢字
    enable_merge_det_boxes=True,  # 合并重叠 boxes
)
```

### 2.4 沿用 v1 的下游模块（无需重构）
- **AGE-45 ide_meta_extract**：从 above_code 区域的 line 中正则提取 tab/breadcrumb（含 `.cc`/`.h` 等扩展名 + `>` 路径分隔）
- **AGE-46 code_file_grouping**：按文件路径跨张归类，同文件按 y 顺序拼接（输入从原"per-column OCR"改为 `IDELayout.columns + tab 提取的路径`）
- **AGE-47 code_renderer**：写 `output/<task>/files/<relative-path>` + `files-index.json`
- **AGE-48 CodeLLMRefiner**：字符级 OCR 修正白名单
- **AGE-49 编译验证**：`gcc -fsyntax-only` / `python -m py_compile`
- **AGE-50 前端原图 ↔ 还原代码对照**

---

## 3. 用户已确认的关键决策（v1/v2 通用）

| # | 决策 | 答案 | 实施位置 |
|---|---|---|---|
| 1 | 输出粒度 | 每文件独立源文件，验收要求能通过编译 | AGE-47 / AGE-49 |
| 2 | LLM 幻觉容忍度 | 字符级 OCR 修正白名单（O↔0 / l↔1 / I↔l / rn↔m / 全角→半角），禁语义改动 | AGE-48 |
| 3 | 多语言混排 | 按文件分组，同图分栏 ≠ 同文件 | AGE-46 强约束 |
| 4 | tab 不可读 fallback | 不用 EXIF 时间戳；OCR 拿不到 tab 发 quality 信号 `code.tab_unreadable` 人工补正 | AGE-45 |
| 5 | 多栏阅读顺序 | **新增**：禁止跨栏合并（业内 PP-StructureV3 / MinerU 默认行为正好相反，必须显式不用） | AGE-46 |

---

## 4. 配置入口（AGE-51 沿用，扩展）

```python
class IDEUIConfig(BaseModel):
    """v2 简化：只保留 column-content 阈值"""
    enable: bool = False

class CodeRestoreConfig(BaseModel):
    """AGE-8 IDE 代码照片还原"""
    enable: bool = False
    output_files_dir: str = "files"
    file_grouping_strategy: Literal["tab_breadcrumb", "content_only"] = "tab_breadcrumb"
    # v2 新增：自动选 OCR pipeline
    # 当 enable=True 时，自动覆盖 ocr.paddle_pipeline = "basic"

class OCRConfig(BaseModel):
    paddle_pipeline: Literal["basic", "vl"] = "vl"   # AGE-55
    ...

class PipelineConfig(BaseModel):
    code: CodeRestoreConfig = Field(default_factory=CodeRestoreConfig)
    ...
```

API 层：`POST /tasks` 加 `code: CodeRestoreConfig | None`，前端 TaskForm 新增"识别 IDE 代码"开关。

---

## 5. 测试策略

### 单测（每模块独立）
- ✅ `tests/processing/test_ide_layout.py`：32 测试通过（24 合成 + 8 spike fixture）
- ⏳ `tests/processing/test_code_assembly.py`：缩进保留 / 行号-代码配对 / 缺号检测
- ⏳ `tests/processing/test_ide_meta_extract.py`：tab/breadcrumb 正则
- ⏳ `tests/processing/test_code_file_grouping.py`：跨张归类 + 同名不同路径分组
- ⏳ `tests/llm/test_code_prompt.py`：白名单字符级 + 拒绝行数变化
- ⏳ `tests/output/test_code_renderer.py`：路径穿越防护 + index 字段完整

### 集成测试
- ⏳ `tests/pipeline/test_age8_e2e.py`：8 张 spike 子集端到端

### 实测验证（已完成）
- ✅ 8 张 spike：全部 2 anchor，mono 100%
- ✅ NAS 全 272 张：成功率 100%，平均最大单调性 1.0，唯一 weak_monotonic warning（DSC06875，含三位数行号 OCR 偶发噪声但仍可用）

---

## 6. 分阶段交付（v2 调整后）

### Phase 1：行级布局识别（在做）
- [x] **AGE-53** `ide_layout.py` ✅ 已实现 + 32 单测 + 272/272 全集验证
- [ ] **AGE-54** `code_assembly.py` 栏代码组装 + 缩进保留
- [ ] **AGE-55** OCR `basic`/`vl` pipeline 切换 + worker 改造

**Phase 1 验收**（v2 标准）：
- 全 272 张 spike：≥ 95% 检出至少 1 个 anchor → **已 100% 达成**
- 8 张端到端 → 输出每栏代码文本，缩进与原图肉眼一致 → 待 AGE-54 完成

### Phase 2：跨张归类 + 输出（5-7 天）
- AGE-45 / AGE-46 / AGE-47

**Phase 2 验收**：8 张 spike → ≥ 3 个独立源文件，路径符合 Chromium 源树。

### Phase 3：编译级精修（1-2 周）
- AGE-48 / AGE-49 / AGE-50

**Phase 3 验收**：≥ 3 个文件通过编译 + 5 个文件人工 diff 公开 Chromium 源 ≥ 80%。

---

## 7. 实测证据（完整）

### 7.1 8 张 spike 详细结果（`output/age8-line-layout/`）

| 图 | 总行数 | anchor 0 (x1, mono) | anchor 1 (x1, mono) | column 0/1 行数 | sidebar 类型 |
|---|---|---|---|---|---|
| DSC06835 | 135 | 185 (1.0) | 1720 (1.0) | 47/48 | 折叠 |
| DSC06836 | 159 | 185 (1.0) | 1712 (1.0) | 46/46 | 折叠 |
| DSC06837 | 147 | 197 (1.0) | 1723 (1.0) | 46/44 | 折叠 |
| DSC06838 | 203 | 1026 (1.0) | 1936 (1.0) | 46/54 | 展开 (EXPLORER) |
| DSC06839 | 217 | 1019 (1.0) | 1921 (1.0) | 51/59 | 展开 |
| DSC06840 | 190 | 1028 (1.0) | 1930 (1.0) | 51/41 | 展开 |
| DSC06841 | 134 | 177 (1.0) | 1701 (1.0) | 46/49 | 折叠 |
| DSC06842 | 139 | 170 (1.0) | 1686 (1.0) | 46/56 | 折叠 |

### 7.2 全 272 张统计（`output/age8-validate-full/summary.json`）

```json
{
  "total": 272,
  "success": 272,
  "success_rate": 1.0,
  "anchor_count_distribution": {"2": 272},
  "n_columns_distribution":      {"2": 272},
  "avg_max_monotonic": 1.0,
  "high_monotonic_count_geq_0.9": 272,
  "high_monotonic_rate_geq_0.9": 1.0,
  "flag_distribution": {"code.weak_monotonic": 1}
}
```

- **检出率 100%**（272/272 都识别出 2 个 anchor）
- **唯一 weak_monotonic**：DSC06875，右栏行号 211-320 含三位数，OCR 偶发噪声 mono=0.676，但左栏 mono=1.0 + 仍检出 2 anchor，整体可用
- **左栏代码行数**：avg 49.7（min 34 / max 66）
- **右栏代码行数**：avg 53.2（min 35 / max 72）
- **above_code（tab/menu）**：avg 10.0
- **below_code（terminal）**：avg 20.6
- **sidebar**：avg 1.7（max 41，EXPLORER 展开图）

### 7.3 业内对照
| 工具 | IDE 多栏处理 | 结果 |
|---|---|---|
| PaddleOCR-VL（默认）| layout 解析 | DSC06838 整图合并为单 content block |
| PaddleOCR-VL `merge_layout_blocks=False` | 关后处理合并 | 与默认无差异（参数对底层 layout 无效）|
| PP-DocBlockLayout 阈值 0.05~0.5 | "多栏文档子区域"模型 | 全部输出单一 Region 覆盖整图 |
| PP-StructureV3 + reading order | layout-first + pointer network | 跨栏合并阅读顺序（与需求相反）|
| MinerU pipeline backend | 同上 | 同上 |
| **行号列锚点（v2）** | 行级 OCR + 数据驱动聚类 | **272/272 = 100%** |

---

## 8. 不做的事（边界）

- 不还原 IDE 配置 / git diff / merge conflict / 图标字符画
- 不主动联网验证代码
- 不实施 EXIF 时间戳 fallback
- **不做"多栏阅读顺序合并"**（业内默认行为，与我们需求相反）
- 不识别 IDE 弹窗 / 命令面板 / 设置页（quality flag 标记跳过）

---

## 9. 风险与未知

| 风险 | 影响 | 应对 |
|---|---|---|
| VSCode hide line numbers（用户关行号） | 中 | quality flag `code.no_anchor`，整图归 sidebar 待人工补正；spike 273 张全开行号未触发 |
| 三位数以上行号 OCR 噪声 | 低 | 已实测：DSC06875 weak_monotonic 仅 warning 不阻断；可加 `code.line_gap_at_<n>` 注释占位（AGE-54）|
| 同名文件跨多个目录（多个 BUILD.gn） | 低 | breadcrumb 路径区分，组 ID = 完整路径（AGE-46）|
| LLM 字符级修正引入语义错（变量名 `O1` → `01`） | 中 | AGE-48 LLM 输出 changelog；编译失败回退到未精修版（AGE-49）|
| sidebar 文件树底部越界进 below_code | 低 | ide_layout 已修复：x < anchor[0].x1_min 优先归 sidebar |
| 全 272 张 anchor 数全 = 2 | 设计 | 数据集全是双栏拍摄；算法支持任意 N 栏（spike 测试 1/2/3 都通过）|

---

## 10. 引用

- 本文档：`docs/zh/backend/age-8-ide-code.md`
- 进度：`docs/progress.md`（2026-04-25 节）
- 模块：`backend/docrestore/processing/ide_layout.py`
- 单测：`tests/processing/test_ide_layout.py`
- 实测脚本：`scripts/age8_probe_basic_ocr.py` / `age8_analyze_line_layout.py` / `age8_validate_full_dataset.py`
- 数据：`output/age8-probe-basic/`（8 张 lines.jsonl）/ `output/age8-line-layout/`（spike 报告）/ `output/age8-validate-full/`（272 张完整统计）
