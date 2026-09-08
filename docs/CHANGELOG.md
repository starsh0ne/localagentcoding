	# Changelog
	## v0.2.0 — 工程化骨架 + 多Agent调度架构
	### 新增
	- src/包结构、config.py（Pydantic Settings + 统一模型构造）
	- agents/base.py：AgentSpec + AgentRegistry 调度接口
	- agents/coder.py：Coder骨架；agents/general.py：意图识别+调度中枢
	- pyproject.toml / .env.example / Makefile / CHANGELOG
	### 变更
	- main.py 瘦身为薄CLI入口；v0.1工具迁入 tools/builtin.py
	### 设计决策
	- 调度采用 Agent-as-Tool 模式（弃前置分类路由，理由：可扩展/可连续调度）
	- 子Agent不持有调度工具，两级扁平防死循环
	### 已知债务
	- calculator eval注入（→v0.3沙箱）；会话无持久化无压缩（→v0.4）
	## v0.1.0 — 最小智能体循环
	- 单文件：LLM + 2工具 + 裸循环