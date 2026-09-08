# ============================================================
# api/server.py | v0.5 诞生（FastAPI: chat提交 + SSE轮次/任务事件流）
# 鉴权: X-API-Key头，fail-closed(空key拒绝启动)，/health豁免
# SSE: 轮次流带回放(支持晚连接/Last-Event-ID重连); 任务流以DB为
#      准(先订阅→读历史→pid去重，防订阅与读库间丢事件)
# ============================================================
import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agent_pro import __version__
from agent_pro.api.bus import get_event_bus
from agent_pro.api.worker import get_worker
from agent_pro.config import get_settings
from agent_pro.memory.store import get_memory_db
from agent_pro.tasks.store import get_task_store
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
HEARTBEAT_S = 15
async def require_api_key(x_api_key: str = Header(alias="X-API-Key")) -> None:
    if x_api_key != get_settings().api_auth_key:
        raise HTTPException(status_code=401, detail="invalid api key")
class ChatBody(BaseModel):
    message: str
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
    worker_task = asyncio.create_task(get_worker().run())
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        worker_task.cancel()
        cleanup_task.cancel()
app = FastAPI(title="Agent Pro API", version=__version__, lifespan=lifespan)
@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}
@app.post("/api/sessions")
async def new_session(_: None = Depends(require_api_key)):
    return {"session_id":
            f"s-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"}
@app.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatBody, _: None = Depends(require_api_key)):
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="message不能为空")
    turn = get_worker().submit(session_id, body.message.strip())
    return {"turn_id": turn.turn_id, "session_id": session_id, "status": turn.status}
@app.get("/api/chat/turns/{turn_id}")
async def turn_status(turn_id: str, _: None = Depends(require_api_key)):
    turn = get_worker().get(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="turn不存在")
    return {"turn_id": turn.turn_id, "session_id": turn.session_id,
            "status": turn.status, "result": turn.result,
            "error": turn.error, "created_at": turn.created_at}
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