"""pomodoro.py 纯逻辑单元测试（pytest，仅测试依赖）。

覆盖点：
- 暂停/继续状态机的纯逻辑推算（compute_pause_state / compute_resume_state）
- 剩余时间推算（compute_remaining）
- 崩溃残留判定（is_state_stale）与 load_state 自动清理（使用 tmp_path 隔离）
- complete_session 的记录构建（build_session_record，含 tag 与旧数据兼容）
- stats 聚合（aggregate_stats，含按标签统计与旧数据无 tag 字段兼容）

测试均通过 monkeypatch 将数据/状态文件指向 tmp_path，不污染真实数据文件。
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# 使 pomodoro 模块所在目录（tests 的上一级）可被导入
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pomodoro  # noqa: E402

NOW = datetime(2024, 1, 15, 10, 0, 0)


# ==================== 夹具 ====================
@pytest.fixture
def running_state():
    """一个"已运行 60 秒"的工作番茄钟运行态"""
    start = NOW - timedelta(seconds=60)
    return {
        "status": "running",
        "mode": "work",
        "start_time": start.isoformat(),
        "elapsed_seconds": 0,
        "target_seconds": 25 * 60,
        "tag": "写周报",
    }


# ==================== compute_remaining ====================
def test_compute_remaining_running(running_state):
    now = datetime.fromisoformat(running_state["start_time"]) + timedelta(seconds=100)
    assert pomodoro.compute_remaining(running_state, now) == pytest.approx(25 * 60 - 100)


def test_compute_remaining_paused():
    paused = {
        "status": "paused",
        "mode": "work",
        "start_time": None,
        "elapsed_seconds": 120,
        "target_seconds": 25 * 60,
        "tag": None,
    }
    assert pomodoro.compute_remaining(paused, NOW) == pytest.approx(25 * 60 - 120)


# ==================== 暂停/继续状态机 ====================
def test_compute_pause_state(running_state):
    now = datetime.fromisoformat(running_state["start_time"]) + timedelta(seconds=90)
    paused = pomodoro.compute_pause_state(running_state, now)
    assert paused["status"] == "paused"
    assert paused["start_time"] is None
    assert paused["elapsed_seconds"] == pytest.approx(90)
    assert paused["mode"] == "work"
    assert paused["tag"] == "写周报"  # 标签跨暂停保留


def test_compute_resume_state():
    paused = {
        "status": "paused",
        "mode": "work",
        "start_time": None,
        "elapsed_seconds": 90,
        "target_seconds": 25 * 60,
        "tag": "写周报",
    }
    resumed = pomodoro.compute_resume_state(paused, NOW)
    assert resumed["status"] == "running"
    assert resumed["start_time"] == NOW.isoformat()
    assert resumed["elapsed_seconds"] == 90  # 已累计秒数保留
    assert resumed["tag"] == "写周报"


# ==================== 崩溃残留判定 ====================
def test_is_state_stale():
    base = {
        "status": "running",
        "mode": "work",
        "elapsed_seconds": 0,
        "target_seconds": 25 * 60,
        "tag": None,
    }
    # 早已远超目标时长（2 小时前开始）→ 残留
    stale = dict(base, start_time=(NOW - timedelta(hours=2)).isoformat())
    assert pomodoro.is_state_stale(stale, NOW) is True

    # 刚启动不久 → 正常
    fresh = dict(base, start_time=(NOW - timedelta(seconds=5)).isoformat())
    assert pomodoro.is_state_stale(fresh, NOW) is False

    # 超过 target + 60 缓冲 → 残留
    borderline = dict(base, start_time=(NOW - timedelta(seconds=25 * 60 + 120)).isoformat())
    assert pomodoro.is_state_stale(borderline, NOW) is True

    # 非 running → 不判定为残留
    paused = dict(base, status="paused", start_time=None)
    assert pomodoro.is_state_stale(paused, NOW) is False

    # start_time 缺失或无法解析 → 残留
    assert pomodoro.is_state_stale(dict(base, start_time=None), NOW) is True
    assert pomodoro.is_state_stale(dict(base, start_time="bad-time"), NOW) is True


def test_load_state_cleans_stale_running(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "pomodoro_state.json"
    monkeypatch.setattr(pomodoro, "STATE_FILE", state_file)
    monkeypatch.setattr(pomodoro, "DATA_FILE", tmp_path / "pomodoro_data.json")

    stale = {
        "status": "running",
        "mode": "work",
        "start_time": (datetime.now() - timedelta(hours=2)).isoformat(),
        "elapsed_seconds": 0,
        "target_seconds": 25 * 60,
        "tag": None,
    }
    state_file.write_text(json.dumps(stale), encoding="utf-8")

    state = pomodoro.load_state()
    assert state["status"] == "idle"
    assert not state_file.exists()  # 残留状态文件已被清理
    assert "自动清理" in capsys.readouterr().out


def test_load_state_keeps_fresh_running(tmp_path, monkeypatch):
    state_file = tmp_path / "pomodoro_state.json"
    monkeypatch.setattr(pomodoro, "STATE_FILE", state_file)
    monkeypatch.setattr(pomodoro, "DATA_FILE", tmp_path / "pomodoro_data.json")

    fresh = {
        "status": "running",
        "mode": "work",
        "start_time": (datetime.now() - timedelta(seconds=5)).isoformat(),
        "elapsed_seconds": 0,
        "target_seconds": 25 * 60,
        "tag": "写周报",
    }
    state_file.write_text(json.dumps(fresh), encoding="utf-8")

    state = pomodoro.load_state()
    assert state["status"] == "running"
    assert state_file.exists()
    assert state["tag"] == "写周报"


# ==================== 会话记录构建（tag + 旧数据兼容） ====================
def test_build_session_record_with_tag():
    state = {
        "status": "running",
        "mode": "work",
        "start_time": (NOW - timedelta(minutes=25)).isoformat(),
        "elapsed_seconds": 0,
        "target_seconds": 25 * 60,
        "tag": "写周报",
    }
    rec = pomodoro.build_session_record(state, NOW)
    assert rec["date"] == "2024-01-15"
    assert rec["start_time"] == "09:35:00"
    assert rec["end_time"] == "10:00:00"
    assert rec["duration_minutes"] == 25
    assert rec["completed"] is True
    assert rec["mode"] == "work"
    assert rec["tag"] == "写周报"


def test_build_session_record_without_tag():
    state = {
        "status": "running",
        "mode": "work",
        "start_time": (NOW - timedelta(minutes=25)).isoformat(),
        "elapsed_seconds": 0,
        "target_seconds": 25 * 60,
        "tag": None,
    }
    rec = pomodoro.build_session_record(state, NOW)
    assert "tag" not in rec  # 无标签时不写 tag 字段，与旧数据结构一致


# ==================== stats 聚合 ====================
def test_aggregate_stats_with_tags_and_legacy():
    sessions = [
        # 带标签的工作番茄钟 ×2
        {"date": "2024-01-15", "start_time": "09:00", "end_time": "09:25",
         "duration_minutes": 25, "completed": True, "mode": "work", "tag": "写周报"},
        {"date": "2024-01-15", "start_time": "09:30", "end_time": "09:55",
         "duration_minutes": 25, "completed": True, "mode": "work", "tag": "写周报"},
        # 旧数据：无 tag 字段的工作番茄钟（不得报错，也不计入标签统计）
        {"date": "2024-01-15", "start_time": "10:00", "end_time": "10:25",
         "duration_minutes": 25, "completed": True, "mode": "work"},
        # 带标签的休息番茄钟
        {"date": "2024-01-15", "start_time": "10:30", "end_time": "10:35",
         "duration_minutes": 5, "completed": True, "mode": "break", "tag": "午休"},
        # 另一日期，不应计入当日统计
        {"date": "2024-01-16", "start_time": "09:00", "end_time": "09:25",
         "duration_minutes": 25, "completed": True, "mode": "work", "tag": "写周报"},
        # 未完成记录不计入
        {"date": "2024-01-15", "start_time": "11:00", "end_time": "11:10",
         "duration_minutes": 10, "completed": False, "mode": "work", "tag": "摸鱼"},
    ]

    stats = pomodoro.aggregate_stats(sessions, "2024-01-15")
    assert stats["work_count"] == 3
    assert stats["break_count"] == 1
    assert stats["total_work_minutes"] == 75
    assert stats["total_break_minutes"] == 5
    # 无标签的旧记录不计入标签统计；完成数只算 completed
    assert stats["tag_summary"] == {
        "写周报": {"count": 2, "minutes": 50},
    }
    assert len(stats["completed_sessions"]) == 4


def test_aggregate_stats_empty_day():
    stats = pomodoro.aggregate_stats([], "2024-01-15")
    assert stats["work_count"] == 0
    assert stats["total_work_minutes"] == 0
    assert stats["tag_summary"] == {}
    assert stats["completed_sessions"] == []
