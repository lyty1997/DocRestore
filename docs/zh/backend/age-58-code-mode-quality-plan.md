# AGE-58 代码模式最终输出质量修复方案

> 历史路线记录：本文用于解释代码模式质量优化的阶段性来源。当前已落地能力以 `processing.md`、`api.md`、`data-models.md`、`../frontend/features.md` 和代码实现为准。

**状态**：方案草案
**关联**：AGE-58
**输入场景**：IDE 代码照片、截图翻拍、单栏/多栏编辑器、跨页重叠拍摄
**输出目标**：按源文件组织的可读、可审计、尽量可通过语法/编译检查的代码文件

## 1. 背景与判断修正

本方案基于内部 IDE 代码截图数据集与
`/tmp/docrestore_b5950355` 的抽样排查，但设计目标不是某一项目专用，也不限定
C/C++。

已确认的判断口径：
- 代码模式仍应依赖 `PageOCR.text_lines` 的行级 bbox 契约；没有行级 OCR 时明确失败是正确的。
- PP-OCR basic 对可见代码行可以提供有用行号和文本，但暗色主题、小字号、拍摄透视、低对比、语法高亮会带来稳定字符错误。
- 不能仅凭“多张图片归并为少量文件”判定分组错误；接近 3000 行的大文件本来就需要合并很多页。
- 真正需要判断的是路径候选置信度、行号区间连续性、column 来源、UI 噪声污染、语法/编译诊断和 LLM 截断状态。
- 参考源码只能是可选增强，不能把代码模式设计成 示例/C++ 专用恢复器。

现有方案方向是**刚刚好**：
- 不是过度工程：代码模式已经有独立的行号锚点、组装、路径提取、分组、渲染、LLM 修复模块；质量问题正好发生在这些边界之间，需要显式建模。
- 不是欠工程：如果只换 OCR、只调 prompt 或只靠人工 review，无法解决 UI 噪声、路径低置信、跨栏归并和 token 截断这些结构性问题。

## 2. 目标与非目标

目标：
- 提高多语言代码照片还原质量，不绑定 示例、VSCode 或 C/C++。
- 保留 `OCREngine` 抽象：任意 OCR 引擎只要填充 `PageOCR.text_lines` 即可接入。
- 把“是否可信”变成可观测数据，避免 `.quality_report.json` 空报告掩盖代码模式失败。
- 让文件分组基于 column 级 segment、路径置信度和行号连续性，而不是单一 filename fuzzy key。
- 把 LLM 修复从整文件大请求改为“确定性清理 + 诊断驱动小片段修复”。
- 可选接入用户提供的项目根目录或源码包，用作通用代码库上下文增强。

非目标：
- 不主动联网查找源码。
- 不在代码中写死任何 示例 路径、文件名、正文关键字或 C++ 专用规则作为通用逻辑。
- 不用固定像素比例裁剪 IDE UI；继续以行号列、bbox 和文本语义做数据驱动判断。
- 不允许 LLM 在缺少证据时创造业务逻辑；所有修复必须保留来源、范围和失败回退路径。

## 3. 总体修复路线

### 3.1 主线：不依赖参考源码

主线必须在用户没有源码库的情况下也能运行：

1. `PageOCR.text_lines`
2. `ide_layout.analyze_layout()` 识别行号锚点和 column
3. column 级 `CodeSegment` 建模
4. UI 噪声过滤与路径候选置信度计算
5. segment 分组为 `SourceFile`
6. 确定性 OCR 清理
7. 语言感知语法/编译诊断
8. 小片段 LLM 修复
9. 渲染 `files/`、`files-index.json`、代码质量报告

### 3.2 增强线：可选代码库上下文

增强线只在用户提供项目根目录、Git 工作树或参考源码包时启用：

- 通过文件列表、路径片段、符号名和代码片段做 fuzzy retrieval。
- 用候选源码校正路径、符号、import/include、缩进和重复片段。
- 仍以 OCR segment 为主输入，参考源码只是候选证据。
- 多语言支持依赖扩展名、shebang、项目文件和 parser/linter，不写死语言。

## 4. 数据模型补强

新增或扩展内部模型，不必一开始全部写入公开 API。

### 4.1 CodeSegment

每张图片的每个编辑器 column 生成一个 segment：

```python
class CodeSegment(BaseModel):
    page_stem: str
    column_index: int
    bbox: tuple[int, int, int, int]
    line_no_range: tuple[int, int]
    lines: list[CodeLine]
    path_candidates: list[PathCandidate]
    selected_path: str | None
    selected_path_confidence: float
    language: str | None
    flags: list[str]
```

关键点：
- segment 是分组前的最小可审计单位。
- `line_no_range` 来自代码行号，不从页数推断质量。
- path 可以为空；低置信路径不应强行覆盖高置信历史路径。

### 4.2 PathCandidate

```python
class PathCandidate(BaseModel):
    path: str | None
    filename: str | None
    language: str | None
    source: Literal["breadcrumb", "tab", "peer", "reference", "content"]
    confidence: float
    raw_text: str
    flags: list[str]
```

置信度来源：
- breadcrumb 命中完整路径，分隔符和扩展名可信：高。
- tab 兜底但无 breadcrumb：中低。
- 同图 peer 补全目录：中。
- reference context fuzzy match：视相似度决定，不能覆盖明确矛盾的 OCR 证据。
- 只有 filename 或路径段明显污染：低。

### 4.3 CodeQualityReport

代码模式质量报告至少包含：
- segment 数、source file 数、每文件来源页/column/行号区间。
- UI 噪声命中统计。
- 路径候选低置信和冲突。
- `code.refine.truncated`、`large_gap_collapsed`、`missing_line_nos` 等现有 flags。
- 语法/编译检查状态和失败行。
- LLM 小片段修复次数、失败次数、回退次数。

## 5. UI 噪声过滤

过滤目标不是“删掉所有看起来像 UI 的字符串”，而是基于几何和上下文判断文本是否属于代码行。

应过滤或降权：
- 顶栏、菜单、窗口标题、tab bar 噪声。
- breadcrumb 中的 symbol path 尾部噪声。
- 搜索框、命令面板、`Loading...`、Marketplace、Terminal、Problems 面板。
- status bar、git blame、提示 toast。

规则原则：
- 已经成功配对到行号的文本不能仅凭 denylist 删除；真实代码也可能包含普通英文字符串。
- 未配对行、bbox 在行号范围之外、y_center 在代码区外、或位于 overlay 小矩形内的文本才作为高风险噪声。
- 噪声处理结果要进 flags，例如 `code.ui_noise.filtered=...`、`code.overlay.search_box`、`code.overlay.loading`。

实现位置：
- 短期在 `code_assembly.py` 组装前后增加 `CodeNoiseFilter`。
- 中期把过滤结果作为 `CodeSegment.flags`，由 grouping 和 quality report 消费。

## 6. 路径提取与文件分组

### 6.1 路径提取

`ide_meta_extract.py` 保留 breadcrumb 优先的原则，但要输出候选集合和置信度，而不是单一 `IDEMeta.path`。

新增校验：
- 路径段不能包含明显 UI token、窗口标题、搜索框片段。
- 大小写错误可以降权但不直接失败，例如 `BUiLD.gn` 可作为 `BUILD.gn` 候选。
- 异常目录段、重复后缀、孤立符号段要保留 raw 证据并降权。
- 同图 peer 补全只在目录上下文一致时生效，不覆盖不同扩展名或不同行号连续性的 segment。

### 6.2 文件分组

`code_file_grouping.py` 应从 `PageColumn` 直接分组升级为 `CodeSegment` 分组。

合并条件：
- path candidate 兼容且置信度达到阈值。
- 行号区间重叠、相邻或 gap 可解释。
- 同一 page 的不同 column 默认独立；如果归到同一路径，要标记 `code.grouping.same_page_same_path` 并要求内容/行号证据支持。
- `merged_pages` 只作为规模信息，不作为失败条件。

风险信号：
- 低置信路径参与合并。
- path 频繁在相邻页跳变。
- 大 gap 被折叠。
- 同一行号有多个差异较大的 OCR 版本。
- 同一文件跨 column 来源异常切换。

## 7. 确定性 OCR 清理

在 LLM 之前做可解释、可测试、尽量保持行数的清理。

通用规则：
- 全角标点转半角。
- 常见不可见字符、中文噪声符号、孤立 OCR 残片清理。
- 行首 `1/`、`l/` 在注释上下文中修为 `//`。
- 成对引号、括号、方括号的小范围修复只在单行证据充分时进行。
- 不跨行重写业务逻辑。

语言感知规则：
- 用扩展名、shebang、项目文件判断语言。
- 每种语言维护 `comment_prefix`、parser/linter、formatter、常见 OCR 混淆规则。
- 规则必须有测试样例，不能把某个项目的符号名写成通用规则。

## 8. 语法/编译诊断

现有 `scripts/age8_compile_check.py` 可以作为起点，但 AGE-58 需要把诊断纳入 pipeline 或至少纳入代码模式后处理报告。

多语言诊断建议：

| 语言 | 低成本诊断 |
|---|---|
| Python | `python -m py_compile` |
| JavaScript/TypeScript | `node --check` / `tsc --noEmit`（有项目配置时） |
| C/C++ | `clang++ -fsyntax-only` / `gcc -fsyntax-only`，缺 sysroot 时降级为 syntax-only 风险报告 |
| Go | `go test` / `go test ./...`（有 module 时）或 `gofmt -w` 前 dry run |
| Rust | `cargo check`（有 Cargo.toml 时） |
| Java/Kotlin/Swift/Dart | 优先 parser/formatter，缺工具时标 `tool_unavailable` |
| JSON/YAML/TOML/XML | 标准解析器 |

诊断结果用于：
- 写入 `files-index.json` 和质量报告。
- 选择 LLM 修复窗口。
- 评估确定性清理是否改善或恶化。

## 9. LLM 小片段修复

当前整文件修复容易触发 `code.refine.truncated`。AGE-58 应改为分块策略：

- 按诊断失败行取窗口，例如失败行前后 10-30 行。
- 没有诊断工具时，按 flags 和 OCR 低置信行切片。
- 每个窗口附带文件路径、语言、相邻上下文、相关 path candidates 和 source pages。
- `refine` 模式默认保持行数；`rewrite` 模式只允许在小窗口内使用，并记录 line delta。
- LLM 输出必须能映射回原文件范围；解析失败、截断或恶化诊断时回退。

小片段修复不是“只给 LLM 小片段上下文”。正确边界是：
- **编辑范围小**：LLM 只能修改诊断窗口或低置信窗口，输出必须带原始行号范围。
- **只读上下文可以大**：允许附带同文件摘要、相关符号、相邻函数/类、import/include、调用点、编译错误链路和可选参考代码库候选。
- **上下文分层**：优先给局部窗口，其次给所在函数/类，再给同文件 outline，最后给跨文件检索结果；超过 token 预算时按诊断相关性裁剪。
- **关联修复分两步**：先让 LLM 给出修复计划和依赖判断，再让它只对明确窗口输出 patch；如果判断需要改多个窗口，生成多个 scoped patch，而不是整文件 rewrite。
- **跨窗口一致性**：同一符号或同一 OCR 混淆在多个窗口出现时，先汇总为候选规则，再逐窗口应用并用诊断验证。

需要新增 `CodeRepairContext`：

```python
class CodeRepairContext(BaseModel):
    file_path: str
    language: str | None
    edit_range: tuple[int, int]
    local_lines: list[NumberedCodeLine]
    enclosing_symbols: list[CodeSymbol]
    file_outline: list[CodeSymbol]
    diagnostics: list[CodeDiagnostic]
    related_snippets: list[CodeSnippetCandidate]
    path_candidates: list[PathCandidate]
    source_pages: list[str]
    constraints: list[str]
```

`related_snippets` 的来源可以是：
- 同文件相邻函数/类。
- 同项目同名符号或相似调用点。
- import/include 指向的文件片段。
- 可选 `CodeContextProvider` 检索结果。
- 同一 OCR 任务中重复出现的相似行。

安全约束：
- 低置信上下文只能作为提示，不可直接覆盖 OCR 文本。
- 关联修复必须能落回有限行号范围；无法定位范围时只报告 unresolved。
- 如果修复某个窗口导致新的诊断错误或破坏已通过窗口，应回退该窗口。
- 对业务逻辑缺失、语义不确定或多种修法都合理的情况，保留 `OCR-Q` / unresolved 标记，不强行猜。

验收要求：
- 大文件不再整文件一次性进入 LLM。
- `code.refine.truncated` 不应成为常态；若仍出现，必须定位到具体窗口并写入报告。
- 小片段修复能使用跨窗口/跨文件只读上下文，但每次实际改动都有明确行号范围和回退证据。

### 9.1 小段后全文件一致性修复

小段修复后可以再做一次全文件 pass，但它不应是“把完整文件交给 LLM
重新生成”。推荐定义为**全文件一致性审计 + 受限 patch**：

1. 小段修复先清掉局部语法破口，让 parser/linter 能尽量建立结构。
2. 重新运行诊断，得到剩余错误、warning 和跨窗口一致性问题。
3. 全文件 pass 读取全局上下文，但只能输出有限范围 patch 或 unresolved。
4. patch 应再次经过确定性应用、诊断验证和恶化回退。

全文件 pass 适合处理：
- 同一 OCR 混淆在多处出现，例如符号名、类型名、宏名大小写不一致。
- import/include 缺失、重复或路径 OCR 错误。
- 跨窗口括号、缩进、块结构、函数边界不一致。
- 局部修复后暴露的新诊断错误。
- 文件级命名风格和重复残片清理。

不适合处理：
- 缺少证据的业务逻辑补全。
- 大范围重排或重写算法。
- 无行号来源、无法映射回原图的整文件替换。

大文件组织方式：
- 文件较短且 token 预算充足时，可以给完整当前代码，但仍要求输出 patch。
- 文件较长时，给 file outline、所有诊断、已修复窗口摘要、低置信行列表、
  相关上下文片段和必要的 numbered excerpts，不发送完整文件。
- 超大文件可以做层级 pass：先按函数/类分块生成摘要和风险，再由全文件 pass
  只看摘要、诊断和候选 patch。

建议 prompt 结构：

```text
System:
你是代码 OCR 修复器。只能修复 OCR 造成的字符、标点、缩进、路径和结构错误。
不要发明业务逻辑。无法确定时输出 unresolved。
输出必须是 JSON，patch 必须绑定原始行号范围。

Task:
对当前文件做全文件一致性审计。你可以读取全局上下文，但只能修改 listed editable ranges。
优先修复诊断错误和跨窗口一致性问题。不要整文件重写。

File:
- path: ...
- language: ...
- source_pages: ...
- path_confidence: ...

Diagnostics:
...

Previous local repairs:
...

Global context:
- imports/includes:
- file outline:
- symbol table:
- repeated OCR confusions:
- unresolved items:

Editable ranges:
R1 lines 120-145 reason=syntax_error
R2 lines 780-792 reason=same_symbol_conflict

Read-only excerpts:
...

Required JSON:
{
  "plan": [{"issue": "...", "evidence": "...", "ranges": ["R1"]}],
  "patches": [
    {
      "range_id": "R1",
      "start_line": 120,
      "end_line": 145,
      "replacement": "...",
      "reason": "...",
      "confidence": 0.0
    }
  ],
  "unresolved": [{"line": 0, "reason": "...", "needed_evidence": "..."}]
}
```

组织原则：
- prompt 中明确区分 `editable ranges` 与 `read-only excerpts`。
- LLM 不能修改未列入 editable range 的行；如果发现问题，应返回新的候选 range，
  由调度器二次确认后再发起修复。
- 输出使用结构化 JSON patch，而不是完整文件文本；只有小文件可选完整文件输出，
  且仍需逐行 diff 和诊断验证。
- 全文 pass 的目标是“减少剩余诊断和一致性问题”，不是追求看似更漂亮的重写。

## 10. 可选代码库上下文层

新增可插拔接口：

```python
class CodeContextProvider(Protocol):
    def list_files(self) -> list[CodeContextFile]: ...
    def search_paths(self, query: str, language: str | None) -> list[PathCandidate]: ...
    def search_snippets(self, text: str, language: str | None) -> list[CodeSnippetCandidate]: ...
```

输入来源：
- 用户指定项目根目录。
- 用户上传参考源码包。
- 已存在 Git 工作树。

使用边界：
- 默认关闭。
- 不联网。
- 不写死项目名、路径、文件名或语言。
- 只作为候选证据；最终仍要通过 OCR provenance、路径置信度和语法诊断。

## 11. 分阶段实施

### Phase 0：质量可观测性

改动：
- 把代码模式 flags 汇总进 `.quality_report.json`。
- 让 `files-index.json` 暴露 path confidence、segment 来源、refine 截断、diagnostics。
- 增加 UI 噪声扫描报告。

验收：
- `/tmp/docrestore_b5950355` 这类产物不能再出现空质量报告。
- `code.refine.truncated`、`large_gap_collapsed`、低置信路径必须可见。

### Phase 1：segment 与路径置信度

改动：
- 引入 `CodeSegment` / `PathCandidate`。
- `ide_meta_extract.py` 输出候选和置信度。
- `code_file_grouping.py` 基于 segment 合并。

验收：
- 大文件允许合并很多页，但必须能解释每个 segment 的路径来源和行号区间。
- 低置信 header 不再覆盖高置信路径。

### Phase 2：UI 噪声过滤与确定性清理

改动：
- 增加 `CodeNoiseFilter`。
- 增加语言注册表和确定性 OCR 清理规则。

验收：
- 搜索框、Terminal、Marketplace、`Loading...` 等 UI 文本不进入最终代码，或被明确标记为不可确认片段。
- 清理规则保持行数，且单测覆盖多语言样例。

### Phase 3：诊断驱动修复

改动：
- 把语法/编译诊断接入代码模式报告。
- LLM 改为窗口级修复。

验收：
- 长文件不再整文件 refine。
- 修复前后诊断差异可追踪，恶化时回退。

### Phase 4：二次裁剪 OCR

改动：
- 先用整图 OCR 找行号锚点，再按 column bbox 裁剪、增强、放大后重 OCR。
- 保留 crop 坐标映射，保证 bbox 能回到原图。

验收：
- 字符级错误率下降，尤其是标点、下划线、大小写和注释符。
- 不引入固定 IDE 像素比例。

### Phase 5：可选代码库上下文

改动：
- 实现 `CodeContextProvider`。
- 路径和片段检索接入 PathCandidate 与 LLM 修复窗口。

验收：
- 无参考源码时主线照常工作。
- 有参考源码时路径和片段修复质量提升，但所有引用都有来源记录。

## 12. 测试策略

单元测试：
- `test_ide_meta_extract.py`：候选路径、置信度、breadcrumb/tab 冲突。
- `test_code_file_grouping.py`：大文件多页合并、低置信路径隔离、同页多 column 冲突。
- `test_code_noise_filter.py`：搜索框、Terminal、Loading、Marketplace、status bar。
- `test_ocr_postfix.py`：通用和语言感知 OCR 清理。
- `test_code_refine.py`：窗口级 LLM、截断回退、诊断恶化回退。

集成测试：
- 示例_VDA 抽样：双栏、大文件、遮罩、搜索框。
- TMedia 抽样：单栏/双栏混合。
- 多语言合成 fixture：Python、TypeScript、Go、Rust、JSON/YAML。
- 文档照片对照：无行号时不误入代码模式。

验收指标：
- 质量报告非空且能解释风险。
- UI 噪声进入最终代码的数量下降到可审计范围。
- `code.refine.truncated` 不再按文件级普遍出现。
- 语法/编译失败行能在前端定位到来源页。
- 多语言 fixture 不依赖 C/C++ 专用逻辑。

## 13. 推荐优先级

优先做 Phase 0-2。原因：
- 当前最大风险不是“大文件合并页数多”，而是低置信路径、UI 噪声和质量报告不可见。
- 没有 segment 和质量报告，后续 LLM、二次 OCR、参考源码匹配都缺少可靠反馈。
- Phase 0-2 不依赖外部工具齐全，回归成本最低，能最快改善用户 review 体验。

Phase 3 之后再做 LLM 和诊断；Phase 4/5 作为质量上限增强，不应阻塞主线落地。

## 14. Linear 子 issue 拆分

AGE-58 已拆成以下 child issues：

| Issue | 主题 | 依赖 |
|---|---|---|
| AGE-61 | 代码模式质量报告汇总代码 flags 与诊断风险 | - |
| AGE-62 | 引入 `CodeSegment` 与 `PathCandidate` 建模代码来源置信度 | AGE-61 |
| AGE-63 | 按 `CodeSegment` 重写代码文件分组与路径置信合并 | AGE-62 |
| AGE-64 | 过滤 IDE UI 噪声并实现确定性 OCR 清理规则 | AGE-62 |
| AGE-65 | 接入多语言语法诊断并回填代码索引 | AGE-61, AGE-64 |
| AGE-66 | 实现诊断驱动的 LLM 小窗口修复与 `CodeRepairContext` | AGE-62, AGE-65 |
| AGE-67 | 实现小段修复后的全文件一致性审计 pass | AGE-66 |
| AGE-68 | 代码模式按 column 裁剪增强后进行二次 OCR | AGE-62, AGE-64 |
| AGE-69 | 实现可选 `CodeContextProvider` 作为通用代码库上下文层 | AGE-62, AGE-66 |

推荐执行顺序：
- 主线最短闭环：AGE-61 → AGE-62 → AGE-64 → AGE-65 → AGE-66。
- 分组质量线：AGE-62 → AGE-63。
- 全局一致性修复：AGE-66 → AGE-67。
- OCR 质量增强：AGE-62 / AGE-64 → AGE-68。
- 可选代码库上下文增强：AGE-62 / AGE-66 → AGE-69。
