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

## v0.4.0 — 记忆系统 + 任务共享状态 + 缺陷修复
### 缺陷修复(用户实测发现, 固化为铁律)
- handoff工具描述为空: f-string docstring不是docstring(非字符串字面量)，
  动态描述必须走@tool(description=...)参数
- 单文件完整应用未调度: "多文件"字样误导判据；判据重写为三维量化
  (交付形态完整度/技术跨度/明确指令)，且判据唯一来源=工具描述
### 新增
- memory/: SessionManager(5轮短期+每5轮压缩+摘要滚动+价值提取入库)
  MemoryDB(会话/消息/摘要/preference|fact|entity分库/FTS5+可选向量)
- tasks/: TaskStore共享状态(计划书/进度/任务子目录)
- tools/task_tools.py: 通用侧create/update计划书+进度+验收; coder侧读计划+报进度
- workspace_files工厂化: coder绑任务子目录, 通用全局只读
### 已知债务
- SQLite单写者, 多线程未处理(→v0.5 API并发); 长期记忆仅精确去重(语义去重→后续)

## v0.5.0 — API服务化(鉴权+串行队列+SSE)
### 新增
- api/bus.py: 事件总线(线程安全发布/回放缓冲/TTL清理)
- api/worker.py: 单worker串行队列 + Turn注册表(提交/消费分离)
- api/server.py: chat提交/turn与task双SSE流/history/轮询兜底
- 鉴权: X-API-Key, fail-closed(空key拒启), /health豁免
### 变更
- agents/base.py: handoff改stream, coder内部动作实时推task话题
- tools/task_tools.py: update_progress发布progress/task_done事件
- memory/store.py + tasks/store.py: 线程安全修复(清偿v0.4债务)
### 设计决策
- 提交/消费分离: 长任务不挂HTTP请求, POST返回turn_id, SSE消费
- task SSE: DB为进度事实源, 先订阅后读库按pid去重
### 已知债务
- 并行coder需每任务独立venv; turn注册表内存态(重启丢, history兜底);
  浏览器EventSource无法带自定义头(未来Web端需token URL或cookie方案);
  公网暴露需加HTTPS反代(→v1.0); 无CORS(未来Web端补)

## v0.6.0 — 私有知识库(RAG)
### 新增
- kb/parsers.py: 解析器注册表(md/markdown/txt; 围栏内#不误判)
- kb/chunking.py: 结构感知分块(标题路径溯源/小节合并/超长硬切+重叠)
- kb/store.py: kb.db + RRF混合检索(向量+FTS5, 未配置embedding自动降级)
- kb/ingest.py + __main__: import/reindex/stats/search CLI
- tools/knowledge_tools.py: search_knowledge工具(判据全在描述, 铁律)
- embeddings.py: 自memory/store.py提取共享
### 变更
- general.py/coder.py: 挂载search_knowledge + 工作流各一条
### 设计决策
- 独立kb.db(rebuild不伤会话数据); import按source幂等替换
- 溯源: 块自带source+heading_path, 检索结果带引用返回
### 已知债务
- doc/docx/xlsx/csv/PDF解析器(注册表已就位, 加函数即扩);
  目录监听增量更新(用户决策: 后续版本); 内存余弦仅自用规模适用

## v0.7.0 — 可观测性（结构化日志 + 成本/Token 核算）【已实现】
### 范围决策（用户 2026-09-10 确认）
- 范围：日志 + 成本账本（最小可用，满足生产 Tier-1）
- 追踪后端：LangSmith（**本版不做追踪**，该选择留作后续追踪版）
- 成本落地：仅结构化日志（不建 SQLite 账本、不暴露 /metrics）
### 新增
- observability/logging_setup.py：stdlib-only `JsonFormatter`（单行 JSON）+ `RotatingFileHandler`（10MB×5, UTF-8），日志双写 stdout + `logs/agent_pro.log`；`contextvars` 上下文(session/turn/request/agent/model)经 `ContextFilter` 自动注入每条记录；`setup_logging()` 幂等；`context()` 上下文管理器
- observability/cost.py：`TokenUsageCallbackHandler(BaseCallbackHandler)` 兼容读取 LangChain `usage_metadata` 与 `llm_output["token_usage"]`；`MODEL_PRICING` 价格表（deepseek 系列已填，未知模型退化为只记 token）；`summary()` 出按模型拆分 + 估算费用
- observability/__init__.py：统一导出 setup_logging / context / cost handler 等
### 变更
- config.py：加 `LOG_LEVEL` / `LOG_DIR`
- api/__main__.py / main.py：启动调用 `setup_logging`
- api/server.py：加 HTTP 中间件（request_id + method/path/status/耗时）
- api/worker.py：每 turn 独立 cost handler（经 contextvar 透传给 coder 子 Agent 累加同一笔账）；turn 起止/异常结构化日志；`dispatch` 调度决策日志；turn 结束记 `cost_summary`
- agents/coder.py：handoff 透传 cost handler + 标注 agent="coder" 上下文
- .env.example：补 LOG_LEVEL / LOG_DIR
### 设计要点（相对方案微调）
- cost handler **按 turn 作用域**（每轮新建，经 contextvar 注入 worker/coder 共享），而非方案原写的「挂到 agent 构造全局」——避免跨 turn 累加；turn 结束即读 `summary()`
- 上下文用 `with observe.context(...)` 在进入作用域时设置、退出时还原，支持嵌套（coder 内 agent 标签覆盖，退出后回退 general）
### 执行方案
- 详见 `docs/plans/v0.7-execution-plan.md`
### 已知边界
- 不含 tracing / 成本库 / /metrics / 看板（按决策）

## v0.8.0 — 代码执行隔离沙箱（含 HITL 审批闸）【已实现】
### 版本号决策
- 本版覆盖原蓝图 v0.8「权限注册」；原蓝图 v0.8 权限注册顺延为 **v0.9**（其「危险操作审批闸」已在本版以基础形态落地，完整工具级权限模型留 v0.9）。
### 范围决策（用户 2026-09-11 拍板）
- 隔离技术：**WSL2 子环境**（备选 Docker 隔离最强但需装 Desktop/冷启动慢；受限子进程零依赖但同 OS 用户、能读宿主机 .env）
- 触发范围：**仅 coder 自验/测试**（备选全部 exec 工具工作量过大；仅危险操作需界定且普通读仍暴露）
- 网络策略：**允许出网（pip 可用）**（备选完全禁网则测试装不了依赖；白名单域名需维护）
- HITL 审批：**执行前需确认**（备选全自动则危险代码直接执行风险高）
### 目标
- coder 交付前对生成代码做自验，执行发生在 WSL2 隔离子环境，网络放行，每次执行前需用户确认；普通对话不进沙箱；直接消化 backlog #24（交付物无验证关卡）
### 新增
- `sandbox/wsl_exec.py`：`run_in_wsl(command, cwd_win, timeout)` —— Windows 路径→`/mnt/c/...` 映射、`bash -lc` 执行、超时硬杀、stdout/stderr 截断(20K/8K)、UTF-8 locale 强制、`WSL_AVAILABLE` 探测；不可用则降级报错（不静默回退宿主机）
- `sandbox/__init__.py`：导出 `run_in_wsl` / `SandboxResult` / `wsl_available`
- `tools/verify_tools.py`：`request_verification` 工具（取 turn_id→注册审批请求→发布 `approval_request` 事件→`threading.Event.wait` 阻塞 worker 线程→结果回填）；共享审批注册表 `register_request/get_request/cleanup_request/try_claim/finalize`（worker 线程写、API 线程读）
- `api/server.py`：`POST /api/turns/{turn_id}/approve`（复用 `require_api_key`）——`approve=false` 拒绝；`approve=true` 经 `asyncio.to_thread(run_in_wsl)` 沙箱执行并回填；双点击由 `try_claim` 原子占位防重入
### 变更
- `observability/logging_setup.py`：加 `get_turn_id()`（工具判断是否在轮次内）
- `agents/coder.py`：挂载 `request_verification` 到 CODER_TOOLS；`CODER_SYSTEM_PROMPT` 加「交付前自验」步与沙箱约束（禁读 .env/宿主目录、cwd_win 必传工作区路径）
- `api/worker.py`：无改动（审批状态机在工具+端点+注册表协作完成，worker 仅透传上下文）
### 设计要点
- 阻塞安全：worker 经 `asyncio.to_thread` 跑 `_execute`，`Event.wait` 在工作线程，不占事件循环（端点照常处理）；上下文经 contextvars 透传，工具内能取到 turn_id
- 中等隔离定位：防误用 + 进程/目录隔离，**不防针对性恶意代码读 Windows 盘**（WSL 默认挂 /mnt/c）
### 执行方案
- 详见 `docs/plans/v0.8-execution-plan.md`
### 已知边界
- 隔离强度中等（非 Docker 级）；审批等待时串行整队挂起（异步中断 LangGraph `interrupt()` 留后续）；允许出网（白名单域名留后续）；完整权限模型顺延 v0.9；仅超时护栏无 CPU/内存硬限额；CLI 模式无 turn_id 故不触发审批（仅 API 模式支持，自验需走 API）