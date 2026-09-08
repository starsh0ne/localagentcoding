"""pomodoro.py 纯逻辑单元测试（无需真实计时/IO，仅依赖 pytest）。"""

from datetime import datetime, timedelta

import pomodoro as app


def t(now: datetime, seconds_ago: float) -> datetime:
    return now - timedelta(seconds=seconds_ago)


def make_running(now: datetime, minutes=25, elapsed_before=0.0, seconds_running=0.0):
    """构造一个 running 状态：已专注 elapsed_before 秒，且本运行段已跑 seconds_running 秒。"""
    data = app.new_data()
    s = data["state"]
    s["status"] = app.RUNNING
    s["duration_minutes"] = minutes
    s["started_at"] = app.to_iso(t(now, elapsed_before + seconds_running))
    s["running_from"] = app.to_iso(t(now, seconds_running))
    s["elapsed_seconds"] = elapsed_before
    return data


# ---------------- 剩余时间推算 ----------------
def test_compute_remaining_running():
    now = datetime.now().astimezone()
    data = make_running(now, minutes=25, seconds_running=300)  # 已专注 300s
    info = app.compute_info(data, now)
    assert info["dur"] == 1500
    assert abs(info["elapsed"] - 300) < 0.001
    assert abs(info["remaining"] - 1200) < 0.001
    assert info["complete"] is False


def test_compute_complete_when_overdue():
    now = datetime.now().astimezone()
    data = make_running(now, minutes=25, seconds_running=1600)  # 超时 100s
    info = app.compute_info(data, now)
    assert info["complete"] is True
    assert info["remaining"] == 0.0
    assert info["elapsed"] == 1500  # 封顶在计划时长


def test_ensure_completed_logs_history():
    now = datetime.now().astimezone()
    data = make_running(now, minutes=25, seconds_running=1600)
    changed, entry = app.ensure_completed(data, now)
    assert changed is True
    assert entry["completed"] is True
    assert entry["focus_seconds"] == 1500
    assert data["state"]["status"] == app.IDLE
    assert len(data["history"]) == 1


# ---------------- start / pause / resume / reset ----------------
def test_start_rejects_when_active():
    now = datetime.now().astimezone()
    data = make_running(now, minutes=25, seconds_running=10)
    ok, changed, _ = app.start_pomodoro(data, 25)
    assert ok is False and changed is False  # 已有进行中，拒绝覆盖
    assert data["state"]["status"] == app.RUNNING


def test_pause_then_resume_keeps_time():
    now = datetime.now().astimezone()
    data = make_running(now, minutes=25, seconds_running=120)  # 已专注 2 分钟
    ok, changed, _ = app.pause_pomodoro(data, now)
    assert ok and changed
    s = data["state"]
    assert s["status"] == app.PAUSED
    assert abs(s["elapsed_seconds"] - 120) <= 1  # 暂停时把 120s 固化下来

    # 暂停中时间流逝不计入（now 已前进 10 分钟）
    later = now + timedelta(minutes=10)
    ok, changed, _ = app.resume_pomodoro(data, later)
    assert ok and changed
    assert data["state"]["status"] == app.RUNNING

    # 恢复后再跑 60s：剩余应为 1500-120-60 = 1320
    info = app.compute_info(data, later + timedelta(seconds=60))
    assert abs(info["remaining"] - 1320) < 1.0


def test_reset_records_aborted():
    now = datetime.now().astimezone()
    data = make_running(now, minutes=25, seconds_running=90)
    ok, changed, _ = app.reset_pomodoro(data, now)
    assert ok and changed
    assert data["state"]["status"] == app.IDLE
    assert len(data["history"]) == 1
    assert data["history"][0]["completed"] is False
    assert abs(data["history"][0]["focus_seconds"] - 90) <= 1


# ---------------- stats 聚合 ----------------
def test_stats_today():
    now = datetime.now().astimezone()
    data = app.new_data()
    data["history"] = [
        {"completed": True, "planned_minutes": 25, "focus_seconds": 1500,
         "started_at": app.to_iso(now), "ended_at": app.to_iso(now), "label": ""},
        {"completed": True, "planned_minutes": 25, "focus_seconds": 1500,
         "started_at": app.to_iso(now), "ended_at": app.to_iso(now), "label": "写周报"},
        {"completed": False, "planned_minutes": 25, "focus_seconds": 600,
         "started_at": app.to_iso(now), "ended_at": app.to_iso(now), "label": ""},
    ]
    lines = app.stats_text(data, scope="today", now=now)
    text = "\n".join(lines)
    assert "完成番茄钟：2 个" in text
    assert "累计专注：50m" in text      # 2 × 25 分钟
    assert "中途放弃：1 个" in text
    assert "写周报" in text


# ---------------- 持久化容错 ----------------
def test_store_load_corrupt_file_quarantines(tmp_path):
    f = tmp_path / "pomodoro.json"
    f.write_text("{ 这不是合法 JSON", encoding="utf-8")
    store = app.Store(str(f))
    data = store.load()
    assert data == app.new_data()
    # 损坏文件被改名备份，而不是覆盖
    corrupts = list(tmp_path.glob("*.corrupt-*"))
    assert len(corrupts) == 1


def test_store_roundtrip(tmp_path):
    f = tmp_path / "pomodoro.json"
    store = app.Store(str(f))
    data = app.new_data()
    data["history"].append({"completed": True})
    store.save(data)
    assert store.load() == data


def test_normalize_state_repairs_missing_fields():
    state = {"status": "running"}  # 缺一堆字段
    app.normalize_state(state)
    assert state["status"] == app.IDLE  # 无开始时间的孤儿 running -> idle
    assert state["duration_minutes"] == app.DEFAULT_MINUTES
    assert state["elapsed_seconds"] == 0.0
