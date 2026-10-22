# ============================================================
# api/server.py | v0.5 诞生（FastAPI: chat提交 + SSE轮次/任务事件流）
# 鉴权: X-API-Key头，fail-closed(空key拒绝启动)，/health豁免
# SSE: 轮次流带回放(支持晚连接/Last-Event-ID重连); 任务流以DB为
#      准(先订阅→读历史→pid去重，防订阅与读库间丢事件)
# v1.1: 会话/聊天端点接受可选作用域(user_id/project_id/agent_id), 用于记忆隔离
# ============================================================
import asyncio
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from agent_pro import __version__
from agent_pro.api.bus import get_event_bus
from agent_pro.api.queue import QueueFullError
from agent_pro.api.worker import get_worker
from agent_pro.config import get_settings
from agent_pro.memory.store import Scope, get_memory_db
from agent_pro.observability import context, get_logger
from agent_pro.api.state_store import get_state_store
from agent_pro.sandbox.wsl_exec import run_in_wsl
from agent_pro.tasks.store import get_task_store
from agent_pro.tools.verify_tools import (finalize, get_request, try_claim,
                                          SANDBOX_TIMEOUT_S)
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
log = get_logger("api")
HEARTBEAT_S = 15
async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    # v0.9(#21): 缺头/错头均返 401(原缺头会被 FastAPI 当参数校验失败返 422)
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="missing api key")
    if x_api_key != get_settings().api_auth_key:
        raise HTTPException(status_code=401, detail="invalid api key")
class ScopeFields(BaseModel):
    """v1.1 记忆隔离作用域: 三层可选, 缺省 'default'(向后兼容)。"""

    user_id: str = Field(default="default", max_length=128)
    project_id: str = Field(default="default", max_length=128)
    agent_id: str = Field(default="default", max_length=128)

    def resolve(self, fallback: Scope | None = None) -> Scope:
        """未显式传的字段回退到 fallback(会话创建时的作用域); 空串按 default。"""
        fb = fallback or Scope()
        sent = self.model_fields_set
        def pick(name: str, cur: str, old: str) -> str:
            if name not in sent:
                return old
            v = (cur or "").strip()
            return v or "default"
        return Scope(pick("user_id", self.user_id, fb.user_id),
                     pick("project_id", self.project_id, fb.project_id),
                     pick("agent_id", self.agent_id, fb.agent_id))


class ChatBody(ScopeFields):
    message: str = Field(..., max_length=100_000,
                         description="用户消息(≤100KB)")


class ApproveBody(BaseModel):
    approve: bool
    comment: str = ""
def _sse(evt: dict) -> str:
    return (f"event: {evt['type']}\nid: {evt['seq']}\n"
            f"data: {json.dumps(evt['data'], ensure_ascii=False)}\n\n")
async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(600)
        get_event_bus().cleanup()
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not get_settings().api_auth_key:
        raise RuntimeError("API_AUTH_KEY未配置 — 拒绝以空鉴权启动")
    get_event_bus().bind_loop(asyncio.get_running_loop())
    worker_task = asyncio.create_task(get_worker().run(get_settings().worker_concurrency))
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        worker_task.cancel()
        cleanup_task.cancel()
app = FastAPI(title="Agent Pro API", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    rid = uuid.uuid4().hex
    start = time.monotonic()
    with context(request_id=rid):
        response = await call_next(request)
        duration = time.monotonic() - start
        log.info("HTTP 请求", extra={
            "event_kind": "http",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_s": round(duration, 3)})
    return response


@app.get("/", include_in_schema=False)
async def root():
    """根路径无业务语义: 浏览器访问直接落到 Swagger 交互文档。

    此前根路径未定义路由 → 返回 404, 人工调试体验差(需记住 /docs)。
    用 RedirectResponse 默认 307 临时重定向, 不引入缓存语义。
    """
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}
@app.post("/api/sessions")
async def new_session(body: ScopeFields | None = None,
                      _: None = Depends(require_api_key)):
    """创建会话; body 可选 user_id/project_id/agent_id(默认 'default')。"""
    scope = (body or ScopeFields()).resolve()
    session_id = f"s-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    get_memory_db().ensure_session(session_id, scope)
    log.info("创建 session", extra={"event_kind": "session_create",
                                    "session_id": session_id, **scope._asdict()})
    return {"session_id": session_id, **scope._asdict()}
@app.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatBody, _: None = Depends(require_api_key)):
    if len(session_id) > 128:
        raise HTTPException(status_code=400, detail="session_id 过长(≤128)")
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message不能为空")
    db = get_memory_db()
    # v1.1: 未传作用域字段 → 回退到该会话创建时的作用域
    scope = body.resolve(db.get_scope(session_id))
    # v0.9(#29): 惰性创建 session — 不存在则建(返回是否新建用于日志), 不再静默用任意字符串
    created = db.ensure_session(session_id, scope)
    if created:
        log.info("惰性创建 session", extra={"event_kind": "session_create",
                                            "session_id": session_id,
                                            **scope._asdict()})
    try:
        turn = await get_worker().submit(session_id, body.message.strip(),
                                         scope=scope)
    except QueueFullError:
        # 背压: 队列已满, 拒绝新增提交(防内存无限增长)
        raise HTTPException(status_code=429, detail="队列已满，请稍后重试")
    return {"turn_id": turn.turn_id, "session_id": session_id,
            "status": turn.status}
@app.get("/api/chat/turns/{turn_id}")
async def turn_status(turn_id: str, _: None = Depends(require_api_key)):
    turn = get_worker().get(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="turn不存在")
    return {"turn_id": turn.turn_id, "session_id": turn.session_id,
            "status": turn.status, "result": turn.result,
            "error": turn.error, "created_at": turn.created_at}
@app.post("/api/turns/{turn_id}/approve")
async def approve_turn(turn_id: str, body: ApproveBody,
                       _: None = Depends(require_api_key)):
    """HITL 执行审批闸(v0.8): 确认 coder 自验命令是否在沙箱执行。
    approve=false → 拒绝, agent 调整/说明; approve=true → 在 WSL 沙箱执行并回填。"""
    if get_request(turn_id) is None:
        raise HTTPException(status_code=404, detail="无待审批请求")
    if not try_claim(turn_id):
        raise HTTPException(status_code=409, detail="该请求已处理")
    entry = get_request(turn_id)
    bus = get_event_bus()
    if not body.approve:
        finalize(turn_id, False, result=body.comment or "用户拒绝执行")
        bus.publish(f"turn:{turn_id}", "approval_result",
                    {"approve": False, "comment": body.comment})
        log.info("执行审批拒绝", extra={"event_kind": "approval_result",
                                        "approve": False, "turn_id": turn_id})
        return {"status": "rejected"}
    # 通过 → 沙箱(WSL2)执行; to_thread 不阻塞事件循环
    res = await asyncio.to_thread(
        run_in_wsl, command=entry["command"], cwd_win=entry["cwd_win"],
        timeout=SANDBOX_TIMEOUT_S)
    finalize(turn_id, True, result=res.to_text(),
             exit_code=res.returncode, duration_s=res.duration_s)
    bus.publish(f"turn:{turn_id}", "approval_result",
                {"approve": True, "exit_code": res.returncode,
                 "duration_s": res.duration_s})
    log.info("执行审批通过·沙箱已运行", extra={
        "event_kind": "approval_result", "approve": True,
        "turn_id": turn_id, "exit_code": res.returncode,
        "duration_s": res.duration_s})
    return {"status": "approved", "exit_code": res.returncode,
            "timed_out": res.timed_out}
@app.get("/api/chat/turns/{turn_id}/events")
async def turn_events(turn_id: str, request: Request,
                      _: None = Depends(require_api_key)):
    if get_worker().get(turn_id) is None:
        raise HTTPException(status_code=404, detail="turn不存在")
    try:
        last_seq = int(request.headers.get("last-event-id", "0") or 0)
    except ValueError:
        last_seq = 0
    bus = get_event_bus()
    topic = f"turn:{turn_id}"
    q, replay = bus.subscribe(topic, last_seq=last_seq)
    async def gen():
        try:
            for evt in replay:            # 晚连接/重连: 先补历史
                yield _sse(evt)
                if evt["type"] in ("final", "error"):
                    return               # 终态已在回放中 → 关闭流(v0.9 #19)
            # 回放为空(可能服务刚重启, 总线无缓冲) → 尝试从状态后端恢复终态
            if not replay:
                d = get_state_store().get_turn(turn_id)
                if d and d.get("status") in ("done", "error"):
                    term = "final" if d["status"] == "done" else "error"
                    data = ({"content": d.get("result") or ""}
                            if term == "final" else {"error": d.get("error") or ""})
                    yield _sse({"seq": 0, "type": term, "data": data})
                    return
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"     # 心跳防中间设备断连
                    continue
                yield _sse(evt)
                if evt["type"] in ("final", "error"):
                    break
        finally:
            bus.unsubscribe(topic, q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=SSE_HEADERS)
@app.get("/api/sessions/{session_id}/history")
async def session_history(session_id: str, _: None = Depends(require_api_key)):
    db = get_memory_db()
    return {"session_id": session_id, "summary": db.get_summary(session_id),
            "messages": db.active_messages(session_id)}
@app.get("/api/tasks/{task_id}")
async def task_status(task_id: str, _: None = Depends(require_api_key)):
    store = get_task_store()
    row = store.get(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task不存在")
    progress = store.progress_list(task_id)
    return {"task_id": task_id, "title": row["title"], "status": row["status"],
            "plan": row["plan"],
            "progress": [p["note"] for p in reversed(progress)]}
@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request,
                      _: None = Depends(require_api_key)):
    store = get_task_store()
    if store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task不存在")
    bus = get_event_bus()
    topic = f"task:{task_id}"
    # 顺序关键: 先订阅→再读DB历史→按pid去重（杜绝两步之间的事件丢失）
    q, _ = bus.subscribe(topic, replay=False)
    async def gen():
        max_pid = 0
        try:
            row0 = store.get(task_id)
            yield _sse({"seq": 0, "type": "snapshot",
                        "data": {"task_id": task_id, "title": row0["title"],
                                 "status": row0["status"]}})
            for row in store.progress_history(task_id):
                max_pid = max(max_pid, row["id"])
                yield _sse({"seq": 0, "type": "progress",
                            "data": {"pid": row["id"], "note": row["note"],
                                     "from_db": True}})
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if evt["type"] == "progress" and evt["data"].get("pid", 0) <= max_pid:
                    continue  # DB历史已含，去重
                yield _sse(evt)
                # 不break: task_done后任务仍可被update_task_plan延续
        finally:
            bus.unsubscribe(topic, q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=SSE_HEADERS)