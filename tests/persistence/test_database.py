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

"""TaskDatabase 单元测试"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from docrestore.persistence.database import TaskDatabase
from docrestore.pipeline.config import LLMConfig


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[TaskDatabase]:
    """创建临时数据库并初始化。"""
    db_path = str(tmp_path / "test.db")
    database = TaskDatabase(db_path)
    await database.initialize()
    yield database
    await database.close()


async def test_insert_and_get_task(db: TaskDatabase) -> None:
    """插入任务后应能查询到（完整 Config 快照往返）。"""
    llm_snapshot = LLMConfig(model="gpt-4")

    await db.insert_task(
        task_id="abc12345",
        status="pending",
        image_dir="/test/images",
        output_dir="/test/output",
        llm=llm_snapshot,
        ocr=None,
        created_at="2026-04-08T10:00:00",
    )

    row = await db.get_task("abc12345")
    assert row is not None
    assert row.task_id == "abc12345"
    assert row.status == "pending"
    assert row.image_dir == "/test/images"
    assert row.output_dir == "/test/output"
    assert row.llm == llm_snapshot
    assert row.ocr is None
    assert row.error is None
    assert row.created_at == "2026-04-08T10:00:00"


async def test_get_nonexistent_task(db: TaskDatabase) -> None:
    """查询不存在的任务返回 None。"""
    assert await db.get_task("nonexist") is None


async def test_update_status(db: TaskDatabase) -> None:
    """更新状态和错误信息。"""
    await db.insert_task(
        task_id="t001",
        status="pending",
        image_dir="/img",
        output_dir="/out",
    )

    await db.update_status("t001", "processing")
    row = await db.get_task("t001")
    assert row is not None
    assert row.status == "processing"
    assert row.error is None

    await db.update_status("t001", "failed", error="OCR 超时")
    row = await db.get_task("t001")
    assert row is not None
    assert row.status == "failed"
    assert row.error == "OCR 超时"


async def test_insert_and_get_results(db: TaskDatabase) -> None:
    """插入结果后应能按 task_id 查询。"""
    await db.insert_task(
        task_id="t002",
        status="completed",
        image_dir="/img",
        output_dir="/out",
    )
    await db.insert_results("t002", [
        ("/out/document.md", "文档一", ""),
        ("/out/sub/document.md", "文档二", "sub"),
    ])

    results = await db.get_results("t002")
    assert len(results) == 2
    assert results[0].doc_title == "文档一"
    assert results[0].doc_dir == ""
    assert results[1].doc_title == "文档二"
    assert results[1].doc_dir == "sub"


async def test_delete_task_cascades_results(db: TaskDatabase) -> None:
    """删除任务应级联删除结果。"""
    await db.insert_task(
        task_id="t003",
        status="completed",
        image_dir="/img",
        output_dir="/out",
    )
    await db.insert_results("t003", [("/out/doc.md", "", "")])

    deleted = await db.delete_task("t003")
    assert deleted is True

    assert await db.get_task("t003") is None
    assert await db.get_results("t003") == []


async def test_delete_nonexistent_task(db: TaskDatabase) -> None:
    """删除不存在的任务返回 False。"""
    assert await db.delete_task("ghost") is False


async def test_list_tasks_pagination(db: TaskDatabase) -> None:
    """列表查询支持分页和状态过滤。"""
    for i in range(5):
        status = "completed" if i % 2 == 0 else "failed"
        await db.insert_task(
            task_id=f"p{i:03d}",
            status=status,
            image_dir=f"/img/{i}",
            output_dir=f"/out/{i}",
            created_at=f"2026-04-08T10:{i:02d}:00",
        )

    # 全量查询
    result = await db.list_tasks(page=1, page_size=3)
    assert result.total == 5
    assert len(result.tasks) == 3
    assert result.page == 1

    # 第二页
    result2 = await db.list_tasks(page=2, page_size=3)
    assert len(result2.tasks) == 2

    # 按状态过滤
    completed = await db.list_tasks(status="completed")
    assert completed.total == 3
    assert all(t.status == "completed" for t in completed.tasks)


async def test_list_tasks_with_result_count(db: TaskDatabase) -> None:
    """列表查询应包含结果数量。"""
    await db.insert_task(
        task_id="rc01",
        status="completed",
        image_dir="/img",
        output_dir="/out",
    )
    await db.insert_results("rc01", [
        ("/out/a.md", "A", "a"),
        ("/out/b.md", "B", "b"),
    ])

    result = await db.list_tasks()
    assert len(result.tasks) == 1
    assert result.tasks[0].result_count == 2


async def test_recover_interrupted(tmp_path: Path) -> None:
    """初始化时应将中断任务标记为 failed。"""
    db_path = str(tmp_path / "recover.db")
    db1 = TaskDatabase(db_path)
    await db1.initialize()

    await db1.insert_task(
        task_id="int1", status="processing",
        image_dir="/img", output_dir="/out",
    )
    await db1.insert_task(
        task_id="int2", status="pending",
        image_dir="/img", output_dir="/out",
    )
    await db1.insert_task(
        task_id="ok1", status="completed",
        image_dir="/img", output_dir="/out",
    )
    await db1.close()

    # 重新打开（模拟重启）
    db2 = TaskDatabase(db_path)
    await db2.initialize()

    r1 = await db2.get_task("int1")
    assert r1 is not None
    assert r1.status == "failed"
    assert r1.error == "服务重启中断"

    r2 = await db2.get_task("int2")
    assert r2 is not None
    assert r2.status == "failed"

    ok = await db2.get_task("ok1")
    assert ok is not None
    assert ok.status == "completed"

    await db2.close()
