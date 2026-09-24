# ============================================================
# tools/verify_tools.py | v0.8 诞生（HITL 执行审批工具 + 共享审批注册表）
#                        v1.0.2: +CLI 终端审批路径 / +执行目录硬校验
# 机制: coder 自验前调用 request_verification, 先过执行目录守卫(cwd 必须落在
#       工作区内), 再按运行模式分流:
#   - API 模式(有 turn_id): 发布 approval_request 事件 + 阻塞当前 worker 线程
#       (threading.Event.wait) → 端点 POST /api/turns/{id}/approve 唤醒并
#       (若通过)在 WSL 执行回填。
#   - CLI 模式(无 turn_id; main.py 不启动 API 服务, 审批端点不可达): 退化为
#       终端内 y/N 人工确认, 通过后直接 run_in_wsl 执行并回填。审批语义一致:
#       默认拒绝, 只有显式确认才放行。
# 阻塞安全: worker 经 asyncio.to_thread 跑 _execute, Event.wait 在工作线程,
#           不占用 asyncio 事件循环(端点照常处理)。
# ============================================================
import threading
from langchain.tools import tool

from agent_pro.api.bus import get_event_bus
from agent_pro.api.state_store import get_state_store
from agent_pro.config import get_settings
from agent_pro.observability import get_logger, get_turn_id
from agent_pro.sandbox.wsl_exec import WSL_AVAILABLE, run_in_wsl
from agent_pro.tools.sandbox import SandboxViolation, WorkspaceSandbox

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
    # v0.9: 审批元数据持久化(重启可查), Event 原语仍留内存(阻塞必需)
    get_state_store().set_approval_meta(turn_id, {
        "command": command, "cwd_win": cwd_win, "note": note, "decision": None})
    return entry


def get_request(turn_id: str) -> dict | None:
    with _approval_lock:
        return _approval_registry.get(turn_id)


def cleanup_request(turn_id: str) -> None:
    with _approval_lock:
        _approval_registry.pop(turn_id, None)
    get_state_store().clear_approval(turn_id)


def try_claim(turn_id: str) -> bool:
    """原子占位: 把待审(None)置为 'processing', 成功返回 True;
    已处理/不存在/并发抢占返回 False(防双重点击重复执行)。"""
    with _approval_lock:
        e = _approval_registry.get(turn_id)
        if e is None or e["decision"] is not None:
            return False
        e["decision"] = "processing"
    # v0.9: Redis SET NX 原子占位防双批(跨进程/重启冗余保障; 单进程内存锁已够)
    get_state_store().claim_approval(turn_id)
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
    get_state_store().update_approval_meta(turn_id, decision=decision,
                                          result=result, exit_code=exit_code,
                                          duration_s=duration_s)


# ---- 执行目录守卫: cwd_win 由 LLM 给出, 一律视为不可信输入 ----

def resolve_sandbox_cwd(cwd_win: str | None) -> str:
    """把 cwd_win 规范化为工作区内的**绝对**路径; 越界抛 SandboxViolation。

    缺省(None/空白)时返回工作区根。返回绝对路径是必需的: WSL 路径映射
    (wsl_exec._win_to_wsl) 对没有盘符的路径原样透传, 相对路径会让命令在
    WSL 家目录而不是工作区里执行。
    """
    sandbox = WorkspaceSandbox(get_settings().workspace_dir)
    if cwd_win is None or not str(cwd_win).strip():
        return str(sandbox.root)
    return str(sandbox.resolve_abs_safe(cwd_win))


# ---- CLI 模式审批: 无 API 端点可达, 退化为终端内人工确认 ----

def _console_confirm(command: str, cwd_win: str, note: str) -> bool:
    """终端内人工审批。默认拒绝, 仅显式输入 y/yes 才放行。"""
    print("\n" + "-" * 62)
    print("[审批] coder 请求在隔离沙箱(WSL2)执行命令做自验")
    print(f"  命令: {command}")
    print(f"  目录: {cwd_win}")
    if note:
        print(f"  说明: {note}")
    print("  风险: WSL2 默认挂载 /mnt/c, 不防针对性恶意代码读取宿主盘")
    print("-" * 62)
    try:
        answer = input("允许执行? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def _verify_via_console(command: str, cwd_win: str, note: str) -> str:
    """CLI 路径: 终端确认 → 沙箱执行 → 回填真实输出。"""
    if not _console_confirm(command, cwd_win, note):
        log.info("CLI 自验被拒绝", extra={"event_kind": "approval_result",
                                         "approve": False,
                                         "command": command[:200]})
        return ("【审批被拒绝】用户拒绝执行。"
                "请交付现状并说明无法自验，不要编造运行结果。")
    res = run_in_wsl(command=command, cwd_win=cwd_win,
                     timeout=SANDBOX_TIMEOUT_S)
    log.info("CLI 自验·沙箱执行", extra={
        "event_kind": "sandbox_exec",
        "sandbox": "wsl2",
        "command": command[:200],
        "exit_code": res.returncode,
        "duration_s": res.duration_s})
    return f"【审批通过·沙箱执行结果】\n{res.to_text()}"


@tool("request_verification", description=(
    "交付前请求在隔离沙箱(WSL2)执行命令做自验(如运行测试/启动程序), "
    "执行前需用户人工确认(API 模式为审批端点, CLI 模式为终端确认)。\n"
    "参数:\n"
    "- command: 要执行的完整 shell 命令(例如 'uv run --group dev pytest')\n"
    "- cwd_win: 命令运行的 Windows 工作区**绝对**路径; 取值见系统提示中的"
    "【本次任务工作区】, 必须落在工作区边界内(执行前有硬校验, 越界直接拒绝); "
    "省略则默认为工作区根\n"
    "- note: 给用户的说明(为什么需要执行、风险点)\n"
    "约束: 命令必须限定在工作区内; 禁止读宿主机凭据(.env/ssh)或访问 "
    "/mnt/c/Users 等宿主目录; 沙箱允许出网以便 pip 安装依赖。\n"
    "若用户拒绝执行, 应说明无法自验并交付现状(不要编造运行结果)。"
))
def request_verification(command: str, cwd_win: str | None = None,
                         note: str = "") -> str:
    # ① 执行目录守卫先行: 越界直接拒绝, 不进入审批/执行
    try:
        cwd_abs = resolve_sandbox_cwd(cwd_win)
    except SandboxViolation as e:
        log.warning("自验目录越界被拒", extra={
            "event_kind": "sandbox_denied",
            "command": command[:200],
            "cwd_win": cwd_win})
        return (f"错误: {e}\n"
                f"请把 cwd_win 改为本次任务工作区内的绝对路径后重试。")
    # ② WSL 不可用: 绝不静默回退宿主机执行
    if not WSL_AVAILABLE:
        return ("错误: WSL 不可用, 拒绝在宿主机直接执行代码; "
                "请交付现状, 由用户人工验证。")
    turn_id = get_turn_id()
    # ③ CLI 模式(无轮次上下文 → 审批端点不可达): 终端内人工确认
    if not turn_id:
        return _verify_via_console(command, cwd_abs, note)
    # ④ API 模式: HITL 审批闸(注册 → 阻塞 → 端点唤醒)
    entry = register_request(turn_id, command, cwd_abs, note)
    # 发布审批请求事件(SSE 可见)
    get_event_bus().publish(f"turn:{turn_id}", "approval_request", {
        "command": command,
        "cwd_win": cwd_abs,
        "note": note,
        "needs_approval": True})
    log.info("请求执行审批", extra={
        "event_kind": "approval_request",
        "needs_approval": True,
        "command": command[:200],
        "cwd_win": cwd_abs})
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
