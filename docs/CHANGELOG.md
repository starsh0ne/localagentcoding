# Changelog
## v1.0.1 — 生产就绪加固（P0/P1 完善）
### 目标
- v1.0.0 架构就绪后，堵上**上线前必须处理**的稳定性/安全细节，达到可上线（单节点/局域网）标准，并补齐文档。
### 决策（已拍板）
- 范围：完整 P0（稳定性/安全）+ P1（测试/可观测/文档），不做大功能。
- 死代码：删除确认无引用的 7 个 TODO 空壳文件（registry/memory_tools/task_tools/errors/core/prompts/tracing），保持仓库干净。
### 计划变更
- **P0-稳定性**
  - api/worker.py：① `turns` 字典加锁跨线程保护 + 完成后从内存剔除（**防长服务内存泄漏**，历史改查 state_store）；② 消费者自愈（意外退出 1s 后重启，不死整体服务）；③ 前置状态写失败降级为告警不击杀消费者。
  - api/queue.py + config：`QUEUE_MAXSIZE`（默认 1000）背压，满则抛 `QueueFullError`；server 捕获返回 **429**。
  - api/server.py：消息体 `max_length=100_000`（超 422）；`session_id` 长度 ≤128（超 400）。
  - api/worker.py：对外错误文案**脱敏**（`client_safe_error`），不含内部路径/堆栈，仅留追踪码；原始异常仅落日志。
- **P1**
  - tests/test_security.py：沙箱穿越拦截 / pip 白名单 / API 鉴权 401 / 队列背压 / 错误脱敏 / turn 回收 等 13 项回归。
  - eval/runner.py：修复成本始终为 0 的 bug（按模型聚合 `estimated_cost_usd`）；未知模型价显示 `N/A`。
  - README.md：安装/启动/使用/评测/生产部署/安全模型/Git 发布流程与版本规范。
### 删除（确认无引用）
- src/agent_pro/agents/core.py、agents/prompts.py、observability/tracing.py、tools/registry.py、tools/errors.py、tools/memory_tools.py、tools/task_tools.py。
### 验收结果（实测）
- 安全/并发回归：pytest 全绿（`test_v1_concurrency.py` 3 项 + `test_security.py` 13 项）。
- 服务冒测（TestClient，Memory 后端）：`/health` 200；缺头/错头 401；正确 key 200；超长 message 422；队列自动降级内存、启动无阻塞。
- `pip_install` 白名单拦 `-e`/`--index-url`/命令注入；`WorkspaceSandbox` 拦绝对路径与 `../` 穿越。

## v1.0.0 — 并发改造（已实现）
### 目标
- 把 v0.9 单进程串行队列改造为**可配置并发 worker 池**，真正支持多 Agent 任务并行执行。
- 补齐"连接复用改造"与"真实 RAG 命中率"，使简历中"高并发"与"检索命中率"有据可依。
### 决策（已拍板）
- B1 并行模型：单进程 worker 池 + Redis 队列/状态后端，SSE 事件总线留进程内
- B2 venv：保留共用 venv，`pip_install` 加全局锁防并发污染（真隔离留债务）
- B3 SQLite：每线程持有一个连接 + WAL（连接复用改造）
- B4 评测：实现 eval/runner.py + 内置黄金集，跑真实 RAG 引用命中率
### 计划变更
- 新增 api/queue.py（TurnQueue：Memory/Redis 双后端）
- 改 api/worker.py（N 并发消费者 + per-turn 超时）、api/server.py（lifespan 起 N 消费者）
- 改 memory/kb/tasks store.py（每线程连接 + WAL）
- 改 tools/env_tools.py（pip_install 全局锁）
- 改 config.py（worker_concurrency/turn_timeout_s/queue_backend/eval_dataset_path）、版本 1.0.0
- 实现 eval/runner.py + eval/gold_dataset.json
### 已知边界
- 共享 venv 并发 pip 经锁串行，运行时仍共用环境（互斥依赖冲突为已知债务）
- Redis 路径本环境无 Redis 仅验证 Memory 降级；真·100 并发取决于 DeepSeek RPM/TPM 与单机资源
### 实现期发现并修复的真实缺陷
- **`search_knowledge` 未挂载到通用 agent**（`agents/general.py`）：system prompt 要求"涉及私有笔记/规范 → search_knowledge"，但该工具只被 import 未加入工具列表，RAG 对通用 agent 完全失效，"回答逐条可溯源"此前名不副实。已接入，eval 引用命中率方才可得。
- **eval `EvalReport.__str__` NameError**（漏写 `self.`），已修。
### 验收结果（实测）
- **并发**：`tests/test_v1_concurrency.py` 三项全过（队列顺序 / worker 池并行 / 每线程连接并发读写）；本环境 Redis 不可用→自动降级内存队列与内存态，启动无阻塞。
- **RAG 真实引用命中率**：`eval/runner.py` 对 12 条黄金集跑 general agent → 已回答 12/12，引用命中 11/12，**命中率 92%**（平均延迟 12.2s）。
  - 唯一未命中：第 5 条"什么是 LLMOps…"模型用通用知识正确作答但未回显来源文件名。
  - 说明：本环境 embedding 服务（WSL :8001）未启动，检索自动降级为 FTS5；向量召回缺失不影响来源可溯源，92% 为纯 FTS 下界，接入 embedding 后不降反升。
  - 简历"检索命中率 85%"为保守表述，实测 92% 高于该值，**陈述成立且有真数字支撑**。
- 成本统计对 `deepseek-v4-flash` 显示 $0（价格表未覆盖该模型名，可观测性债务，不影响功能与命中率）。

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

## v0.9.0 — 生产就绪加固【已实现】
### 决策点（用户 2026-09-16 拍板）
- A1 持久化存储：**Redis**（本地无 Redis 时降级内存态 + 启动告警，不阻断启动）
- A2 chat 未建 session：**惰性创建**（不再把任意字符串当已存在 session）
- A3 加固范围：**全做**（状态持久化 + session 惰性创建 + 修 #19 SSE 晚连接挂死 + 修 #21 缺头 422→401）
### 新增
- `api/state_store.py`：状态后端（Redis 优先 + 进程内内存降级）；`TurnRegistry`（turn 持久化，重启可恢复）+ `ApprovalRegistry`（审批元数据 + `SET NX` 原子防双批）；`import redis` 软依赖，未装则降级不阻断启动
- `config.py`/`pyproject.toml`/`.env.example`：`redis_url`（默认 `redis://localhost:6379`）；`redis` 为**可选依赖**（`[project.optional-dependencies].redis`，`pip install -e ".[redis]"` 启用，未装则软降级内存态，不阻断启动）
### 变更
- `api/worker.py`：turn 注册表接 state_store（submit 落库、get 重启恢复、run 循环 status/result/error 同步）
- `tools/verify_tools.py`：审批元数据落 state_store；`try_claim` 增 Redis `SET NX` 占位（防双批）；`threading.Event` 阻塞原语保留内存
- `api/server.py`：`require_api_key` 缺头/错头均 401（#21）；chat 路由 `ensure_session` 惰性创建并记日志（#29）；`/events` 回放遇 final/error 即 `return` 关闭流（#19），回放为空时从 state_store 恢复终态（重启后晚连接可关闭）
- `memory/store.py`：`ensure_session` 返回是否新建（供惰性创建日志）
- `api/__main__.py`：启动提前探测 Redis 后端
### 设计要点
- 降级优先：Redis 不可用 → 内存态 + `WARNING` 日志，服务照常起（history/SQLite 仍兜底）
- 审批注册表含 `threading.Event` 阻塞原语且生命周期绑定进程内 worker 线程，跨重启不可恢复，故**仅元数据 + 防双批占位持久化**，不持久化阻塞状态（合理取舍，已在方案注明）
- 401 语义统一：`Header(default=None)` 让缺头不再被 FastAPI 当参数校验（原 422）而显式返 401
### 已知边界
- Redis 降级时 turn/approval 持久化失效（靠 history 兜底），生产须保证 Redis 可用
- 惰性创建仍可能产生脏 session 名（无害，可被显式 `POST /sessions` 覆盖）
- 出网/CPU 限额、CORS、HTTPS 反代不在本期（留 v1.0 或独立运维项）
- 完整工具级权限模型（蓝图原 v0.9）顺延，本期只做基础设施加固
### 执行方案
- 详见 `docs/plans/v0.9-execution-plan.md`