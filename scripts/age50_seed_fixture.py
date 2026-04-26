#!/usr/bin/env python
# Copyright 2026 @lyty1997
# Licensed under the Apache License, Version 2.0 (the "License")

"""AGE-50 视觉验证：把 output/age8-e2e-refined 灌成 fake completed 任务

用途：本地 dev 调代码模式 UI；不进 CI。

行为：
  - 直接写 SQLite tasks.db，插入一条 status=completed 的任务
  - image_dir 指向 test_images/age8-spike（source-images 能列出原图）
  - output_dir 指向 output/age8-e2e-refined（含 files-index.json + files/）
  - 任务 ID 固定为 ``age50-fixture``，重复运行幂等
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "docrestore.db"
TASK_ID = "age50-fixture"
# 用 spike 符号链接的真实目标，避免 source-image endpoint 的"resolved 必须
# 在 image_dir 下"安全检查把符号链接拒掉
SPIKE_DIR = ROOT / "test_images" / "age8-spike"
_RESOLVED = (
    next(SPIKE_DIR.glob("*.JPG"), None)
    or next(SPIKE_DIR.glob("*.jpg"), None)
)
IMAGE_DIR = (
    _RESOLVED.resolve().parent if _RESOLVED is not None else SPIKE_DIR
)
OUTPUT_DIR = ROOT / "output" / "age8-e2e-refined"


def main() -> int:
    if not OUTPUT_DIR.exists():
        sys.stderr.write(f"output_dir 不存在: {OUTPUT_DIR}\n")
        return 1
    if not (OUTPUT_DIR / "files-index.json").exists():
        sys.stderr.write(
            f"files-index.json 不存在，先跑 AGE-52 测试: {OUTPUT_DIR}\n",
        )
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM task_results WHERE task_id = ?", (TASK_ID,),
        )
        cur.execute("DELETE FROM tasks WHERE task_id = ?", (TASK_ID,))

        # 最小化 LLM/OCR/PII 配置；前端 TaskDetail 不依赖这些字段
        cur.execute(
            """\
            INSERT INTO tasks
                (task_id, status, image_dir, output_dir,
                 llm, ocr, pii, created_at, updated_at)
            VALUES (?, 'completed', ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                TASK_ID,
                str(IMAGE_DIR),
                str(OUTPUT_DIR),
                json.dumps({"model": "fixture"}),
                json.dumps({"engine": "paddleocr", "paddle_pipeline": "basic"}),
                json.dumps({"enable": False}),
            ),
        )
        # 插一条 task_results 让 /tasks/{id}/results 能拿到
        # （前端 TaskDetail 会先拉 results，再额外探测 files-index）
        cur.execute(
            "INSERT INTO task_results (task_id, output_path, doc_title, doc_dir)"
            " VALUES (?, ?, ?, ?)",
            (TASK_ID, str(OUTPUT_DIR / "document.md"), "AGE-8 Code Mode Fixture", ""),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"已写入任务 {TASK_ID}: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
