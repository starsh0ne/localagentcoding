#!/usr/bin/env python3
"""
番茄钟CLI应用 - 单文件实现
功能：开始/暂停/重置番茄钟，任务标签，今日统计，历史记录，数据持久化
特点：纯标准库、零第三方依赖（tests 除外）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# ==================== 配置 ====================
WORK_MINUTES = 25          # 工作时长（分钟）
BREAK_MINUTES = 5          # 休息时长（分钟）

# 以脚本所在目录为基准，保证从任意 cwd 运行都读写同一份数据
BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "pomodoro_data.json"   # 数据文件路径
STATE_FILE = BASE_DIR / "pomodoro_state.json"  # 状态文件路径


# ==================== 纯逻辑（不依赖 IO，便于单元测试） ====================
def compute_remaining(state, now=None):
    """根据状态推算剩余秒数（可为负值），纯函数。

    - running：elapsed = elapsed_seconds + (now - start_time)
    - paused / idle：elapsed = elapsed_seconds
    """
    now = now or datetime.now()
    elapsed = float(state.get("elapsed_seconds", 0))
    if state.get("status") == "running":
        start_iso = state.get("start_time")
        if start_iso:
            try:
                elapsed += (now - datetime.fromisoformat(start_iso)).total_seconds()
            except ValueError:
                pass  # start_time 无法解析，仅按已累计秒数推算
    target = float(state.get("target_seconds", WORK_MINUTES * 60))
    return target - elapsed


def compute_pause_state(state, now=None):
    """由运行态推算暂停态（纯函数）：累计已运行秒数并把 start_time 置空。"""
    now = now or datetime.now()
    target = float(state.get("target_seconds", WORK_MINUTES * 60))
    elapsed = target - compute_remaining(state, now)
    paused = dict(state)
    paused["elapsed_seconds"] = elapsed
    paused["status"] = "paused"
    paused["start_time"] = None
    return paused


def compute_resume_state(state, now=None):
    """由暂停态推算恢复后的运行态（纯函数）：start_time 置为当前时间。"""
    now = now or datetime.now()
    resumed = dict(state)
    resumed["status"] = "running"
    resumed["start_time"] = now.isoformat()
    return resumed


def is_state_stale(state, now=None):
    """判断 running 状态是否属于异常残留（进程被强杀/断电）。

    当 status==running 且 (now - start_time) 已明显超过 target_seconds
    （含 60 秒缓冲），或 start_time 缺失/无法解析时，判定该会话早已失效。
    """
    if state.get("status") != "running":
        return False
    start_iso = state.get("start_time")
    if not start_iso:
        return True
    target = float(state.get("target_seconds", WORK_MINUTES * 60))
    now = now or datetime.now()
    try:
        elapsed = (now - datetime.fromisoformat(start_iso)).total_seconds()
    except ValueError:
        return True
    return elapsed > target + 60


def build_session_record(state, now=None):
    """由完成时的状态生成一条历史记录（纯函数，不含 IO）。

    tag 字段：仅当存在且非空时写入，保证旧数据/无标签记录不携带该字段。
    """
    now = now or datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    start_iso = state.get("start_time")
    if start_iso:
        try:
            start_time_str = datetime.fromisoformat(start_iso).strftime("%H:%M:%S")
        except ValueError:
            start_time_str = now.strftime("%H:%M:%S")
    else:
        start_time_str = now.strftime("%H:%M:%S")
    end_time_str = now.strftime("%H:%M:%S")
    target = int(float(state.get("target_seconds", WORK_MINUTES * 60)))
    session = {
        "date": date_str,
        "start_time": start_time_str,
        "end_time": end_time_str,
        "duration_minutes": target // 60,
        "completed": True,
        "mode": state.get("mode", "work"),
    }
    tag = state.get("tag")
    if tag:
        session["tag"] = tag
    return session


def aggregate_stats(sessions, date_str):
    """按日期聚合会话统计（纯函数，便于测试）。

    返回: work_count / break_count / total_work_minutes / total_break_minutes /
          tag_summary（工作番茄钟按标签：count 与 minutes）/ completed_sessions
    """
    day_sessions = [s for s in sessions if s.get("date") == date_str]
    completed = [s for s in day_sessions if s.get("completed", False)]
    work = [s for s in completed if s.get("mode") == "work"]
    breaks = [s for s in completed if s.get("mode") == "break"]

    tag_summary = {}
    for s in work:
        tag = (s.get("tag") or "").strip()
        if not tag:
            continue  # 旧数据无 tag 字段，按无标签处理，不参与标签统计
        entry = tag_summary.setdefault(tag, {"count": 0, "minutes": 0})
        entry["count"] += 1
        entry["minutes"] += int(s.get("duration_minutes", 0))

    return {
        "work_count": len(work),
        "break_count": len(breaks),
        "total_work_minutes": sum(int(s.get("duration_minutes", 0)) for s in work),
        "total_break_minutes": sum(int(s.get("duration_minutes", 0)) for s in breaks),
        "tag_summary": tag_summary,
        "completed_sessions": completed,
    }


# ==================== 数据管理 ====================
def _idle_state():
    """空闲态的默认状态字典"""
    return {
        "status": "idle",   # idle, running, paused
        "mode": "work",     # work, break
        "start_time": None,
        "elapsed_seconds": 0,
        "target_seconds": WORK_MINUTES * 60,
        "tag": None,
    }


def load_data():
    """加载番茄钟历史数据（含损坏容错）"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"sessions": []}
            if not isinstance(data.get("sessions"), list):
                data["sessions"] = []
            return data
        except (json.JSONDecodeError, IOError):
            pass
    return {"sessions": []}


def save_data(data):
    """保存番茄钟历史数据"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_state():
    """加载当前番茄钟状态。

    - 兼容旧版状态文件缺失字段
    - 崩溃残留检测：running 却远超目标时长 → 自动清理并返回空闲态
    """
    if os.path.exists(STATE_FILE):
        state = None
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except (json.JSONDecodeError, IOError):
            state = None
        if isinstance(state, dict):
            # 兼容旧版本状态文件缺失的字段
            state.setdefault("status", "idle")
            state.setdefault("mode", "work")
            state.setdefault("start_time", None)
            state.setdefault("elapsed_seconds", 0)
            state.setdefault("tag", None)
            state.setdefault(
                "target_seconds",
                WORK_MINUTES * 60 if state["mode"] == "work" else BREAK_MINUTES * 60,
            )
            # 崩溃残留：status==running 但 start_time 早已超时 → 视为会话已失效
            if state["status"] == "running" and is_state_stale(state):
                print("⚠️  检测到上次番茄钟会话异常中断（状态残留），已自动清理，请重新开始")
                clear_state()
                return _idle_state()
            return state
    return _idle_state()


def save_state(state):
    """保存当前番茄钟状态"""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def clear_state():
    """清除状态文件"""
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except OSError:
        pass


# ==================== 时间工具 ====================
def get_current_date():
    """获取当前日期字符串"""
    return datetime.now().strftime("%Y-%m-%d")


def get_current_time():
    """获取当前时间字符串"""
    return datetime.now().strftime("%H:%M:%S")


def format_duration(seconds):
    """将秒数格式化为 mm:ss"""
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def format_duration_hms(seconds):
    """将秒数格式化为 hh:mm:ss"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


# ==================== 番茄钟核心逻辑 ====================
def start_pomodoro(mode="work", tag=None):
    """开始番茄钟。

    mode: "work"/"break"；tag: 可选任务标签。
    - 已在运行：提示并返回
    - 已有暂停会话：继续原会话（保留其标签），忽略新传入的 tag
    - 空闲：全新开始（携带 tag）
    """
    state = load_state()

    if state["status"] == "running":
        print("⚠️  番茄钟已在运行中！")
        print(f"   当前模式: {'工作' if state['mode'] == 'work' else '休息'}")
        if state.get("tag"):
            print(f"   📌 任务标签: {state['tag']}")
        remaining = max(0, compute_remaining(state))
        print(f"   剩余时间: {format_duration(int(remaining))}")
        return

    if state["status"] == "paused":
        print("▶️  继续番茄钟...")
        resumed = compute_resume_state(state)
        save_state(resumed)
        run_timer(resumed)
        return

    # 全新开始
    target = WORK_MINUTES * 60 if mode == "work" else BREAK_MINUTES * 60
    state = {
        "status": "running",
        "mode": mode,
        "start_time": datetime.now().isoformat(),
        "elapsed_seconds": 0,
        "target_seconds": target,
        "tag": tag,
    }
    save_state(state)

    mode_name = "工作" if mode == "work" else "休息"
    duration = WORK_MINUTES if mode == "work" else BREAK_MINUTES
    print(f"🍅 开始{mode_name}番茄钟 ({duration}分钟)")
    if tag:
        print(f"   📌 任务标签: {tag}")
    print("   按 Ctrl+C 可暂停")

    run_timer(state)


def run_timer(state):
    """运行计时器（前台阻塞）。

    - 每秒刷新剩余时间；剩余<=0 的检测放在 sleep 之前
    - Ctrl+C：先判断是否实际已超时 → 是则直接完成会话，否则转为暂停
    """
    try:
        while True:
            # 计算剩余时间（剩余<=0 检测在 sleep 前）
            remaining = compute_remaining(state)
            if remaining <= 0:
                complete_session(state)
                return

            # 显示倒计时
            line = f"\r   ⏱️  剩余: {format_duration(int(remaining))} "
            tag = state.get("tag")
            if tag:
                line += f"📌 {tag} "
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(1)

    except KeyboardInterrupt:
        now = datetime.now()
        # 竞态边界：若此刻实际已超时，直接完成会话而非误入暂停分支
        if compute_remaining(state, now) <= 0:
            print()  # 收尾倒计时行
            complete_session(state)
            return

        # 暂停
        paused = compute_pause_state(state, now)
        save_state(paused)
        print(f"\n\n⏸️  已暂停 (已运行 {format_duration(int(paused['elapsed_seconds']))})")
        tag = paused.get("tag")
        if tag:
            print(f"   📌 任务标签: {tag}")


def complete_session(state):
    """完成一个番茄钟会话"""
    session = build_session_record(state)

    # 保存到历史数据
    data = load_data()
    data["sessions"].append(session)
    save_data(data)

    # 清除状态
    clear_state()

    # 显示完成信息
    mode_name = "工作" if session["mode"] == "work" else "休息"
    print(f"\n\n🎉 {mode_name}番茄钟完成！")
    print(f"   时长: {session['duration_minutes']}分钟")
    print(f"   完成时间: {session['end_time']}")
    if session.get("tag"):
        print(f"   📌 任务标签: {session['tag']}")

    # 显示今日统计
    print()
    show_stats()


def pause_pomodoro():
    """暂停番茄钟。

    单进程架构下无法真正暂停另一个终端正在运行的计时进程，
    因此本命令仅给出准确提示；真正的暂停需在运行番茄钟的终端按 Ctrl+C。
    """
    state = load_state()

    if state["status"] == "idle":
        print("ℹ️  没有正在运行的番茄钟")
        return

    if state["status"] == "paused":
        print("ℹ️  番茄钟已处于暂停状态")
        remaining = max(0, compute_remaining(state))
        print(f"   已运行: {format_duration(int(state['elapsed_seconds']))}")
        print(f"   剩余: {format_duration(int(remaining))}")
        return

    if state["status"] == "running":
        print("ℹ️  本 pause 命令无法真正暂停另一终端正在运行的计时进程（单进程架构）。")
        print("   真正的暂停：请在运行番茄钟的那个终端按 Ctrl+C；")
        print("   之后可用 'pomodoro.py resume' 继续。")


def resume_pomodoro():
    """继续番茄钟"""
    state = load_state()

    if state["status"] == "idle":
        print("ℹ️  没有可继续的番茄钟，请使用 'start' 开始新的番茄钟")
        return

    if state["status"] == "running":
        print("⚠️  番茄钟已在运行中！")
        return

    if state["status"] == "paused":
        print("▶️  继续番茄钟...")
        resumed = compute_resume_state(state)
        save_state(resumed)
        run_timer(resumed)


def reset_pomodoro():
    """重置番茄钟"""
    state = load_state()

    if state["status"] == "idle":
        print("ℹ️  没有需要重置的番茄钟")
        return

    clear_state()
    print("🔄 番茄钟已重置")


def show_status():
    """显示当前番茄钟状态"""
    state = load_state()

    if state["status"] == "idle":
        print("📋 当前状态: 空闲")
        print("   使用 'pomodoro.py start' 开始番茄钟")
        return

    mode_name = "工作" if state["mode"] == "work" else "休息"
    tag = state.get("tag")
    remaining = max(0, compute_remaining(state))

    if state["status"] == "running":
        elapsed = max(0, state["target_seconds"] - remaining)
        print(f"📋 当前状态: 运行中 ({mode_name})")
        print(f"   已运行: {format_duration(int(elapsed))}")
        print(f"   剩余: {format_duration(int(remaining))}")
    else:
        print(f"📋 当前状态: 已暂停 ({mode_name})")
        print(f"   已运行: {format_duration(int(state['elapsed_seconds']))}")
        print(f"   剩余: {format_duration(int(remaining))}")

    if tag:
        print(f"   📌 任务标签: {tag}")


def show_stats(date_str=None):
    """显示统计信息"""
    data = load_data()

    if date_str is None:
        date_str = get_current_date()

    stats = aggregate_stats(data["sessions"], date_str)

    # 显示统计
    print(f"📊 {date_str} 番茄钟统计")
    print("=" * 40)
    print(f"  🍅 工作番茄钟: {stats['work_count']} 个")
    print(f"  ☕ 休息番茄钟: {stats['break_count']} 个")
    print(f"  ⏱️  总专注时长: {stats['total_work_minutes']} 分钟 "
          f"({stats['total_work_minutes'] // 60}h {stats['total_work_minutes'] % 60}m)")
    print(f"  🛋️  总休息时长: {stats['total_break_minutes']} 分钟")

    # 按任务标签概览（仅当日存在带标签的工作番茄钟时显示）
    if stats["tag_summary"]:
        print("\n  🏷️  按任务标签（工作番茄钟）:")
        for tag, info in sorted(stats["tag_summary"].items()):
            print(f"     · {tag}: {info['count']} 个 / 专注 {info['minutes']} 分钟")

    # 显示详细记录
    completed = stats["completed_sessions"]
    if completed:
        print("\n  📝 详细记录:")
        for i, s in enumerate(completed, 1):
            mode_icon = "🍅" if s.get("mode") == "work" else "☕"
            tag_part = f" [📌 {s['tag']}]" if s.get("tag") else ""
            print(f"     {i}. {mode_icon} {s['start_time']} - {s['end_time']} "
                  f"({s['duration_minutes']}分钟){tag_part}")


def show_history(days=7):
    """显示最近几天的历史记录"""
    data = load_data()

    if not data["sessions"]:
        print("📭 暂无番茄钟记录")
        return

    # 获取最近几天的日期
    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    print(f"📚 最近 {days} 天番茄钟历史")
    print("=" * 50)

    for date_str in dates:
        sessions = [s for s in data["sessions"] if s["date"] == date_str and s.get("completed")]
        if sessions:
            work = [s for s in sessions if s.get("mode") == "work"]
            breaks = [s for s in sessions if s.get("mode") == "break"]
            work_count = len(work)
            break_count = len(breaks)
            total_minutes = sum(int(s.get("duration_minutes", 0)) for s in work)

            # 本日主要任务标签（出现次数最多的带标签工作番茄钟）
            tag_counts = {}
            for s in work:
                if s.get("tag"):
                    tag_counts[s["tag"]] = tag_counts.get(s["tag"], 0) + 1
            tag_line = ""
            if tag_counts:
                main_tag = max(tag_counts, key=lambda t: (tag_counts[t], t))
                tag_line = f" | 🏷️ {main_tag}"
            print(f"  📅 {date_str}: 🍅×{work_count} ☕×{break_count} | "
                  f"专注 {total_minutes}分钟{tag_line}")


# ==================== 交互式菜单 ====================
def _start_from_menu(mode):
    """从交互菜单开始番茄钟：空闲时才询问任务标签（可回车跳过）"""
    state = load_state()
    tag = None
    if state["status"] == "idle":
        tag = input("   任务标签（可回车跳过）: ").strip() or None
    start_pomodoro(mode, tag)


def interactive_menu():
    """交互式菜单"""
    while True:
        print("\n" + "=" * 40)
        print("🍅 番茄钟 - 交互式菜单")
        print("=" * 40)
        print("  1. 开始工作番茄钟 (25分钟，可带标签)")
        print("  2. 开始休息番茄钟 (5分钟，可带标签)")
        print("  3. 继续番茄钟")
        print("  4. 查看状态")
        print("  5. 今日统计")
        print("  6. 历史记录")
        print("  7. 重置番茄钟")
        print("  0. 退出")
        print("-" * 40)

        choice = input("请选择操作 (0-7): ").strip()

        if choice == "1":
            _start_from_menu("work")
        elif choice == "2":
            _start_from_menu("break")
        elif choice == "3":
            resume_pomodoro()
        elif choice == "4":
            show_status()
        elif choice == "5":
            show_stats()
        elif choice == "6":
            show_history()
        elif choice == "7":
            reset_pomodoro()
        elif choice == "0":
            print("👋 再见！")
            break
        else:
            print("❌ 无效选择，请重试")


# ==================== 命令行入口 ====================
def main():
    parser = argparse.ArgumentParser(
        description="🍅 番茄钟CLI应用 - 提高工作效率的好帮手（支持任务标签）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python pomodoro.py start                       # 开始工作番茄钟
  python pomodoro.py start --break               # 开始休息番茄钟
  python pomodoro.py start --tag 写周报          # 带任务标签开始工作番茄钟
  python pomodoro.py start --break --tag 午休    # 休息也可带标签
  python pomodoro.py resume                      # 继续暂停的番茄钟
  python pomodoro.py pause                       # 仅提示：真正的暂停需在运行终端按 Ctrl+C
  python pomodoro.py status                      # 查看当前状态
  python pomodoro.py stats                       # 查看今日统计
  python pomodoro.py history                     # 查看历史记录
  python pomodoro.py reset                       # 重置番茄钟
  python pomodoro.py                             # 进入交互式菜单
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # start 命令
    start_parser = subparsers.add_parser("start", help="开始番茄钟（可 --break 休息、--tag 打标签）")
    start_parser.add_argument("--break", dest="is_break", action="store_true", help="开始休息番茄钟")
    start_parser.add_argument("--tag", help="任务标签（可选），如 --tag 写周报")

    # resume 命令
    subparsers.add_parser("resume", help="继续暂停的番茄钟")

    # pause 命令
    subparsers.add_parser(
        "pause",
        help="提示如何暂停（真正的暂停需在运行番茄钟的终端按 Ctrl+C）",
    )

    # status 命令
    subparsers.add_parser("status", help="查看当前状态")

    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="查看统计")
    stats_parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)，默认为今天")

    # history 命令
    history_parser = subparsers.add_parser("history", help="查看历史记录")
    history_parser.add_argument("--days", type=int, default=7, help="显示最近几天 (默认7天)")

    # reset 命令
    subparsers.add_parser("reset", help="重置番茄钟")

    args = parser.parse_args()

    if args.command is None:
        # 无参数，进入交互式菜单
        interactive_menu()
    elif args.command == "start":
        mode = "break" if args.is_break else "work"
        start_pomodoro(mode, args.tag)
    elif args.command == "resume":
        resume_pomodoro()
    elif args.command == "pause":
        pause_pomodoro()
    elif args.command == "status":
        show_status()
    elif args.command == "stats":
        show_stats(args.date)
    elif args.command == "history":
        show_history(args.days)
    elif args.command == "reset":
        reset_pomodoro()


if __name__ == "__main__":
    main()
