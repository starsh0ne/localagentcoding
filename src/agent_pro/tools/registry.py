"""工具注册中心 (v0.3)

统一管理可用工具：注册、按名查找、按配置启用，并转换为 LangChain
BaseTool / tool 装饰器形式供 agent.core.create_agent 装配。

设计要点：
- 工具以装饰器或显式注册方式登记到 REGISTRY。
- 支持从配置 (config.TOOLS_ENABLED) 选择性启用。
- 与 errors.py 配合做调用失败的分类与兜底。
"""

# TODO(v0.3): 实现工具注册中心
#   REGISTRY: dict[str, BaseTool]
#   register_tool(name, tool) / get_tool(name) / list_enabled(settings)
#   提供 @tool 包装并纳入错误处理的统一封装。
