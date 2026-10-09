# ============================================================
# eval/runner.py | v1.0 实现（v0.6 仅留 TODO）
# 加载数据集(问题/期望来源) → 逐条跑 general agent → 统计 RAG 引用命中率等。
# 用法: python -m agent_pro.eval.runner
# 说明: 数据集覆盖 knowledge/ 私有文档; 问题设计刻意触发 search_knowledge,
#       以测量"检索是否真的被引用(可溯源)"——即简历里的"检索命中率"。
# ============================================================
import contextvars
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_pro.agents import create_app_agent
from agent_pro.config import get_settings
from agent_pro.observability import (context, get_logger, reset_cost_handler,
                                     set_cost_handler, setup_logging,
                                     TokenUsageCallbackHandler)
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
        cost = (f"${self.total_cost_usd:.4f}"
                if self.total_cost_usd > 0 else "N/A(未知模型价/免费)")
        return (f"[Eval] 共{self.total}条 | 已回答{self.answered} | "
                f"引用命中{self.citation_hits} | 引用命中率{self.citation_hit_rate:.0%} | "
                f"平均延迟{self.avg_latency_s:.1f}s | 成本{cost}")


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


def _invoke_with_timeout(agent, question: str, settings, cost,
                         timeout_s: int) -> dict:
    """在守护线程内跑 agent.invoke, 超时抛 TimeoutError(单条失败不拖垮整轮)。

    背景: agent.invoke 同步阻塞, 无法被事件/信号中断; 单条若卡在超长 LLM 请求会
    拖死整轮评测。这里用「守护线程 + join(timeout)」软超时——超时后线程被弃用,
    因是 daemon 线程故不阻塞进程退出。
    陷阱: contextvars 不会自动跨线程, 必须 copy_context() 显式带入, 否则日志上下文
    (agent/model/turn) 在新线程里会丢失。
    """
    box: dict = {}

    def _target() -> None:
        try:
            box["result"] = agent.invoke(
                {"messages": [("user", question)]},
                config={"recursion_limit": settings.agent_recursion_limit,
                        "callbacks": [cost]})
        except Exception as e:  # 传回主线程, 由 run_one 统一处理
            box["error"] = e

    ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: ctx.run(_target),
                         name="eval-item", daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        raise TimeoutError(f"单条评测超时(>{timeout_s}s), 已跳过")
    if "error" in box:
        raise box["error"]
    return box.get("result") or {}


def run_one(item: EvalItem) -> dict:
    settings = get_settings()
    agent = create_app_agent()
    cost = TokenUsageCallbackHandler(default_model=settings.model_name)
    token = set_cost_handler(cost)
    started = time.monotonic()
    with context(agent="general", model=settings.model_name):
        try:
            result = _invoke_with_timeout(agent, item.question, settings, cost,
                                          settings.eval_item_timeout_s)
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
    # 成本: 按模型聚合 estimated_cost_usd(未知模型价为 None→计 0)
    cost_usd = sum((m.get("estimated_cost_usd") or 0.0)
                   for m in summary.get("by_model", {}).values())
    return {
        "question": item.question,
        "expected_source": item.expected_source,
        "answered": bool(answer and answer != "(无输出)"),
        "cited": cited,
        "latency_s": round(duration, 2),
        "cost_usd": round(cost_usd, 6),
        "answer_preview": answer[:200],
    }


def run_eval(items: list[EvalItem]) -> EvalReport:
    ensure_kb()
    rep = EvalReport(total=len(items))
    for i, it in enumerate(items, 1):
        r = run_one(it)
        rep.items.append(r)
        if r["answered"]:
            rep.answered += 1
        if r["cited"]:
            rep.citation_hits += 1
        rep.avg_latency_s += r["latency_s"]
        rep.total_cost_usd += r["cost_usd"]
        # 进度打到 stdout: 此前 eval 未初始化日志, 全程静默被误判为卡死
        flag = "命中" if r["cited"] else "未引用"
        print(f"[{i}/{len(items)}] {flag} {r['latency_s']}s "
              f"— {it.question[:40]}", flush=True)
        log.info("eval item 完成", extra={"cited": r["cited"],
                                          "q": it.question[:40]})
    if rep.total:
        rep.citation_hit_rate = rep.citation_hits / rep.total
        rep.avg_latency_s = rep.avg_latency_s / rep.total
    return rep


def main() -> None:
    s = get_settings()
    # 关键: eval 是 CLI 入口, 此前从未初始化日志 → 无 handler 导致 INFO 全被丢弃,
    # 控制台与文件日志双双失效(整轮静默, 易被误判卡死)。
    setup_logging(level=s.log_level, log_dir=s.log_dir)
    items = load_dataset(s.eval_dataset_path)
    print(f"数据集 {len(items)} 条 | 单条超时 {s.eval_item_timeout_s}s "
          f"| 日志目录 {s.log_dir}", flush=True)
    rep = run_eval(items)
    print(rep)
    print(json.dumps(rep.items, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
