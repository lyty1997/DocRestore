# PaddleOCR 性能对比实验结果

> 基准时间：2026-04-27（benchmark 数据）
> 硬件：RTX 4070 SUPER (12GB) + NVIDIA A2 (16GB)；benchmark 在 4070S 上跑，被另一进程占 7.85GB（剩 ~4GB 可用）
> 测试图：`test_images/crop_compare/AI子系统*/Linux_AI子系统_开发指南/` 22 张屏幕拍摄文档照片，
> 提供「未裁剪原图（3488×2624）」和「裁剪到文档主体（1776×1723）」两组

---

## ⚡ 0. 你应该先看这里（2026-04-27 之后的状态）

**结论一句话**：PicoDet-S 主区域检测器 + PP-StructureV3 light 端到端通路已经搭好，**不再需要前端强制裁剪**。本文件下方第三节"中期 ColumnFilter 重标"方案已废弃（与"禁用固定几何阈值"原则冲突）。

**新一张图来了，按这棵决策树走：**

```
原图（带 IDE/PDF 阅读器侧栏、Chrome 标签）
    │
    ├─ 主区域已经在外部裁好（你给的就是单文档区域）
    │     → 直接 PP-StructureV3 light（本文第二节实验 1，0.45-1.15 s/张）
    │
    └─ 是带 UI/侧栏的屏幕拍摄
          → 走 paddle_sv3_worker.py：
            PicoDet-S(main_doc, 单类) → 裁出主区域 → PP-StructureV3 light → markdown
            v0 模型 best score 0.79（小数据集），目录 main_doc_train/output/best_model/
```

**关键产物入口（按"我现在要干什么"组织）**

| 我要做的事 | 去这里 |
|---|---|
| 把 worker 接入 docrestore backend | `scripts/paddle_sv3_worker.py` + `test_images/dococr_bench/main_doc_train/INTEGRATION.md` |
| 训练 / 重训 PicoDet 主区域检测器 | `test_images/dococr_bench/main_doc_train/README.md`（8 阶段流程） |
| 半自动标注（已有原图+裁剪图配对） | `main_doc_train/tools/crop_to_anylabeling.py`（SIFT+RANSAC） |
| 半自动标注（只有原图，用 v0 预测） | `main_doc_train/tools/predict_to_anylabeling.py` |
| 装 PaddleX/PaddleDetection 报错排查 | `main_doc_train/LESSONS_LEARNED.md`（11 条踩坑） |
| 看本次 benchmark 原始数据 | 本文第二、四节 |

**已废弃的下方内容**：
- 第三节"中期 - 原图自动裁剪 / ColumnFilterThresholds 重标"（被 PicoDet 路径替代，且违反"禁用几何阈值"原则）
- 第二节实验 3 ColumnFilter 复用方案（同上）
- 第三节"短期 - 在原 worker 里改 PaddleOCRVL → sv3-light"伪代码（实际已封装为独立的 `paddle_sv3_worker.py`，不要照伪代码动 `paddle_ocr_worker.py`）

---

## 一、关键结论

| 方案 | 输入 | 单图耗时 | 吞吐 | 质量 | 显存 |
|------|------|----------|------|------|------|
| **PaddleOCRVL（旧 baseline）** | 任意 | 1.78 s/张（已优化版，progress.md 记录） | 0.56 img/s | 端到端 markdown，强 | ~8 GB（vLLM KV cache） |
| **PicoDet-S(main_doc) + PP-StructureV3 light** ✅ 新基线 | 原图 | ~1.07 s/张（含检测+裁剪+OCR） | ~0.93 img/s | 接近 VLM，主体正文/标题/代码可读 | ~3.5 GB |
| PP-StructureV3 light + 裁剪图（人工预裁） | 裁剪图 | 0.45–1.15 s（avg 0.75 s） | 1.34 img/s | 同上 | ~3 GB |
| PP-StructureV3 light + 原图 | 原图 | 0.98 s | 1.0 img/s | ❌ 侧栏目录混入正文，无法用 | ~3 GB |
| PP-StructureV3 region（PP-DocBlockLayout） | 原图 | 1.04 s | 0.96 img/s | ❌ 同上，PP-DocBlockLayout 是「多栏文章子区域检测」，不是「文档 vs UI」 | ~3 GB |
| ~~PP-StructureV3 light + 原图 → ColumnFilter → 重跑~~ | 原图 | ~~1.0–1.9 s~~ | ~~0.6–1.0 img/s~~ | ❌ 已废弃：违反"禁用几何阈值"原则 | — |
| 纯 PaddleOCR（PP-OCRv5 det+rec） | 任意 | 0.5 s | 2.0 img/s | 仅文本框，无 markdown 结构 | ~2 GB |

**当前首选**：`PicoDet-S(main_doc) + PP-StructureV3 light`（封装在 `scripts/paddle_sv3_worker.py`），可吃原图、自动裁主区域，相比 PaddleOCRVL 仍快 ~1.7×、显存降 ~55%。
**质量上限**：如果输入已经是干净裁剪图，直跑 sv3-light 速度会更快（0.75 s/张），但需要前端/上游保证裁剪。

---

## 二、实验细节

### 实验 1：PP-StructureV3 light（关闭 table/formula/chart/seal/region）+ 裁剪图

22 张全跑通：
```
min   = 0.45 s
mean  = 0.75 s
max   = 1.15 s
init  = 5.8 s（首次冷启动 ~317 s 下载模型）
```

API 调用：
```python
PPStructureV3(
    use_table_recognition=False,
    use_formula_recognition=False,
    use_chart_recognition=False,
    use_seal_recognition=False,
    use_region_detection=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
)
pipeline.predict(
    img_path,
    text_det_limit_side_len=1600,  # 抗 OOM 关键参数
    text_det_limit_type="max",
)
```

`text_det_limit_side_len=1600 + max` 把最长边限制到 1600 px，OCR 检测的 feature map 显存从 2.2 GB 降到 ~600 MB，裁剪图（1776×1723）会被等比缩到 1600×1551。

抽样 markdown（`out/sv3-light/AI子系统-裁剪/.../DSC07965.md`）：
```markdown
## TH1520_Linux_AI子系统_开发指南
## 版本历史
<img src="imgs/img_in_table_box_..." />
## 参考文档
<img src="imgs/img_in_table_box_..." />
## 概述
TH1520的AI子系统相关的HHB和SHL均已开源在github：
•HHB:https://github.com/T-head-Semi/tvm
•SHL:https://github.com/T-head-Semi/csi-nn2
...
```
正文标题/段落/列表/链接均正确还原。

### 实验 2：原图直接跑 sv3-light 失败

肉眼对比 `out/sv3-light/AI子系统/.../DSC07965/DSC07965_layout_det_res.JPG`：
- 左侧 Wiki 目录树（深灰背景）→ 被识别为多个 `text` 0.43–0.51
- 右侧"大纲"侧栏 → 被识别为 `paragraph_title` 0.33
- 顶部 Chrome 标签栏 → 被识别为 `text` 0.44

**根因**：PP-DocLayout_plus-L 训练数据是论文/报告/书籍/试卷，不包含「软件 UI 截图」语料。
PaddleOCR 默认 `markdown_ignore_labels=['number','footnote','header','header_image','footer','footer_image','aside_text']`
会过滤侧栏类标签，但模型把侧栏误打成了正文 `text` / `paragraph_title`，过滤无效。

PP-DocBlockLayout（`use_region_detection=True`）也救不了 —— 它检测的是「多栏文章中每个子文章的区域」（报纸/杂志），不是「文档主体 vs UI 元素」。

### 实验 3：ColumnFilter 复用方案 ❌ 已废弃

> 这条路 04-27 之后被否决：用户明确反馈"不能用几何方法识别和裁剪 UI、侧边栏，因为字体/焦段/侧边栏宽度都会改变阈值"。后续 DBSCAN 密度聚类也验证为结构性失败（侧边栏目录密度等级与正文相同）。当前走 PicoDet 路径替代。下方原始记录留作"踩过的坑"参考，不再迭代。

复用 `backend/docrestore/ocr/column_filter.py`（原本给 DeepSeek-OCR-2 写的列检测）。在 sv3-light 输出的 `parsing_res_list` 上做后处理：

5 张原图测试，仅 1 张（DSC07965）触发自动裁剪（L=222, R=999 —— 只检出左侧栏）。
其余 4 张 ColumnFilter 都返回 `has_sidebar=False`，原因：
- `right_candidate_min_x1=800` / `right_candidate_max_width=200` 阈值是为 DeepSeek-OCR 的归一化坐标调过的
- PP-StructureV3 给侧栏返回的 box 比 DeepSeek-OCR grounding 框更大（layout 块级 vs 文本行级），不满足 `max_width=200` 约束

### 实验 4：DBSCAN 密度聚类预裁主区域 ❌ 已废弃

后续也试过把 OCR 文本框的中心点做 DBSCAN（自适应 k-distance eps），按聚类得分挑主区域。试了 `sum_box_area` / `mean_area*size` / 列宽 CV / 行间距方差 多种评分，**所有变体都会在某些图上把侧边栏目录评成主区域**——目录每行短而密，密度量级 ≥ 正文。**结构性失败，不是调参能救的**。废弃记录见 `scripts/precrop_main_doc.py` 注释 + `main_doc_train/LESSONS_LEARNED.md` S1-5。

---

## 三、产线集成路径（已落地版）

> 04-27 原文有"短期 / 中期 / 长期"三段，现在的实际进展是：原"长期"PicoDet 方案被提前到主路径并已实装；原"中期" ColumnFilter 阈值重标弃用；原"短期"伪代码不要再照抄到 `paddle_ocr_worker.py`，改用独立的 `paddle_sv3_worker.py`。下面给现状版。

### 当前推荐：drop-in 切换 worker

直接用 `scripts/paddle_sv3_worker.py`（已写好，与 `paddle_ocr_worker.py` 同协议、同 JSON Lines stdin/stdout），切换方式：

1. 在 docrestore backend 的 worker spawn 处把 `paddle_ocr_worker.py` 换成 `paddle_sv3_worker.py`（详细接入清单见 `main_doc_train/INTEGRATION.md`）
2. `initialize` 消息里多带两个字段：
   ```json
   {
     "main_doc_model_dir": ".../main_doc_train/output/best_model/inference",
     "main_doc_model_name": "PicoDet-S"
   }
   ```
   不传则降级为"裁剪图直跑 sv3-light"模式（输入必须已裁好）
3. v0 模型位置：`test_images/dococr_bench/main_doc_train/output/best_model/inference/`（best score 0.79，小数据集）

收益（相对 PaddleOCRVL）：
- 速度：1.78 s → ~1.07 s/张（**1.7× ↑**，含主区域检测开销）
- 显存：~8 GB → ~3.5 GB（**↓55%**）
- 启动：~80 s → ~6 s（无需起 vllm-server，`ppocr_vlm` env 可下线）
- 输入：**支持原图**（v0 PicoDet 自动裁），不再依赖前端裁剪

注意事项：
- v0 训练数据少（~22 张），best score 0.79，跨域（其他项目截图）泛化未验证。**上量样本后必须重训**，流程见 `main_doc_train/README.md` 的 8 阶段
- 公式/表格密集的文档，可以临时把 `use_table_recognition=True / use_formula_recognition=True` 打开，单图 ~1.5 s 仍快于 VLM
- 显存预算：检测器（PicoDet-S ~6 MB 模型 + ~500 MB 推理 buffer）+ sv3-light（~3 GB）；A2(16GB) 上有充裕空间，4070S 上要留意已被占用情况

### 已废弃的旧方案（避免重复踩坑）

- ❌ "在 `paddle_ocr_worker.py` 里直接改 `PPStructureV3`"：worker 重复造轮，已封装到 `paddle_sv3_worker.py`
- ❌ "ColumnFilterThresholds 为 PP-StructureV3 重标定"：违反"禁用几何阈值"原则
- ❌ "DBSCAN 自适应密度聚类"：结构性失败，见实验 4
- ❌ "亮区聚类预裁"：见 `scripts/precrop_main_doc.py` 注释

---

## 四、产物索引

```
test_images/dococr_bench/
├── RESULTS.md                        # 本文件（benchmark 数据 + 当前推荐路径）
├── scripts/
│   ├── run_structurev3.py            # sv3-full / sv3-light / sv3-region / ocr-only
│   ├── run_sv3_with_recrop.py        # ❌ sv3-light + ColumnFilter 重裁（已废弃）
│   └── precrop_main_doc.py           # ❌ 亮区聚类（已验证不可用）
├── out/                              # benchmark 原始输出（22 张）
│   ├── sv3-light/AI子系统-裁剪/...    # ⭐ 裁剪图全部输出 markdown + layout 可视化
│   ├── sv3-light/AI子系统/...         # 原图直跑反例
│   ├── sv3-region/                   # PP-DocBlockLayout 实验
│   └── ocr-only/                     # 纯 PaddleOCR
├── out_recrop/                        # ColumnFilter 重裁实验（已废弃）
└── main_doc_train/                    # ★ PicoDet 主区域检测器训练 + 集成
    ├── README.md                      # ★ 8 阶段训练流程入口
    ├── INTEGRATION.md                 # ★ 接入 docrestore backend 指南
    ├── LESSONS_LEARNED.md             # ★ 11 条踩坑笔记（PaddleX 装坑/SIFT/DBSCAN/显存）
    ├── configs/
    │   ├── PicoDet-S_main_doc.yaml   # 小数据 ≤500 张
    │   └── PicoDet-L_main_doc.yaml   # 1000+ 张推荐
    ├── scripts/
    │   ├── run_paddlex.py             # 训练/评估/导出统一入口
    │   └── test_worker.py             # 端到端 worker 烟雾测试
    ├── tools/
    │   ├── crop_to_anylabeling.py     # SIFT+RANSAC 半自动标注（有原图+裁剪图配对）
    │   ├── predict_to_anylabeling.py  # v0 模型预测预填标注（仅原图）
    │   ├── seed_images.py             # 收集图到 dataset/images/
    │   └── build_coco.py              # X-AnyLabeling JSON → COCO 格式
    ├── dataset/                       # COCO 格式数据集（gitignore）
    └── output/best_model/inference/   # ★ v0 推理模型（best score 0.79）

DocRestore/scripts/
└── paddle_sv3_worker.py               # ★ 新 worker（PicoDet + sv3-light 端到端）
```

每张 benchmark 图的输出包含：
- `{stem}.md` — markdown
- `{stem}_layout_det_res.JPG` — layout 框可视化
- `{stem}_overall_ocr_res.JPG` — OCR 文本框可视化
- `{stem}_layout_order_res.JPG` — 阅读顺序可视化
- `{stem}_res.json` — 完整结构化 JSON
- `imgs/` — 切出的图片块
