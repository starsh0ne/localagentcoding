# ============================================================
# eval/runner.py | v1.0 实现（v0.6 仅留 TODO）
# 加载数据集(问题/期望来源) → 逐条跑 general agent → 统计 RAG 引用命中率等。
# 用法: python -m agent_pro.eval.runner
# 说明: 数据集覆盖 knowledge/ 私有文档; 问题设计刻意触发 search_knowledge,
#       以测量"检索是否真的被引用(可溯源)"——即简历里的"检索命中率"。
# ============================================================
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_pro.agents import create_app_agent
from agent_pro.config import get_settings
from agent_pro.observability import (context, get_logger, reset_cost_handler,
                                     set_cost_handler, TokenUsageCallbackHandler)
from agent_pro.kb.ingest import ingest_dir
from agent_pro.kb.store import get_kb_store

log = get_logger("eval")


@dataclass
class EvalItem:
    question: str
    expected_source: str  # 期望被引用的文档 source 子串(文件名)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalItem":
        return cls(question=d["question"], expected_source=d["expected_source"])


@dataclass
class EvalReport:
    total: int = 0
    answered: int = 0
    citation_hits: int = 0
    citation_hit_rate: float = 0.0
    avg_latency_s: float = 0.0
    total_cost_usd: float = 0.0
    items: list = field(default_factory=list)

    def __str__(self) -> str:
        return (f"[Eval] 共{self.total}条 | 已回答{self.answered} | "
                f"引用命中{self.citation_hits} | 引用命中率{self.citation_hit_rate:.0%} | "
                f"平均延迟{self.avg_latency_s:.1f}s | 成本${self.total_cost_usd:.4f}")


def load_dataset(path) -> list[EvalItem]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EvalItem.from_dict(d) for d in data]


def ensure_kb() -> None:
    """KB 为空时自动导入 knowledge/（embedding 缺失则降级纯 FTS，检索仍可用）。"""
    s = get_settings()
    store = get_kb_store()
    st = store.stats()
    if st["chunks"] == 0:
        log.info("KB 为空, 自动导入", extra={"kb_dir": str(s.kb_dir)})
        rep = ingest_dir(s.kb_dir)
        log.info("KB 导入完成", extra={"report": str(rep)})


def run_one(item: EvalItem) -> dict:
    settings = get_settings()
    agent = create_app_agent()
    cost = TokenUsageCallbackHandler(default_model=settings.model_name)
    token = set_cost_handler(cost)
    started = time.monotonic()
    with context(agent="general", model=settings.model_name):
        try:
            result = agent.invoke(
                {"messages": [("user", item.question)]},
                config={"recursion_limit": settings.agent_recursion_limit,
                         "callbacks": [cost]})
            msgs = result.get("messages", [])
            answer = str(msgs[-1].content) if msgs else "(无输出)"
        except Exception as e:  # 单条失败不中断整体
            answer = f"(error: {type(e).__name__}: {e})"
            msgs = []
        finally:
            reset_cost_handler(token)
    duration = time.monotonic() - started
    # 收集所有消息文本, 检查 expected_source 是否出现(检索可溯源)
    blob = "\n".join(str(getattr(m, "content", "")) for m in msgs)
    cited = item.expected_source in blob
    summary = cost.summary()
    return {
        "question": item.question,
        "expected_source": item.expected_source,
        "answered": bool(answer and answer != "(无输出)"),
        "cited": cited,
        "latency_s": round(duration, 2),
        "cost_usd": float(summary.get("total_cost_usd", 0.0) or 0.0),
        "answer_preview": answer[:200],
    }


def run_eval(items: list[EvalItem]) -> EvalReport:
    ensure_kb()
    rep = EvalReport(total=len(items))
    for it in items:
        r = run_one(it)
        rep.items.append(r)
        if r["answered"]:
            rep.answered += 1
        if r["cited"]:
            rep.citation_hits += 1
        rep.avg_latency_s += r["latency_s"]
        rep.total_cost_usd += r["cost_usd"]
        log.info("eval item 完成", extra={"cited": r["cited"], "q": it.question[:40]})
    if rep.total:
        rep.citation_hit_rate = rep.citation_hits / rep.total
        rep.avg_latency_s = rep.avg_latency_s / rep.total
    return rep


def main() -> None:
    s = get_settings()
    items = load_dataset(s.eval_dataset_path)
    rep = run_eval(items)
    print(rep)
    print(json.dumps(rep.items, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
