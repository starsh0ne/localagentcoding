# agent-pro 项目简历素材

> 多智能体调度应用，分阶段自研（v0.1 → v0.8 渐进交付）。以下可直接改写为简历条目或面试陈述。

---

## 一、项目描述

**agent-pro —— 自研多智能体调度应用（个人 / 主导项目，v0.1 → v0.8 渐进交付）**

一个基于 LangChain 的分阶段自研 Agent 系统，从最小智能体循环起步，逐步演进为具备多 Agent 调度、私有知识库（RAG）、会话记忆、API 服务化、可观测性与代码执行隔离沙箱的生产就绪型应用。采用 **Agent-as-Tool** 调度范式（弃前置分类路由），两级扁平结构防止自我调度死循环。

- **技术栈**：Python 3.12 / uv、LangChain ≥0.2（LangGraph 调度）、FastAPI + uvicorn、Pydantic v2、SQLite（FTS5 全文检索）、本地 Qwen3-Embedding-0.6B（vLLM 部署）+ DeepSeek LLM。
- **架构亮点**：提交 / 消费分离的事件总线（SSE 双流）、按 turn 作用域的成本核算、WSL2 隔离子环境 + HITL 审批闸、结构化 JSON 日志（contextvars 注入全链路上下文）。

---

## 二、主要职责

- **多智能体调度框架设计**：设计 AgentSpec / AgentRegistry 两级调度接口，实现 general（调度中枢）+ coder（代码执行）分工，引入 `recursion_limit` 防无限递归，子 Agent 不持有调度工具以切断环。
- **工具与权限体系**：实现路径沙箱（越界 / 穿越 / 符号链接防护）、workspace 文件四件套、AST 白名单计算器求值（替换危险 `eval`）、计算器 / 知识库 / 记忆 / 任务工具注册。
- **RAG 私有知识库**：结构感知分块（标题路径溯源 + 超长硬切重叠）、向量 + FTS5 的 **RRF 混合检索**，未配置 embedding 时自动降级；检索结果带 source + heading 引用返回。
- **会话记忆系统**：短期记忆（5 轮滚动 + 摘要压缩）+ 长期记忆（preference / fact / entity 分库 + 价值提取入库），SQLite 线程安全修复。
- **API 服务化**：FastAPI 实现 chat 提交 / turn 与 task 双 SSE 流 / 历史回放兜底；`X-API-Key` 鉴权 fail-closed（空 key 拒绝启动）；单 worker 串行队列消化长任务。
- **可观测性**：stdlib-only 结构化 JSON 日志（RotatingFileHandler + UTF-8 + contextvars 注入 session / turn / agent / model）、`TokenUsageCallbackHandler` 按模型拆分 Token 与费用。
- **代码执行隔离沙箱**：WSL2 子环境执行（Windows 路径 → `/mnt/c` 映射、超时硬杀），HITL 审批闸（`request_verification` 工具阻塞 worker 线程 + `POST /api/turns/{id}/approve` + 注册表原子防双批）。

---

## 三、项目成果

- 完成 **8 个可运行版本**，每版均提交并通过端到端验证，形成「决策先行（先拍板范围与权衡，再实现）」的研发闭环。
- 落地 **生产 Tier-1 能力**：可观测日志、成本账本、鉴权、隔离执行、HITL 审批。
- 沉淀 **30 条优化 backlog（#1–#30）**，含 2 个真实缺陷修复（工具描述铁律、coder context 遮蔽回归）。
- 关键工程决策均有文档留痕（`docs/CHANGELOG.md` + 每版 `execution-plan.md`），包括「Docker vs WSL2」「出网策略」「是否做追踪」等权衡记录。

---

## 四、面试可能被考察的重难点 / 易错点

### 设计层

1. **为什么用 Agent-as-Tool 而非前置分类路由？**
   答：可扩展、可连续调度；代价是需 `recursion_limit` + 两级扁平防死循环。
2. **长任务为何不挂 HTTP 请求？**
   答：提交 / 消费分离，POST 返回 turn_id，SSE 异步消费，避免网关超时。
3. **隔离沙箱为什么选 WSL2 而非 Docker？**
   答：零额外安装、冷启动快；代价是「中等隔离」——不防针对性恶意代码读 `/mnt/c`，需明确告知边界。

### 实现层（高频易错）

4. **线程 / 异步混用**：审批闸的 `threading.Event.wait` 必须放在 `asyncio.to_thread` 跑的工作线程，否则阻塞事件循环导致端点无法响应——这是经典坑。
5. **contextvars 透传**：成本 handler、`turn_id` 要经 contextvar 跨线程 / 子 Agent 共享，否则 coder 子 Agent 累加不到同一笔账。
6. **SQLite 线程安全**：单写者 + 多读，事件总线 / Turn 注册表需加锁；内存态注册表重启丢失，要靠 history 兜底。
7. **变量遮蔽回归（#30）**：import 的 `context()` 被局部变量 `context=None` 遮蔽，导致 `with context(...)` 抛 `'NoneType' is not callable`、所有代码任务崩溃。教训：通用名别作局部变量、每版冒烟必含真实代码任务。
8. **LangChain 工具描述铁律**：动态 `f-string` docstring 不是 docstring，工具描述必须走 `@tool(description=...)`，否则调度判据失效。
9. **RAG 降级与溯源**：embedding 缺失要自动降级到纯 FTS5；分块必须保留 heading 路径，否则引用不可信。
10. **编码一致性**：日志以 UTF-8 写入，但 Windows 客户端（PowerShell GBK）查看会「乱码」——需区分「服务端写错」与「客户端显示错」。

---

## 五、精简版（30 秒自我介绍用）

主导自研 **agent-pro**：基于 LangChain 的多智能体调度应用，从最小循环演进到生产就绪。负责多 Agent 调度框架、RAG 私有知识库（RRF 混合检索）、会话记忆、FastAPI 服务化（SSE 双流 + 鉴权）、结构化可观测性与 WSL2 代码执行隔离沙箱（HITL 审批闸）。8 个版本全部交付验证，沉淀 30 条优化 backlog 与完整决策文档。
