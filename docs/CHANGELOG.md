# Changelog
## v0.1.0 — 最小智能体循环
	- 单文件：LLM + 2工具 + 裸循环
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

## v0.3.0 — Coder工作区能力 + 沙箱
### 新增
- tools/sandbox.py: 路径沙箱（越界/穿越/符号链接防护）
- tools/workspace_files.py: list/read/write/delete 四件套（100KB读上限）
- tools/env_tools.py: pip_install（正则白名单+超时+输出截断，uv优先）
### 变更
- calculator: eval → AST白名单求值（清偿v0.1债务，幂指数上限）
- agents/base.py: 调度层加recursion_limit(80)+超限捕获+耗时上报
- agents/coder.py: 接入CODER_TOOLS，提示词改为四步工作流
- main.py: 流式进度，dispatch时提示长任务等待
### 设计决策
- 路径沙箱为唯一守卫；SandboxViolation不进入重试
- 轻量工具集注册（BUILTIN_TOOLS/CODER_TOOLS），完整权限注册延至v0.8
### 已知债务
- 嵌套子Agent进度不可见（→v0.7）；多任务共享workspace无隔离（→v0.4决策）