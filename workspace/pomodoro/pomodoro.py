#!/usr/bin/env python3
"""
番茄钟 CLI 应用（单文件、纯标准库，Python 3.12 / uv 管理）。

特点
----
* 双模式：无参数进入交互式 REPL（键盘输入命令），也可直接跑子命令。
* start/pause/resume/reset/status/stats/watch 均可在任意终端执行，
  暂停与继续基于持久化的时间戳推算，不依赖“计时进程还活着”。
* 计时状态（状态机 + 已专注秒数 + 运行段起始时间）与历史记录统一存放在
  一个 JSON 文件里，程序重启后可精确恢复未完成番茄钟的剩余时间。
* 今日统计基于历史记录（每条记录含开始/结束时间）计算。
* JSON 读写原子化，并对“文件不存在 / JSON 损坏 / 字段缺失”做容错。

数据文件
--------
默认 ~/.pomodoro.json；可用环境变量 POMODORO_FILE 或命令行 --file 覆盖。

    {
      "version": 1,
      "state": {
        "status": "idle|running|paused",
        "duration_minutes": 25,
        "label": "写周报",
        "started_at": "2025-01-01T09:00:00+08:00",  // 本番茄钟开始时间
        "running_from": "2025-01-01T09:10:00+08:00", // 最近一次 resume 的时刻
        "elapsed_seconds": 600                        // 最近一次 resume 之前已专注秒数
      },
      "history": [
        {
          "completed": true,
          "planned_minutes": 25,
          "focus_seconds": 1500,
          "started_at": "...",
          "ended_at": "...",
          "label": "写周报"
        }
      ]
    }

注意：status==running 时“当前剩余秒数” = duration*60 - elapsed_seconds
      - (now - running_from)，因此哪怕进程被杀/重启，时间也不会丢失。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

APP_VERSION = "2.0.0"
DEFAULT_MINUTES = 25          # 默认专注时长（分钟）
MAX_MINUTES = 240             # 允许的最大单次时长（分钟）
ENV_FILE = "POMODORO_FILE"    # 可用环境变量指定数据文件路径
DEFAULT_FILE = str(Path.home() / ".pomodoro.json")
BAR_WIDTH = 20                # watch/进度条宽度

# 状态机取值
IDLE, RUNNING, PAUSED = "idle", "running", "paused"


# ==================== 时间工具 ====================
def now_local() -> datetime:
    """返回带本地时区信息的当前时间（保证 isoformat 含时区偏移）。"""
    return datetime.now().astimezone()


def to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def from_iso(text) -> datetime | None:
    """安全解析 ISO 时间；naive 视为本地时间；解析失败返回 None。"""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:  # 兼容旧数据：无时区按本地时间处理
        dt = dt.replace(tzinfo=now_local().tzinfo)
    return dt


def clock(seconds: int | float) -> str:
    """秒 -> MM:SS 或 H:MM:SS（用于倒计时显示）。"""
    total = max(0, int(math.ceil(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def focus_human(seconds: int | float) -> str:
    """秒 -> 人类可读的专注总时长，如 90m / 1h 05m。"""
    total = max(0, int(seconds))
    h, m = divmod(round(total / 60), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def local_date(dt: datetime | None):
    return dt.date() if dt is not None else None


# ==================== 数据模型（纯数据容器） ====================
def fresh_state() -> dict:
    """空闲态状态字典（elapsed_seconds 统一用 float，保证 JSON 往返一致）。"""
    return {
        "status": IDLE,
        "duration_minutes": DEFAULT_MINUTES,
        "label": "",
        "started_at": None,    # 本番茄钟最初的开始时间（pause/resume 不改变）
        "running_from": None,  # 最近一次进入 running 的时刻
        "elapsed_seconds": 0.0,  # running_from 之前累计的专注秒数
    }


def new_data() -> dict:
    """全新的数据文件内容。"""
    return {"version": 1, "state": fresh_state(), "history": []}


def normalize_state(state: dict) -> None:
    """补齐缺失字段并修正非法值（对历史文件/手改文件做容错）。"""
    state.setdefault("status", IDLE)
    if state["status"] not in (IDLE, RUNNING, PAUSED):
        state["status"] = IDLE
    state.setdefault("duration_minutes", DEFAULT_MINUTES)
    try:
        state["duration_minutes"] = max(1, min(MAX_MINUTES, int(state["duration_minutes"])))
    except (TypeError, ValueError):
        state["duration_minutes"] = DEFAULT_MINUTES
    state.setdefault("label", "")
    if not isinstance(state["label"], str):
        state["label"] = ""
    state.setdefault("started_at", None)
    state.setdefault("running_from", None)
    try:
        state["elapsed_seconds"] = max(0.0, float(state.get("elapsed_seconds", 0) or 0))
    except (TypeError, ValueError):
        state["elapsed_seconds"] = 0.0
    # running/paused 却没有任何开始时间：孤儿状态，降级为 idle
    if state["status"] in (RUNNING, PAUSED) and not state.get("started_at"):
        state["status"] = IDLE


# ==================== 数据持久化（Store） ====================
class Store:
    """单个 JSON 文件的读写，含原子写入与损坏隔离。"""

    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> dict:
        """载入数据；文件不存在/损坏/结构不对时给出安全默认值。"""
        if not self.path.exists():
            return new_data()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            self._quarantine()
            return new_data()
        if not isinstance(data, dict):
            self._quarantine()
            return new_data()
        data.setdefault("version", 1)
        if not isinstance(data.get("state"), dict):
            data["state"] = fresh_state()
        normalize_state(data["state"])
        if not isinstance(data.get("history"), list):
            data["history"] = []
        return data

    def save(self, data: dict) -> None:
        """原子写入：先写临时文件再 os.replace，避免写一半损坏。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def _quarantine(self) -> None:
        """把无法解析的文件改名备份（不删除，便于排查）。"""
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = self.path.with_name(self.path.name + f".corrupt-{ts}")
            self.path.replace(backup)
            print(
                f"[warn] 数据文件无法解析，已备份到 {backup} 并从空数据重新开始",
                file=sys.stderr,
            )
        except OSError:
            pass


def resolve_state_file(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get(ENV_FILE)
    if env:
        return env
    return DEFAULT_FILE


# ==================== 计时核心（纯函数，便于测试） ====================
def compute_info(data: dict, now: datetime | None = None) -> dict:
    """根据状态推算：时长/已专注/剩余/进度，及是否已到完成点。

    无论进程是否在跑、是否重启，结果都只由持久化时间戳决定。
    """
    now = now or now_local()
    s = data["state"]
    dur = int(s.get("duration_minutes", DEFAULT_MINUTES)) * 60
    elapsed = float(s.get("elapsed_seconds", 0) or 0)
    seg = 0.0
    if s.get("status") == RUNNING:
        rf = from_iso(s.get("running_from"))
        if rf is not None:
            seg = max(0.0, (now - rf).total_seconds())
    total = min(dur, elapsed + seg) if dur else 0.0
    remaining = max(0.0, dur - total)
    pct = (total / dur) if dur else 0.0
    complete = s.get("status") == RUNNING and dur > 0 and remaining <= 0.0
    return {
        "dur": dur,
        "elapsed": total,
        "remaining": remaining,
        "pct": pct,
        "complete": complete,
    }


def _reset_to_idle(state: dict) -> None:
    """清空当前计时（保留 duration_minutes，作为下次 start 的默认提示）。"""
    state["status"] = IDLE
    state["label"] = ""
    state["started_at"] = None
    state["running_from"] = None
    state["elapsed_seconds"] = 0.0


def _finalize_completed(data: dict, now: datetime | None = None) -> dict:
    """把正在 running 且已到点的番茄钟写入历史并复位状态，返回历史条目。"""
    now = now or now_local()
    s = data["state"]
    dur = int(s.get("duration_minutes", DEFAULT_MINUTES)) * 60
    elapsed_before = float(s.get("elapsed_seconds", 0) or 0)
    rf = from_iso(s.get("running_from")) or now
    # 完成时刻 = 本运行段开始时刻 + 该段开始时剩余的秒数（时间戳精确，与何时检测无关）
    rem_before = max(0.0, dur - elapsed_before)
    finish = rf + timedelta(seconds=rem_before)

    entry = {
        "completed": True,
        "planned_minutes": int(s.get("duration_minutes", DEFAULT_MINUTES)),
        "focus_seconds": dur,
        "started_at": to_iso(from_iso(s.get("started_at")) or now),
        "ended_at": to_iso(finish),
        "label": s.get("label", "") or "",
    }
    data["history"].append(entry)
    _reset_to_idle(s)
    return entry


def ensure_completed(data: dict, now: datetime | None = None):
    """若 running 且已到完成点则结算。返回 (是否有变更, 历史条目或 None)。"""
    if compute_info(data, now)["complete"]:
        return True, _finalize_completed(data, now)
    return False, None


def completion_text(entry: dict) -> str:
    txt = f"✅ 番茄钟完成！({entry['planned_minutes']} 分钟)"
    if entry.get("label"):
        txt += f" — {entry['label']}"
    return txt


# ==================== 操作命令（含 REPL 与子命令共用） ====================
def start_pomodoro(data: dict, minutes: int = DEFAULT_MINUTES,
                   label: str = "", now: datetime | None = None):
    """开始新番茄钟。已有进行中/暂停中的番茄钟时拒绝（需先 reset）。"""
    s = data["state"]
    now = now or now_local()
    if s["status"] in (RUNNING, PAUSED):
        return False, False, [
            "⚠️  已有番茄钟在进行中（pause 可暂停 / reset 可放弃），请先处理。"
        ]
    minutes = max(1, min(MAX_MINUTES, int(minutes)))
    label = (label or "").strip()
    s.update(
        status=RUNNING,
        duration_minutes=minutes,
        label=label,
        started_at=to_iso(now),
        running_from=to_iso(now),
        elapsed_seconds=0.0,
    )
    msg = f"🍅 开始番茄钟：{minutes} 分钟"
    if label:
        msg += f"（📌 {label}）"
    msg += "。输入 pause 暂停 / Ctrl+C 中断进程也无妨，时间照常由状态推算。"
    return True, True, [msg]


def pause_pomodoro(data: dict, now: datetime | None = None):
    """暂停当前番茄钟（累计已专注秒数并清空运行段）。"""
    s = data["state"]
    now = now or now_local()
    if s["status"] != RUNNING:
        return False, False, ["ℹ️  没有运行中的番茄钟可暂停。"]
    info = compute_info(data, now)
    if info["complete"] or info["remaining"] <= 0:
        # 边界情形：暂停时刚好用满时长 -> 记一次完成
        entry = _finalize_completed(data, now)
        return True, True, [completion_text(entry)]
    s["elapsed_seconds"] = float(int(info["elapsed"]))
    s["running_from"] = None
    s["status"] = PAUSED
    return True, True, [
        f"⏸️  已暂停。已专注 {clock(info['elapsed'])}，剩余 {clock(info['remaining'])}。"
    ]


def resume_pomodoro(data: dict, now: datetime | None = None):
    """继续被暂停的番茄钟。"""
    s = data["state"]
    now = now or now_local()
    if s["status"] != PAUSED:
        return False, False, ["ℹ️  没有处于暂停状态的番茄钟（先 start 或 pause）。"]
    info = compute_info(data, now)
    if info["remaining"] <= 0:
        entry = _finalize_completed(data, now)
        return True, True, [completion_text(entry)]
    s["running_from"] = to_iso(now)
    s["status"] = RUNNING
    return True, True, [f"▶️  已继续，剩余 {clock(info['remaining'])}。"]


def reset_pomodoro(data: dict, now: datetime | None = None):
    """放弃当前番茄钟；已专注部分作为 aborted 记录进历史（便于统计）。"""
    s = data["state"]
    now = now or now_local()
    if s["status"] == IDLE:
        return False, False, ["ℹ️  没有需要重置的番茄钟。"]
    dur = int(s.get("duration_minutes", DEFAULT_MINUTES)) * 60
    info = compute_info(data, now)
    total = min(dur, info["elapsed"])
    label = s.get("label", "")
    if total > 0:
        data["history"].append({
            "completed": False,
            "planned_minutes": int(s.get("duration_minutes", DEFAULT_MINUTES)),
            "focus_seconds": int(total),
            "started_at": to_iso(from_iso(s.get("started_at")) or now),
            "ended_at": to_iso(now),
            "label": label,
        })
    _reset_to_idle(s)
    msg = "🔄 已重置当前番茄钟。"
    if total > 0:
        msg += f"（放弃前专注 {clock(total)}{('，📌 ' + label) if label else ''}，已记入历史）"
    return True, True, [msg]


# ==================== 展示：status / stats ====================
def status_text(data: dict, now: datetime | None = None) -> list[str]:
    s = data["state"]
    if s["status"] == IDLE:
        return ["📋 状态：空闲（无进行中的番茄钟）",
                "   使用 start 开始新番茄钟，或 stats 查看今日统计。"]
    info = compute_info(data, now)
    bar = _bar(info["pct"])
    head = "📋 状态：运行中" if s["status"] == RUNNING else "📋 状态：已暂停"
    lines = [
        head,
        f"   计划：{s['duration_minutes']} 分钟",
        f"   剩余：{clock(info['remaining'])}   已专注：{clock(info['elapsed'])}",
        f"   进度：{info['pct'] * 100:3.0f}%  {bar}",
    ]
    if s.get("label"):
        lines.append(f"   📌 任务：{s['label']}")
    return lines


def _bar(pct: float) -> str:
    filled = int(round(pct * BAR_WIDTH))
    filled = max(0, min(BAR_WIDTH, filled))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def stats_text(data: dict, scope: str = "today", now: datetime | None = None) -> list[str]:
    """按日期聚合历史记录生成统计文本。scope: today | all。"""
    now = now or now_local()
    today = now.date()
    history = data["history"]

    def day_of(e):
        return local_date(from_iso(e.get("ended_at") or e.get("started_at")))

    completed = [e for e in history if e.get("completed") and day_of(e) is not None]
    aborted = [e for e in history if not e.get("completed") and day_of(e) is not None]
    if scope == "today":
        completed = [e for e in completed if day_of(e) == today]
        aborted = [e for e in aborted if day_of(e) == today]

    n_done = len(completed)
    focus_sec = sum(int(e.get("focus_seconds", 0)) for e in completed)
    avg = focus_sec // n_done if n_done else 0
    head = f"📊 今日统计（{today.isoformat()}）" if scope == "today" else "📊 全部历史统计"

    lines = [head, "=" * 46]
    if n_done:
        lines.append(f"  🍅 完成番茄钟：{n_done} 个")
        lines.append(f"  ⏱️  累计专注：{focus_human(focus_sec)}（平均 {clock(avg)}/个）")
    else:
        lines.append("  🍅 完成番茄钟：0 个（今天还没有完成记录）")
    if aborted:
        lines.append(
            f"  💨 中途放弃：{len(aborted)} 个（已专注 "
            f"{focus_human(sum(int(e.get('focus_seconds', 0)) for e in aborted))}）"
        )

    s = data["state"]
    if s["status"] != IDLE:
        info = compute_info(data, now)
        lines.append(
            f"  🔄 进行中：{'运行' if s['status'] == RUNNING else '暂停'}，"
            f"剩余 {clock(info['remaining'])}"
        )

    if completed:
        lines.append("  ── 明细 ──")
        shown = completed[-15:]
        for e in shown:
            st = from_iso(e.get("started_at"))
            en = from_iso(e.get("ended_at"))
            span = ""
            if st and en:
                span = f"{st.strftime('%H:%M')}–{en.strftime('%H:%M')} "
            label = f" [📌 {e['label']}]" if e.get("label") else ""
            lines.append(f"    {span}({e['planned_minutes']}m){label} ✓")
        if len(completed) > len(shown):
            lines.append(f"    ……（共 {len(completed)} 条）")
    return lines


# ==================== 提示 / 通知 ====================
def bell() -> None:
    """终端响铃（ASCII BEL），作为结束时提示的一部分。"""
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass


def notify(title: str, body: str = "") -> None:
    """尽力而为的系统通知（Linux notify-send 存在时）；不抛异常。"""
    bell()
    sender = shutil.which("notify-send")
    if not sender:
        return
    try:
        args = [sender, title]
        if body:
            args.append(body)
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ==================== watch：实时倒计时 ====================
def watch(data: dict, store: Store) -> int:
    """前台实时显示倒计时与进度条；结束或手动 Ctrl+C 时返回。"""
    s = data["state"]
    if s["status"] == IDLE:
        print("ℹ️  没有进行中的番茄钟。先 start，再 watch。")
        return 1
    if s["status"] == PAUSED:
        info = compute_info(data)
        print(f"⏸️  番茄钟处于暂停状态（剩余 {clock(info['remaining'])}），先 resume 再 watch。")
        return 0

    print("⏱️  实时倒计时（Ctrl+C 仅退出本视图，计时不受影响）：")
    try:
        while True:
            info = compute_info(data)
            if info["complete"]:
                entry = _finalize_completed(data)
                store.save(data)
                print()  # 结束上一行刷新
                notify("🍅 番茄钟完成", f"专注 {entry['planned_minutes']} 分钟")
                print(completion_text(entry))
                return 0
            bar = _bar(info["pct"])
            line = (f"\r  {clock(info['remaining'])}  [{bar}] "
                    f"{info['pct'] * 100:3.0f}%  计划 {info['dur'] // 60} 分钟")
            if s.get("label"):
                line += f"  📌 {s['label']}"
            sys.stdout.write("\x1b[2K" + line)
            sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n👋 退出倒计时视图。剩余时间保存在状态文件中，随时可 status/watch。")
        return 0


# ==================== 交互式 REPL ====================
HELP_TEXT = """
命令（均可简写为单字母，[] 表示可选）：
  start [分钟] [标签...]   开始番茄钟（默认 25 分钟），如: start 50 写周报
  pause / p               暂停当前番茄钟
  resume / r              继续暂停的番茄钟
  reset / x               放弃并重置当前番茄钟
  status / s              查看当前状态与剩余时间
  watch / w               实时倒计时视图（结束自动提示；Ctrl+C 退出视图）
  stats / t [all]         今日统计（加 all 看全部历史）
  help / h                显示本帮助
  quit / q / exit         退出
"""


def _prompt(data: dict) -> str:
    s = data["state"]
    if s["status"] == IDLE:
        return "🍅 idle> "
    info = compute_info(data)
    tag = "run" if s["status"] == RUNNING else "pause"
    return f"🍅 {tag} {clock(info['remaining'])}> "


def run_repl(data: dict, store: Store) -> int:
    """交互式命令行循环：每次输入前后都基于时间戳结算，天然跨重启。"""
    print(f"🍅 番茄钟 CLI v{APP_VERSION} —— 输入 help 查看命令，quit 退出")
    print(HELP_TEXT)
    while True:
        # 先结算：若 REPL 期间/上次退出后番茄钟已到期，这里补记完成
        changed, entry = ensure_completed(data)
        if changed:
            store.save(data)
            notify("🍅 番茄钟完成", f"专注 {entry['planned_minutes']} 分钟")
            print(completion_text(entry))

        try:
            raw = input(_prompt(data))
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            return 0
        raw = raw.strip()
        if not raw:
            continue
        toks = raw.split()
        cmd, rest = toks[0].lower(), toks[1:]
        ok = True

        if cmd in ("quit", "q", "exit"):
            print("👋 再见！")
            return 0
        elif cmd in ("help", "h", "?"):
            print(HELP_TEXT)
            continue
        elif cmd == "start":
            minutes = DEFAULT_MINUTES
            if rest:
                try:
                    minutes = int(rest[0])
                except ValueError:
                    print("❌ 分钟数需为整数，例如 start 50")
                    continue
            ok, changed, lines = start_pomodoro(
                data, minutes, " ".join(rest[1:])
            )
        elif cmd in ("pause", "p"):
            ok, changed, lines = pause_pomodoro(data)
        elif cmd in ("resume", "r"):
            ok, changed, lines = resume_pomodoro(data)
        elif cmd in ("reset", "x"):
            ok, changed, lines = reset_pomodoro(data)
        elif cmd in ("status", "s"):
            lines, changed = status_text(data), False
        elif cmd in ("watch", "w"):
            watch(data, store)
            continue
        elif cmd in ("stats", "t"):
            scope = "all" if rest and rest[0] in ("all", "--all", "-a") else "today"
            lines, changed = stats_text(data, scope), False
        else:
            lines, changed = [f"❌ 未知命令: {cmd}（help 查看帮助）"], False

        for line in lines:
            print(line)
        if changed:
            store.save(data)
    return 0


# ==================== 命令行入口（子命令） ====================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pomodoro",
        description="🍅 番茄钟 CLI（单文件 / JSON 持久化）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  uv run python pomodoro.py                     # 交互式 REPL\n"
            "  uv run python pomodoro.py start 50 写周报     # 开始 50 分钟番茄钟\n"
            "  uv run python pomodoro.py pause / resume / reset\n"
            "  uv run python pomodoro.py status / watch / stats\n"
            "  uv run python pomodoro.py --file data.json stats\n"
            "数据文件默认 ~/.pomodoro.json，可用 --file 或环境变量 POMODORO_FILE 覆盖。"
        ),
    )
    parser.add_argument("--file", metavar="PATH", help="JSON 数据文件路径（默认 ~/.pomodoro.json）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_start = sub.add_parser("start", help="开始番茄钟（可指定时长/标签）")
    p_start.add_argument("minutes", type=int, nargs="?", default=DEFAULT_MINUTES,
                         help=f"专注分钟数（默认 {DEFAULT_MINUTES}）")
    p_start.add_argument("label", nargs="*", help="可选任务标签，如 写周报")

    sub.add_parser("pause", help="暂停当前番茄钟")
    sub.add_parser("resume", help="继续暂停的番茄钟")
    sub.add_parser("reset", help="放弃并重置当前番茄钟")
    sub.add_parser("status", help="查看当前状态与剩余时间")
    sub.add_parser("watch", help="实时倒计时视图（结束自动提示）")
    p_stats = sub.add_parser("stats", help="今日统计（默认）")
    p_stats.add_argument("--all", action="store_true", help="查看全部历史统计")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(resolve_state_file(getattr(args, "file", None)))
    data = store.load()
    cmd = getattr(args, "command", None)

    if cmd is None:  # 无子命令 -> 交互式 REPL
        return run_repl(data, store)

    # 子命令一律先结算可能已到期的番茄钟
    changed, entry = ensure_completed(data)
    if changed:
        store.save(data)
        print(completion_text(entry))

    if cmd == "start":
        ok, changed, lines = start_pomodoro(data, args.minutes, " ".join(args.label))
    elif cmd == "pause":
        ok, changed, lines = pause_pomodoro(data)
    elif cmd == "resume":
        ok, changed, lines = resume_pomodoro(data)
    elif cmd == "reset":
        ok, changed, lines = reset_pomodoro(data)
    elif cmd == "status":
        ok, changed, lines = True, False, status_text(data)
    elif cmd == "stats":
        ok, changed, lines = True, False, stats_text(data, "all" if args.all else "today")
    elif cmd == "watch":
        watch(data, store)
        return 0
    else:
        print(f"未知命令: {cmd}", file=sys.stderr)
        return 2

    for line in lines:
        print(line)
    if changed:
        store.save(data)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
