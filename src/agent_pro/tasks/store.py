# ============================================================
# tasks/store.py | v0.4诞生 → v0.5变更
#   +线程安全(lock) +append_progress返回pid(SSE去重用)
#   +progress_history(升序全量, SSE历史回放用)
# ============================================================
import secrets
import sqlite3
import threading
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
        self._db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()  # v1.0: 每线程持有一个连接(连接复用)
        self.tasks_dir = workspace_root / "tasks"

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            plan TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_progress(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        conn.commit()
        return conn

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._new_conn()
            self._local.conn = c
        return c

    def create_task(self, title: str, plan: str) -> str:
        task_id = f"t{datetime.now():%Y%m%d}_{secrets.token_hex(3)}"
        with self._lock:
            self._conn().execute(
                "INSERT INTO tasks(id, title, status, plan, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (task_id, title, "created", plan, _now(), _now()))
            self._conn().commit()
        (self.tasks_dir / task_id).mkdir(parents=True, exist_ok=True)
        return task_id

    def update_plan(self, task_id: str, plan: str) -> bool:
        with self._lock:
            cur = self._conn().execute(
                "UPDATE tasks SET plan=?, updated_at=? WHERE id=?",
                (plan, _now(), task_id))
            self._conn().commit()
            return cur.rowcount > 0

    def get(self, task_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn().execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def append_progress(self, task_id: str, note: str,
                        finished: bool = False) -> int:
        """v0.5: 返回progress行id(pipeline去重用)"""
        with self._lock:
            cur = self._conn().execute(
                "INSERT INTO task_progress(task_id, note, created_at)"
                " VALUES(?,?,?)", (task_id, note, _now()))
            pid = cur.lastrowid
            self._conn().execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                ("delivered" if finished else "in_progress", _now(), task_id))
            self._conn().commit()
        return pid

    def progress_list(self, task_id: str, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn().execute(
                "SELECT note, created_at FROM task_progress WHERE task_id=?"
                " ORDER BY id DESC LIMIT ?", (task_id, limit)).fetchall()

    def progress_history(self, task_id: str, limit: int = 100) -> list[sqlite3.Row]:
        """v0.5新增: 升序历史(SSE回放)"""
        with self._lock:
            return self._conn().execute(
                "SELECT id, note, created_at FROM task_progress WHERE task_id=?"
                " ORDER BY id ASC LIMIT ?", (task_id, limit)).fetchall()

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