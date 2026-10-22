# ============================================================
# memory/manager.py | v0.4 诞生（SessionManager: 重建上下文/压缩/提取）
# 压缩策略: 攒满 short+batch 轮 → 保留最近short轮 → 其余LLM压缩
#           摘要滚动合并 + 价值判断(偏好/事实/实体入库, 闲聊删除)
# 压缩LLM失败 → 粗暴拼接兜底, 绝不阻塞主对话
# v1.1变更(取回): 会话摘要常驻 + 长期记忆按当前query语义检索top-k(覆盖
#           preference/fact/entity 三类; 此前只灌全部 preference 且非语义检索);
#           长期记忆读写一律带作用域(user/project/agent)
# ============================================================
import json
from agent_pro.config import build_model, get_settings
from agent_pro.memory.store import KINDS, Scope, get_memory_db

# 各类长期记忆在上下文中的分节标题
_KIND_LABELS = {"preference": "[已知用户偏好]", "fact": "[已知事实]",
                "entity": "[相关实体]"}

COMPACT_PROMPT = """你是记忆压缩器。根据【已有摘要】与【新增对话】，输出严格的JSON（不要其他文字）:
{"summary": "合并后的完整对话摘要（保留：关键决策、结论、待办、项目背景、约定）",
 "memories": [{"kind": "preference|fact|entity", "content": "…"}]}
memories只记录跨会话有长期价值的信息:
- preference: 用户偏好（工具习惯、风格、语言选择等）
- fact: 稳定事实与领域知识（项目背景、技术决策、重要数字）
- entity: 关键实体及关系（人名-角色、项目-技术栈）
闲聊、寒暄、临时性内容一律不记录；没有则memories为空数组。"""


def _extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class SessionManager:
    def __init__(self, session_id: str, scope: Scope | None = None):
        self.sid = session_id
        s = get_settings()
        self.short = s.memory_short_term_rounds
        self.batch = s.memory_compact_batch
        self.top_k = max(1, s.memory_recall_top_k)
        self.db = get_memory_db()
        self.db.ensure_session(session_id, scope)
        # 传入的非空字段逐项覆盖; 未传字段回退到会话创建时的作用域
        session_scope = self.db.get_scope(session_id) or Scope()
        self.scope = Scope(
            scope.user_id if scope and scope.user_id else session_scope.user_id,
            scope.project_id if scope and scope.project_id else session_scope.project_id,
            scope.agent_id if scope and scope.agent_id else session_scope.agent_id,
        )

    def append_user(self, text: str) -> None:
        self.db.append_user(self.sid, text)

    def append_assistant(self, text: str) -> None:
        self.db.append_assistant(self.sid, text)

    def build_messages(self, query: str | None = None) -> list[dict]:
        """每轮从DB重建上下文: [system(摘要+语义召回)] + 最近active消息"""
        msgs = []
        ctx = self._context_message(query)
        if ctx:
            msgs.append({"role": "system", "content": ctx})
        msgs.extend(self.db.active_messages(self.sid))
        return msgs

    def _context_message(self, query: str | None = None) -> str | None:
        """v1.1 两段式: 摘要常驻(短期层) + 长期记忆按当前query语义检索top-k。"""
        parts = []
        summary = self.db.get_summary(self.sid)
        if summary:
            parts.append(f"[此前对话摘要]\n{summary}")
        recalled = self.db.search_memories(
            query or "", limit=self.top_k, scope=self.scope)
        by_kind: dict[str, list[str]] = {}
        for m in recalled:
            by_kind.setdefault(m["kind"], []).append(m["content"])
        for kind in KINDS:  # preference / fact / entity 三类均可召回
            if by_kind.get(kind):
                parts.append(_KIND_LABELS[kind] + "\n"
                             + "\n".join(f"- {c}" for c in by_kind[kind]))
        return "\n\n".join(parts) if parts else None

    def maybe_compact(self) -> None:
        max_round = self.db.max_active_round(self.sid)
        if max_round < self.short + self.batch:
            return
        cutoff = max_round - self.short
        old = self.db.messages_upto(self.sid, cutoff)
        if not old:
            return
        data = self._llm_compact(old)
        self.db.set_summary(self.sid, data["summary"])
        for m in data.get("memories", []):
            self.db.add_memory(m.get("kind", ""), m.get("content", ""),
                               self.sid, scope=self.scope)
        self.db.deactivate_upto(self.sid, cutoff)  # 闲聊至此过期删除(active=0不再入上下文)

    def _llm_compact(self, old_msgs: list[dict]) -> dict:
        prev = self.db.get_summary(self.sid)
        transcript = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in old_msgs)
        payload = f"【已有摘要】\n{prev or '(无)'}\n\n【新增对话】\n{transcript}"
        try:
            resp = build_model().invoke([("system", COMPACT_PROMPT), ("human", payload)])
            data = _extract_json(resp.content)
            if data and isinstance(data.get("summary"), str) and data["summary"].strip():
                data.setdefault("memories", [])
                return data
        except Exception:
            pass
        rough = "\n".join(f"{m['role']}: {m['content'][:100]}" for m in old_msgs)
        return {"summary": (prev + "\n" + rough)[:2000], "memories": []}  # 兜底: 保证流程前进
