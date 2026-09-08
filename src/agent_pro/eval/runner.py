"""评估运行器 (v0.6)

针对数据集 (config.EVAL_DATASET_PATH) 批量运行 Agent，统计回答质量、
工具调用准确率、RAG 引用命中率等指标，输出评测报告用于回归与 A/B。
"""

# TODO(v0.6): 实现评估运行器
#   def run_eval(dataset_path, agent) -> EvalReport:
#     - 加载用例（问题 / 期望 / 评分标准）
#     - 逐条调用 create_agent 产出的 agent
#     - 聚合指标（准确率、延迟、成本、引用命中）
