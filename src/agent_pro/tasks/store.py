# ============================================================
# tasks/store.py | v0.4 诞生（TaskStore: 计划书/进度/子目录 共享状态）
# 通用agent写计划书与验收; coder追加进度; 代码写入 tasks/<task_id>/
# ============================================================
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from agent_pro.agents.base import TaskContext
from agent_pro.config import get_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, db_path: Path, workspace_root: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            plan TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_progress(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        self.conn.commit()
        self.tasks_dir = workspace_root / "tasks"

    def create_task(self, title: str, plan: str) -> str:
        task_id = f"t{datetime.now():%Y%m%d}_{secrets.token_hex(3)}"
        self.conn.execute(
            "INSERT INTO tasks(id, title, status, plan, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?)", (task_id, title, "created", plan, _now(), _now()))
        self.conn.commit()
        (self.tasks_dir / task_id).mkdir(parents=True, exist_ok=True)
        return task_id

    def update_plan(self, task_id: str, plan: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET plan=?, updated_at=? WHERE id=?", (plan, _now(), task_id))
        self.conn.commit()
        return cur.rowcount > 0

    def get(self, task_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def append_progress(self, task_id: str, note: str, finished: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO task_progress(task_id, note, created_at) VALUES(?,?,?)",
            (task_id, note, _now()))
        status = "delivered" if finished else "in_progress"
        self.conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, _now(), task_id))
        self.conn.commit()

    def progress_list(self, task_id: str, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT note, created_at FROM task_progress WHERE task_id=?"
            " ORDER BY id DESC LIMIT ?", (task_id, limit)).fetchall()

    def resolve_task_context(self, task_id: str) -> TaskContext | None:
        row = self.get(task_id)
        if row is None:
            return None
        return TaskContext(task_id=task_id, title=row["title"],
                           workspace_dir=self.tasks_dir / task_id)


@lru_cache
def get_task_store() -> TaskStore:
    s = get_settings()
    return TaskStore(s.data_dir / "agent_pro.db", s.workspace_dir)