# ============================================================
# tools/verify_tools.py | v0.8 诞生（HITL 执行审批工具 + 共享审批注册表）
# 机制: coder 自验前调用 request_verification → 发布 approval_request 事件
#       + 阻塞当前 worker 线程(threading.Event.wait) →
#       API 端点 POST /api/turns/{id}/approve 唤醒并(若通过)在 WSL 执行回填。
# 阻塞安全: worker 经 asyncio.to_thread 跑 _execute, Event.wait 在工作线程,
#           不占用 asyncio 事件循环(端点照常处理)。
# ============================================================
import threading
from langchain.tools import tool

from agent_pro.api.bus import get_event_bus
from agent_pro.observability import get_logger, get_turn_id
from agent_pro.sandbox.wsl_exec import WSL_AVAILABLE

log = get_logger("agent.verify")

# ---- 共享审批注册表(turn_id → entry); worker线程写, API线程读 ----
_approval_lock = threading.Lock()
_approval_registry: dict[str, dict] = {}

# 审批等待硬上限(秒); 超时视为拒绝
APPROVE_TIMEOUT_S = 300
# 沙箱执行超时(秒); 防长任务卡死串行整队
SANDBOX_TIMEOUT_S = 180


def register_request(turn_id: str, command: str,
                     cwd_win: str | None, note: str) -> dict:
    entry = {
        "event": threading.Event(),
        "command": command,
        "cwd_win": cwd_win,
        "note": note,
        "decision": None,      # None=待审, True=通过, False=拒绝
        "result": None,        # 通过时填沙箱输出文本
        "exit_code": None,
        "duration_s": None,
    }
    with _approval_lock:
        _approval_registry[turn_id] = entry
    return entry


def get_request(turn_id: str) -> dict | None:
    with _approval_lock:
        return _approval_registry.get(turn_id)


def cleanup_request(turn_id: str) -> None:
    with _approval_lock:
        _approval_registry.pop(turn_id, None)


def try_claim(turn_id: str) -> bool:
    """原子占位: 把待审(None)置为 'processing', 成功返回 True;
    已处理/不存在/并发抢占返回 False(防双重点击重复执行)。"""
    with _approval_lock:
        e = _approval_registry.get(turn_id)
        if e is None or e["decision"] is not None:
            return False
        e["decision"] = "processing"
        return True


def finalize(turn_id: str, decision: bool, result: str | None = None,
             exit_code=None, duration_s=None) -> None:
    """写入最终决策并唤醒阻塞的 worker 线程。"""
    with _approval_lock:
        e = _approval_registry.get(turn_id)
        if e is None:
            return
        e["decision"] = decision
        e["result"] = result
        e["exit_code"] = exit_code
        e["duration_s"] = duration_s
        e["event"].set()


@tool("request_verification", description=(
    "交付前请求在隔离沙箱(WSL2)执行命令做自验(如运行测试/启动程序), "
    "执行前需用户人工确认。\n"
    "参数:\n"
    "- command: 要执行的完整 shell 命令(例如 'uv run --group dev pytest')\n"
    "- cwd_win: 命令运行的 Windows 工作区绝对路径(必须传本次任务工作区, "
    "例如 'C:\\\\Users\\\\26024\\\\Desktop\\\\agent-pro\\\\workspace\\\\pomodoro')\n"
    "- note: 给用户的说明(为什么需要执行、风险点)\n"
    "约束: 命令必须限定在工作区内; 禁止读宿主机凭据(.env/ssh)或访问 "
    "/mnt/c/Users 等宿主目录; 沙箱允许出网以便 pip 安装依赖。\n"
    "若用户拒绝执行, 应说明无法自验并交付现状(不要编造运行结果)。"
))
def request_verification(command: str, cwd_win: str | None = None,
                         note: str = "") -> str:
    turn_id = get_turn_id()
    if not turn_id:
        return ("错误: 当前不在轮次上下文中(API 模式才支持审批); "
                "无法发起沙箱自验, 请改为交付现状说明。")
    if not WSL_AVAILABLE:
        return ("错误: WSL 不可用, 拒绝在宿主机直接执行代码; "
                "请交付现状, 由用户人工验证。")
    entry = register_request(turn_id, command, cwd_win, note)
    # 发布审批请求事件(SSE 可见)
    get_event_bus().publish(f"turn:{turn_id}", "approval_request", {
        "command": command,
        "cwd_win": cwd_win,
        "note": note,
        "needs_approval": True})
    log.info("请求执行审批", extra={
        "event_kind": "approval_request",
        "needs_approval": True,
        "command": command[:200],
        "cwd_win": cwd_win})
    # 阻塞等待用户决策(工作线程, 不占事件循环)
    ok = entry["event"].wait(timeout=APPROVE_TIMEOUT_S)
    try:
        if not ok:
            return ("审批超时(未确认), 已跳过自验。请人工验证交付物。")
        if entry["decision"] is True:
            log.info("执行审批通过·沙箱运行", extra={
                "event_kind": "sandbox_exec",
                "sandbox": "wsl2",
                "command": command[:200],
                "exit_code": entry.get("exit_code"),
                "duration_s": entry.get("duration_s")})
            return f"【审批通过·沙箱执行结果】\n{entry['result'] or '(无输出)'}"
        # 拒绝
        reason = entry["result"] or "用户拒绝执行"
        return f"【审批被拒绝】{reason}\n请据此调整方案或说明无法自验。"
    finally:
        cleanup_request(turn_id)
