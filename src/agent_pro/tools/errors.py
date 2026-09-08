"""工具错误分类处理 (v0.3)

定义工具调用相关的错误类型，并统一转化为对 Agent 友好的回退行为
（重试 / 跳过 / 返回错误信息），避免单次工具失败中断整轮对话。
"""

# TODO(v0.3): 定义错误分类
#   class ToolError(Exception) 及子类：
#     - ToolTimeout, ToolAuthError, ToolParseError, ToolRateLimit ...
#   def handle_tool_error(exc) -> str | Retry: 统一回退策略，
#   供 registry 包装工具时调用。
