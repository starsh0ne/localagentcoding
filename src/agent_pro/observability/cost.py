# ============================================================
# observability/cost.py | v0.7 诞生（token/费用核算 callback）
# 设计: LangChain BaseCallbackHandler 兼容读取 usage_metadata 与
#       llm_output; 按模型拆分; 未知模型只记 token 不估费。
# ⚠ 价格表会过时，厂商调价须同步更新下方 MODEL_PRICING。
# ============================================================
from collections import defaultdict
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

# 价格单位: 美元 / 1K tokens (prompt=输入, completion=输出)
# 仅填已知公开价; 缺失模型退化为「只记 token，不估费」。
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"prompt": 0.00027, "completion": 0.00110},
    "deepseek-reasoner": {"prompt": 0.00055, "completion": 0.00219},
    # 美团 LongCat(若经 OpenAI 兼容网关计费，按实际查价填入)
    "LongCat-2.0": {"prompt": 0.0, "completion": 0.0},
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    price = MODEL_PRICING.get(model)
    if not price or (price["prompt"] == 0 and price["completion"] == 0):
        return None  # 未知或零价: 不估费，避免误导
    return (prompt_tokens / 1000.0) * price["prompt"] + \
           (completion_tokens / 1000.0) * price["completion"]


def _extract_usage(response: Any) -> tuple[int, int]:
    """从 LLMResult 兼容读取 (prompt_tokens, completion_tokens)。"""
    prompt = completion = 0
    for gens in getattr(response, "generations", []) or []:
        for gen in gens:
            msg = getattr(gen, "message", None)
            um = getattr(msg, "usage_metadata", None) if msg is not None else None
            if um:
                prompt += int(um.get("prompt_tokens") or um.get("input_tokens") or 0)
                completion += int(um.get("completion_tokens") or um.get("output_tokens") or 0)
    lo = getattr(response, "llm_output", None) or {}
    tu = (lo.get("token_usage") or lo.get("usage") or {})
    if tu:
        prompt = prompt or int(tu.get("prompt_tokens") or tu.get("input_tokens") or 0)
        completion = completion or int(tu.get("completion_tokens") or tu.get("output_tokens") or 0)
    return prompt, completion


def _model_name(response: Any) -> str | None:
    lo = getattr(response, "llm_output", None) or {}
    return lo.get("model_name")


class TokenUsageCallbackHandler(BaseCallbackHandler):
    """累加一次 run(通常=一个 turn)内的 token，结束可出 cost_summary。"""

    def __init__(self, default_model: str = ""):
        self.default_model = default_model
        # model -> {"prompt": int, "completion": int}
        self.by_model: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt": 0, "completion": 0})

    def on_llm_end(self, response, *, run_id=None, parent_run_id=None,
                   tags=None, metadata=None, **kwargs) -> None:
        prompt, completion = _extract_usage(response)
        if prompt == 0 and completion == 0:
            return
        model = (_model_name(response) or self.default_model or "unknown")
        self.by_model[model]["prompt"] += prompt
        self.by_model[model]["completion"] += completion

    def on_llm_error(self, error, *, run_id=None, parent_run_id=None,
                     tags=None, metadata=None, **kwargs) -> None:
        pass  # 错误不计费，异常由调用方结构化记录

    def summary(self, session_id: str = "", turn_id: str = "") -> dict:
        total_prompt = sum(m["prompt"] for m in self.by_model.values())
        total_completion = sum(m["completion"] for m in self.by_model.values())
        per_model = {}
        for model, vals in self.by_model.items():
            cost = estimate_cost(model, vals["prompt"], vals["completion"])
            per_model[model] = {
                "prompt_tokens": vals["prompt"],
                "completion_tokens": vals["completion"],
                "estimated_cost_usd": round(cost, 6) if cost is not None else None,
            }
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "by_model": per_model,
        }
