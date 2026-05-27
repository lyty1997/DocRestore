# AGE-8 Design: IDE Code Photos -> Source File Reconstruction

**Status**: v2 design Phase 1.2 landed (AGE-53 done), the rest of Phase 1 in progress
**Linear**: [AGE-8](https://linear.app/axiom-mind/issue/AGE-8/ide-代码照片-源文件还原)
**Input**: `test_images/ide_code_sample/` (full 272-image NAS set, VSCode dark theme)
**Expected output**: one `.cc`/`.h`/`.gn`/`.py`/... source file per file + `files-index.json`

---

## 0. Design Pivot Record (v1 -> v2, 2026-04-25)

### v1 (deprecated)
Based on "pixel-variance geometric detection to strip IDE UI + multi-column slicing". Cancelled: AGE-41 (ide_ui_strip) / AGE-42 (code_columns) / AGE-43 (preview CLI) / AGE-44 (per-column OCR).

**Root cause of failure**:
- Assumed sidebar/tab/terminal occupy fixed proportional regions of the image. **Real-world IDEs are arbitrarily draggable** — split-pane widths can be dragged, sidebars collapse/expand, fonts are zoomable, screen resolutions vary, **and every fixed threshold breaks**.
- Empirical 8-image spike: 7/8 sidebar fallback, 1/8 column fallback hard-cut, results unusable.

### Alternative directions investigated (all unusable)
- **PaddleOCR-VL `merge_layout_blocks=False`**: empirically ineffective — for complex IDE inputs the VL layout model directly emits a single content block, which is the model's capability ceiling
- **PP-DocBlockLayout with threshold lowered to 0.05**: always emits a single Region covering the whole image (out of training distribution)
- **Reading-order pointer network of PP-StructureV3 / MinerU**: direction is **exactly opposite** — they optimise "multi-column paper -> single-column reading order", and would incorrectly merge multi-column code

### v2 (current): line-number column anchoring
**Core idea**: use the IDE editor's intrinsic invariant — the **line-number column** — as a layout anchor.
- **8-image spike**: 100% detected 2 anchors, monotonicity 100%
- **Full 272-image NAS dataset**: 100% success rate (see §7)

---

## 1. Overall Architecture (v2)

```
[full IDE screenshot]
   ↓
[PaddleOCR PP-OCRv5 (basic pipeline, not VL)]
   ↓ line-level rec_boxes + texts + scores
[list[TextLine]]
   ↓
[ide_layout.analyze_layout]   ← AGE-53 ✅ landed
   ├─ filter line numbers: text=^\d{1,4}$ + score≥0.8
   ├─ x1 clustering (bandwidth=20)
   ├─ monotonicity filter (≥60% ascending pairs) → LineNumberAnchor list
   └─ region classification: column_i / above_code / sidebar / below_code
   ↓
[code_assembly.assemble_columns]   ← AGE-54 to be implemented
   ├─ separate line numbers vs code lines
   ├─ y-sort + line-number pairing
   └─ indent preservation (estimated by code character width)
   ↓
[ide_meta_extract]   ← AGE-45 (reused from v1 design)
   extract filename + path from tab/breadcrumb
   ↓
[code_file_grouping]  ← AGE-46 (reused, input switched to column text)
   group across images by file path, concatenate same-file pieces in y order
   ↓
[CodeLLMRefiner (CODE_REFINE_SYSTEM_PROMPT)]   ← AGE-48
   character-level OCR correction (whitelist), no semantic edits
   ↓
[code_renderer]   ← AGE-47
   write to output/<task>/files/<relative-path> + files-index.json
```

---

## 2. Key Module Design

### 2.1 ide_layout (AGE-53, done — implemented + 8/8 spike + 272/272 full-set validation)

**Location**: `backend/docrestore/processing/ide_layout.py`

**Core data classes**:
```python
@dataclass
class TextLine:                      # in models.py
    bbox: tuple[int, int, int, int]
    text: str
    score: float

@dataclass
class LineNumberAnchor:
    x1_center: int
    x1_min: int
    x2_max: int                      # code area start = anchor.x2_max
    y_top: int
    y_bottom: int
    line_count: int
    num_range: tuple[int, int]
    monotonic_ratio: float

@dataclass
class IDELayout:
    anchors: list[LineNumberAnchor]
    columns: list[list[TextLine]]    # text lines within each column
    above_code: list[TextLine]
    below_code: list[TextLine]
    sidebar: list[TextLine]
    other: list[TextLine]
    flags: list[str]
```

**Algorithm**:
1. Filter "line-number lines": `text strictly matches ^\d{1,4}$ + score ≥ 0.8`
2. Fine-grained clustering by x1 (bandwidth=20px, line-number columns are tightly aligned)
3. For each cluster, if `≥ 5 lines` and `ascending-pair ratio ≥ 0.6` → one valid anchor
4. Number of columns = number of anchors (adapts to any N; 1/2/3 columns all empirically work)
5. Region classification decision tree (in priority order):
   - `y_max < min(anchor.y_top)` → above_code
   - `x_max < anchor[0].x1_min` → sidebar (regardless of y; covers the case where the bottom of the sidebar file tree overflows)
   - `y_min > max(anchor.y_bottom)` → below_code
   - `x_min ∈ [anchor_i.x1_min, anchor_{i+1}.x1_min)` → column_i
   - otherwise → other

**Why it is robust** (intrinsic invariants of the IDE editor):
- **Font/zoom independent**: line-number bbox tolerance is relative to width; larger font yields a larger bbox
- **Pane drag independent**: column boundaries are fully derived from neighbouring anchor x positions
- **Sidebar collapse/expand independent**: sidebar = everything left of the leftmost anchor
- **Screen resolution independent**: no absolute pixel thresholds at all
- **Arbitrary column count**: number of anchors = number of columns

**Quality flags**:
- `code.no_anchor`: not detected (VSCode hide line numbers / OCR fully failed)
- `code.single_anchor` / `code.three_plus_anchors`: column-count hint
- `code.weak_monotonic`: anchor monotonic ratio < 0.8 (line-number OCR noise)

### 2.2 code_assembly (AGE-54, to be implemented)

**Location**: `backend/docrestore/processing/code_assembly.py`

**Input**: `IDELayout`
**Output**: `list[CodeColumn]`, each containing `code_text` (with indentation) + line-number mapping + bbox

**Key steps**:
1. **Line-number vs code separation**: within each column, binary-split by x1 — `x1 ≈ anchor.x1_center` are line-number lines, the rest are code lines
2. **y-sort + line-number pairing**: line numbers and code lines within the same y range (tolerance = avg_line_height/2) are treated as the same logical line
3. **Indent preservation**: `(code_line.bbox.x1 - anchor.x2_max) / char_width` = number of indent characters. `char_width` is estimated from single-character lines or `(x2-x1)/len(text)`
4. **Missing-line detection**: gaps in line-number num_range → flag `code.line_gap_at_<n>` + placeholder comment

### 2.3 OCR pipeline switch (AGE-55, to be implemented)

Make `OCRConfig` support `paddle_pipeline: Literal["basic", "vl"]`:
- `vl` (default): PaddleOCR-VL (vllm-server mode), reused for the document scenario, outputs markdown
- `basic`: PP-OCRv5 (DBNet+CRNN), used in the IDE code scenario, outputs line-level rec_boxes that populate `PageOCR.text_lines`

**Change points**:
- new field `OCRConfig.paddle_pipeline`
- `scripts/paddle_ocr_worker.py` init branches between `PaddleOCR(...)` and `PaddleOCRVL(...)`
- when handling OCR commands, in basic mode the worker additionally dumps rec_boxes/rec_texts/rec_scores
- `PaddleOCREngine.ocr` populates `PageOCR.text_lines` from the lines returned by the worker
- `EngineManager`: basic mode does not need to spin up vllm-server, saving GPU
- when `CodeRestoreConfig.enable=True`, automatically override `paddle_pipeline="basic"`

**Initialisation parameters borrowed from MinerU** (already partially used in `scripts/age8_probe_basic_ocr.py`):
```python
PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    det_db_box_thresh=0.3,        # lower detection threshold for dense code lines
    det_db_unclip_ratio=1.8,      # enlarge detection boxes so end-of-line characters are not lost
    enable_merge_det_boxes=True,  # merge overlapping boxes
)
```

### 2.4 Downstream modules reused from v1 (no refactor needed)
- **AGE-45 ide_meta_extract**: regex-extract tab/breadcrumb (containing `.cc`/`.h` etc. extensions + `>` path separators) from lines in the above_code region
- **AGE-46 code_file_grouping**: group across images by file path, concatenate same-file pieces in y order (input changed from the original "per-column OCR" to `IDELayout.columns + paths extracted from tabs`)
- **AGE-47 code_renderer**: write `output/<task>/files/<relative-path>` + `files-index.json`
- **AGE-48 CodeLLMRefiner**: character-level OCR correction whitelist
- **AGE-49 compile validation**: `gcc -fsyntax-only` / `python -m py_compile`
- **AGE-50 frontend original-image <-> reconstructed-code side-by-side**

---

## 3. Key Decisions Already Confirmed by the User (applies to both v1 and v2)

| # | Decision | Answer | Implementation site |
|---|---|---|---|
| 1 | Output granularity | one independent source file per file; acceptance requires it to compile | AGE-47 / AGE-49 |
| 2 | LLM hallucination tolerance | character-level OCR correction whitelist (O<->0 / l<->1 / I<->l / rn<->m / fullwidth->halfwidth), no semantic edits | AGE-48 |
| 3 | Mixed languages | grouped by file; same image with multiple columns != same file | AGE-46 hard constraint |
| 4 | Tab unreadable fallback | do not use EXIF timestamps; if OCR cannot read the tab, emit quality signal `code.tab_unreadable` for manual fixup | AGE-45 |
| 5 | Multi-column reading order | **new**: forbid cross-column merging (the industry default in PP-StructureV3 / MinerU is exactly the opposite, so it must be explicitly disabled) | AGE-46 |

---

## 4. Configuration Entry Point (AGE-51 reused, extended)

```python
class IDEUIConfig(BaseModel):
    """v2 simplification: keep only the column-content threshold"""
    enable: bool = False

class CodeRestoreConfig(BaseModel):
    """AGE-8 IDE code photo reconstruction"""
    enable: bool = False
    output_files_dir: str = "files"
    file_grouping_strategy: Literal["tab_breadcrumb", "content_only"] = "tab_breadcrumb"
    # v2 addition: auto-select OCR pipeline
    # when enable=True, automatically override ocr.paddle_pipeline = "basic"

class OCRConfig(BaseModel):
    paddle_pipeline: Literal["basic", "vl"] = "vl"   # AGE-55
    ...

class PipelineConfig(BaseModel):
    code: CodeRestoreConfig = Field(default_factory=CodeRestoreConfig)
    ...
```

API layer: `POST /tasks` adds `code: CodeRestoreConfig | None`; the frontend TaskForm adds an "Recognise IDE code" toggle.

---

## 5. Test Strategy

### Unit tests (per module, independent)
- ✅ `tests/processing/test_ide_layout.py`: 32 tests passing (24 synthetic + 8 spike fixtures)
- ⏳ `tests/processing/test_code_assembly.py`: indent preservation / line-number-code pairing / missing-number detection
- ⏳ `tests/processing/test_ide_meta_extract.py`: tab/breadcrumb regex
- ⏳ `tests/processing/test_code_file_grouping.py`: cross-image grouping + same-name different-path grouping
- ⏳ `tests/llm/test_code_prompt.py`: whitelist character-level + reject line-count changes
- ⏳ `tests/output/test_code_renderer.py`: path-traversal protection + index field completeness

### Integration tests
- ⏳ `tests/pipeline/test_age8_e2e.py`: end-to-end on the 8-image spike subset

### Empirical validation (completed)
- ✅ 8 spike images: all detect 2 anchors, mono 100%
- ✅ Full 272-image NAS set: success rate 100%, average max monotonicity 1.0, only one weak_monotonic warning (page06875, contains three-digit line numbers with sporadic OCR noise but still usable)

---

## 6. Phased Delivery (after v2 adjustment)

### Phase 1: line-level layout recognition (in progress)
- [x] **AGE-53** `ide_layout.py` ✅ implemented + 32 unit tests + 272/272 full-set validation
- [ ] **AGE-54** `code_assembly.py` column code assembly + indent preservation
- [ ] **AGE-55** OCR `basic`/`vl` pipeline switching + worker rework

**Phase 1 acceptance** (v2 standard):
- Full 272 spike images: ≥ 95% detect at least 1 anchor → **already 100% achieved**
- 8-image end-to-end → output per-column code text, indentation visually identical to the original → pending AGE-54 completion

### Phase 2: cross-image grouping + output (5-7 days)
- AGE-45 / AGE-46 / AGE-47

**Phase 2 acceptance**: 8 spike images → ≥ 3 independent source files, paths matching the source tree.

### Phase 3: compile-grade refinement (1-2 weeks)
- AGE-48 / AGE-49 / AGE-50

**Phase 3 acceptance**: ≥ 3 files compile + manual diff of 5 files against the public sample source ≥ 80%.

---

## 7. Empirical Evidence (full)

### 7.1 Detailed results on the 8 spike images (`output/age8-line-layout/`)

| Image | total lines | anchor 0 (x1, mono) | anchor 1 (x1, mono) | column 0/1 line count | sidebar type |
|---|---|---|---|---|---|
| page06835 | 135 | 185 (1.0) | 1720 (1.0) | 47/48 | collapsed |
| page06836 | 159 | 185 (1.0) | 1712 (1.0) | 46/46 | collapsed |
| page06837 | 147 | 197 (1.0) | 1723 (1.0) | 46/44 | collapsed |
| page06838 | 203 | 1026 (1.0) | 1936 (1.0) | 46/54 | expanded (EXPLORER) |
| page06839 | 217 | 1019 (1.0) | 1921 (1.0) | 51/59 | expanded |
| page06840 | 190 | 1028 (1.0) | 1930 (1.0) | 51/41 | expanded |
| page06841 | 134 | 177 (1.0) | 1701 (1.0) | 46/49 | collapsed |
| page06842 | 139 | 170 (1.0) | 1686 (1.0) | 46/56 | collapsed |

### 7.2 Statistics over the full 272 images (`output/age8-validate-full/summary.json`)

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

- **Detection rate 100%** (272/272 all detect 2 anchors)
- **Only weak_monotonic case**: page06875, right column line numbers 211-320 contain three digits, sporadic OCR noise gave mono=0.676, but the left column had mono=1.0 and 2 anchors were still detected, so it remains usable overall
- **Left-column code line count**: avg 49.7 (min 34 / max 66)
- **Right-column code line count**: avg 53.2 (min 35 / max 72)
- **above_code (tab/menu)**: avg 10.0
- **below_code (terminal)**: avg 20.6
- **sidebar**: avg 1.7 (max 41, EXPLORER expanded image)

### 7.3 Industry comparison
| Tool | IDE multi-column handling | Result |
|---|---|---|
| PaddleOCR-VL (default) | layout parsing | page06838 entire image merged into a single content block |
| PaddleOCR-VL `merge_layout_blocks=False` | disable post-merging | no difference vs default (the parameter does not affect the underlying layout) |
| PP-DocBlockLayout threshold 0.05~0.5 | "multi-column document sub-region" model | always emits a single Region covering the whole image |
| PP-StructureV3 + reading order | layout-first + pointer network | merges reading order across columns (opposite of our requirement) |
| MinerU pipeline backend | same as above | same as above |
| **Line-number column anchoring (v2)** | line-level OCR + data-driven clustering | **272/272 = 100%** |

---

## 8. Out of Scope (boundaries)

- Do not reconstruct IDE settings / git diff / merge conflict / icon ASCII art
- Do not actively go online to verify code
- Do not implement an EXIF timestamp fallback
- **Do not perform "multi-column reading-order merging"** (industry default behaviour, opposite of our requirement)
- Do not recognise IDE popups / command palette / settings pages (mark with quality flag and skip)

---

## 9. Risks & Unknowns

| Risk | Impact | Mitigation |
|---|---|---|
| VSCode hide line numbers (user disables line numbers) | medium | quality flag `code.no_anchor`, whole image classified as sidebar pending manual fixup; the 273-image spike all kept line numbers on and never triggered this |
| OCR noise on line numbers with three or more digits | low | empirically verified: page06875 weak_monotonic is only a warning, not a blocker; can add `code.line_gap_at_<n>` placeholder comments (AGE-54) |
| Same filename across multiple directories (multiple BUILD.gn) | low | breadcrumb path disambiguates; group ID = full path (AGE-46) |
| LLM character-level correction introduces semantic errors (variable name `O1` -> `01`) | medium | AGE-48 LLM emits a changelog; on compile failure fall back to the un-refined version (AGE-49) |
| Bottom of sidebar file tree overflowing into below_code | low | already fixed in ide_layout: x < anchor[0].x1_min is classified as sidebar with priority |
| All 272 images have anchor count = 2 | by design | the dataset is entirely two-column captures; the algorithm supports any N columns (spike tests pass with 1/2/3) |

---

## 10. References

- This document: `docs/zh/backend/age-8-ide-code.md`
- Progress: `docs/progress.md` (2026-04-25 section)
- Module: `backend/docrestore/processing/ide_layout.py`
- Unit tests: `tests/processing/test_ide_layout.py`
- Empirical scripts: `scripts/age8_probe_basic_ocr.py` / `age8_analyze_line_layout.py` / `age8_validate_full_dataset.py`
- Data: `output/age8-probe-basic/` (8 lines.jsonl) / `output/age8-line-layout/` (spike report) / `output/age8-validate-full/` (full 272-image statistics)

