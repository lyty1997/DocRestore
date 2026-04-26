# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SQLite 持久化层：任务与结果的 CRUD 操作"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

from docrestore.pipeline.config import LLMConfig, OCRConfig, PIIConfig

logger = logging.getLogger(__name__)


def _safe_json_loads(raw: str | None) -> dict[str, object]:
    """容错解析 JSON 快照列；解析失败/为 None 时返回空字典。

    Why: list_tasks 在每页对每行做轻量字段提取，不应为脏数据整页 fail。
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_llm_model(raw: str | None) -> str:
    """从 tasks.llm JSON 快照取 model 字段；缺失时返回空字符串。"""
    val = _safe_json_loads(raw).get("model")
    return val if isinstance(val, str) else ""


def _extract_ocr_model(raw: str | None) -> str:
    """从 tasks.ocr JSON 快照取 model 字段；缺失时返回空字符串。"""
    val = _safe_json_loads(raw).get("model")
    return val if isinstance(val, str) else ""


def _extract_pii_enable(raw: str | None) -> bool:
    """从 tasks.pii JSON 快照取 enable 字段；缺失时视为未启用。"""
    val = _safe_json_loads(raw).get("enable")
    return bool(val)


# ── 建表 SQL ──────────────────────────────────────────────
# llm/ocr/pii 列存完整 Config JSON 快照。

_CREATE_TASKS = """\
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'pending',
    image_dir    TEXT NOT NULL,
    output_dir   TEXT NOT NULL,
    llm          TEXT,
    ocr          TEXT,
    pii          TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)"""

_CREATE_TASKS_IDX_STATUS = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
)
_CREATE_TASKS_IDX_CREATED = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)"
)

_CREATE_RESULTS = """\
CREATE TABLE IF NOT EXISTS task_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    output_path TEXT NOT NULL,
    doc_title   TEXT NOT NULL DEFAULT '',
    doc_dir     TEXT NOT NULL DEFAULT ''
)"""

_CREATE_RESULTS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_results_task ON task_results(task_id)"
)


@dataclass
class TaskRow:
    """从 DB 读取的任务行（纯数据，不含运行时状态）"""

    task_id: str
    status: str
    image_dir: str
    output_dir: str
    llm: LLMConfig | None
    ocr: OCRConfig | None
    pii: PIIConfig | None
    error: str | None
    created_at: str
    updated_at: str


@dataclass
class ResultRow:
    """从 DB 读取的结果行"""

    task_id: str
    output_path: str
    doc_title: str
    doc_dir: str


@dataclass
class TaskListItem:
    """列表查询返回的精简任务信息

    pii_enable / ocr_model / llm_model 从 tasks.llm/ocr/pii JSON 快照展开，
    用于在前端任务列表卡片直接显示，避免再次拉单任务详情。
    llm_model 可为空字符串（用户未配置精修时），ocr_model 必为非空。
    """

    task_id: str
    status: str
    image_dir: str
    output_dir: str
    error: str | None
    created_at: str
    result_count: int
    pii_enable: bool = False
    ocr_model: str = ""
    llm_model: str = ""


@dataclass
class TaskListResult:
    """分页列表查询结果"""

    tasks: list[TaskListItem]
    total: int
    page: int
    page_size: int


class TaskDatabase:
    """SQLite 异步任务持久化"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """打开连接、建表、恢复中断任务。"""
        # 确保目录存在
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        # 启用外键约束和 WAL 模式
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        await self._db.execute(_CREATE_TASKS)
        await self._db.execute(_CREATE_TASKS_IDX_STATUS)
        await self._db.execute(_CREATE_TASKS_IDX_CREATED)
        await self._db.execute(_CREATE_RESULTS)
        await self._db.execute(_CREATE_RESULTS_IDX)

        for col in ("llm", "ocr", "pii"):
            await self._migrate_add_column("tasks", col, "TEXT")

        await self._db.commit()

        # 将中断的任务标记为失败
        await self._recover_interrupted()

    async def _migrate_add_column(
        self,
        table: str,
        column: str,
        col_type: str,
    ) -> None:
        """安全地为已有表添加新列（列已存在时静默跳过）。"""
        db = self._get_db()
        with suppress(Exception):
            await db.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"  # noqa: S608
            )

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── 写操作 ──────────────────────────────────────────

    async def insert_task(
        self,
        task_id: str,
        status: str,
        image_dir: str,
        output_dir: str,
        llm: LLMConfig | None = None,
        ocr: OCRConfig | None = None,
        pii: PIIConfig | None = None,
        created_at: str | None = None,
    ) -> None:
        """插入新任务。llm/ocr/pii 为完整 Config 快照。"""
        db = self._get_db()
        now = created_at or datetime.now().isoformat()
        await db.execute(
            """\
            INSERT INTO tasks
                (task_id, status, image_dir, output_dir,
                 llm, ocr, pii,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                status,
                image_dir,
                output_dir,
                llm.model_dump_json() if llm is not None else None,
                ocr.model_dump_json() if ocr is not None else None,
                pii.model_dump_json() if pii is not None else None,
                now,
                now,
            ),
        )
        await db.commit()

    async def update_status(
        self,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """更新任务状态。"""
        db = self._get_db()
        now = datetime.now().isoformat()
        await db.execute(
            "UPDATE tasks SET status=?, error=?, updated_at=? WHERE task_id=?",
            (status, error, now, task_id),
        )
        await db.commit()

    async def insert_results(
        self,
        task_id: str,
        results: list[tuple[str, str, str]],
    ) -> None:
        """批量插入任务结果。

        参数:
            results: [(output_path, doc_title, doc_dir), ...]
        """
        db = self._get_db()
        await db.executemany(
            """\
            INSERT INTO task_results (task_id, output_path, doc_title, doc_dir)
            VALUES (?, ?, ?, ?)""",
            [(task_id, *r) for r in results],
        )
        await db.commit()

    async def delete_task(self, task_id: str) -> bool:
        """删除任务及其结果（CASCADE）。返回是否存在并删除。"""
        db = self._get_db()
        cursor = await db.execute(
            "DELETE FROM tasks WHERE task_id=?", (task_id,)
        )
        await db.commit()
        return cursor.rowcount > 0

    # ── 读操作 ──────────────────────────────────────────

    async def get_task(self, task_id: str) -> TaskRow | None:
        """查询单个任务。"""
        db = self._get_db()
        cursor = await db.execute(
            """\
            SELECT task_id, status, image_dir, output_dir,
                   llm, ocr, pii, error, created_at, updated_at
            FROM tasks WHERE task_id=?""",
            (task_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    async def get_results(self, task_id: str) -> list[ResultRow]:
        """查询任务的所有结果。"""
        db = self._get_db()
        cursor = await db.execute(
            """\
            SELECT task_id, output_path, doc_title, doc_dir
            FROM task_results WHERE task_id=?
            ORDER BY id""",
            (task_id,),
        )
        rows = await cursor.fetchall()
        return [
            ResultRow(
                task_id=r[0],
                output_path=r[1],
                doc_title=r[2],
                doc_dir=r[3],
            )
            for r in rows
        ]

    async def list_tasks(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> TaskListResult:
        """分页查询任务列表（按创建时间倒序）。"""
        db = self._get_db()

        # 构建 WHERE 子句
        where = ""
        params: list[str | int] = []
        if status is not None:
            where = "WHERE t.status = ?"
            params.append(status)

        # 总数
        count_sql = f"SELECT COUNT(*) FROM tasks t {where}"  # noqa: S608
        cursor = await db.execute(count_sql, params)
        row = await cursor.fetchone()
        total: int = row[0] if row else 0

        # 分页查询（LEFT JOIN 统计结果数；llm/ocr/pii 取 JSON 快照用于卡片展示）
        offset = (page - 1) * page_size
        query = f"""\
            SELECT t.task_id, t.status, t.image_dir, t.output_dir,
                   t.error, t.created_at,
                   t.llm, t.ocr, t.pii,
                   COUNT(r.id) AS result_count
            FROM tasks t
            LEFT JOIN task_results r ON t.task_id = r.task_id
            {where}
            GROUP BY t.task_id
            ORDER BY t.created_at DESC
            LIMIT ? OFFSET ?"""  # noqa: S608
        cursor = await db.execute(query, [*params, page_size, offset])
        rows = await cursor.fetchall()

        items = [
            TaskListItem(
                task_id=r[0],
                status=r[1],
                image_dir=r[2],
                output_dir=r[3],
                error=r[4],
                created_at=r[5],
                result_count=r[9],
                llm_model=_extract_llm_model(r[6]),
                ocr_model=_extract_ocr_model(r[7]),
                pii_enable=_extract_pii_enable(r[8]),
            )
            for r in rows
        ]

        return TaskListResult(
            tasks=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    # ── 内部方法 ────────────────────────────────────────

    def _get_db(self) -> aiosqlite.Connection:
        """获取数据库连接，未初始化时报错。"""
        if self._db is None:
            msg = "数据库未初始化，请先调用 initialize()"
            raise RuntimeError(msg)
        return self._db

    async def _recover_interrupted(self) -> None:
        """将中断的 pending/processing 任务标记为 failed。"""
        db = self._get_db()
        now = datetime.now().isoformat()
        cursor = await db.execute(
            """\
            UPDATE tasks
            SET status='failed', error='服务重启中断', updated_at=?
            WHERE status IN ('pending', 'processing')""",
            (now,),
        )
        if cursor.rowcount > 0:
            logger.info("已将 %d 个中断任务标记为失败", cursor.rowcount)
        await db.commit()

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> TaskRow:
        """将 DB 行转换为 TaskRow（反序列化 Config JSON 快照）。"""
        llm_raw = row[4]
        ocr_raw = row[5]
        pii_raw = row[6]
        return TaskRow(
            task_id=row[0],
            status=row[1],
            image_dir=row[2],
            output_dir=row[3],
            llm=LLMConfig.model_validate_json(llm_raw) if llm_raw else None,
            ocr=OCRConfig.model_validate_json(ocr_raw) if ocr_raw else None,
            pii=PIIConfig.model_validate_json(pii_raw) if pii_raw else None,
            error=row[7],
            created_at=row[8],
            updated_at=row[9],
        )
