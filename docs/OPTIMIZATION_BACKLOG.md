# Agent-Pro 优化清单（v0.1 → 当前，待 1.0 统一改进）

> **状态说明**：本文件汇总自 v0.1 立项至今开发 / 使用中暴露的所有问题、脆弱点与改进项。
> 系统当前可正常运行。按约定**暂不改动代码**，所有项统一在 **1.0 版本**重构加固时处理。
> 每项标注：现象 → 根因 → 当前处置 → 1.0 建议方向。

---

## 图例

| 标记 | 含义 |
|------|------|
| 🐛 | 已踩坑（已临时救火，建议 1.0 根治） |
| 🛡️ | 健壮性缺口（当前能跑，遇边界/异常会崩或误导） |
| 🧩 | 功能缺口 / 潜在增强 |
| 🧹 | 工程规范 / 文档 |

---

## 一、🐛 已踩坑（临时修复，建议 1.0 根治）

### 1. uv workspace 同名冲突
- **现象**：`uv add` 报 `Two workspace members are both named agent-pro`。
- **根因**：误在 `agent-pro/agent-pro/` 下生成 uv 默认空壳项目（自带 `.git` + 空 `src/agent_pro/__init__.py`），与根项目同名；`uv add` 又自动给根 `pyproject.toml` 追加 `[tool.uv.workspace] members=["agent-pro"]`，触发冲突。
- **当前处置**：删除嵌套空壳、移除外层 `[tool.uv.workspace]` 段，`uv add` 已正常。
- **1.0 建议**：`pyproject.toml` 显式锁定 `[tool.uv]` 不自动推断 workspace 成员；在贡献指南写明「禁止在项目子目录内 `uv init/add`」。

### 2. 包改名导致 `__init__.py` / `base.py` 文件损坏
- **现象**：`SyntaxError`（`return ...` 后误拼接旧 docstring）、`IndentationError`（`base.py` 整文件缩进丢失，类与方法顶格）。
- **根因**：`agent/` → `agents/` 改名时 `__init__.py` 残留旧包尾巴；文件缩进整段丢失。
- **当前处置**：拆分拼接、按正确缩进重写 `base.py`。
- **1.0 建议**：改名 / 合并包后用 `ruff` 强制 lint + 加 import 冒烟测试；包文件尽量由脚手架生成，避免手拼留下尾巴。

### 3. `@tool` 的 f-string docstring（踩过两次）
- **现象**：`ValueError: Function must have a docstring if description not provided.`
- **根因**：用 `f"""..."""` 当 docstring——`f"""` 是「被丢弃的表达式语句」不是 docstring，`@tool` 拿不到描述。
- **当前处置**：改为 `@tool(name, description=(f"..."))` 显式传 `description`，动态插值才生效。
- **1.0 建议**：封装统一工具工厂（如 `make_tool(name, desc_template, fn)`），禁止裸写 `@tool` + f-string 描述；或在 lint 规则里检测「函数体首条语句是 f-string 三引号」。

### 4. dispatch 路由：提示词 vs 工具描述打架
- **现象**：用户明确说「复杂任务，写个单文件 todo.py」，通用 Agent 仍直接吐文本，日志无 `⚙ dispatch_to_coder` 调用。
- **根因**：模型选工具看的是**工具描述**而非 system prompt。`dispatch_to_coder` 的工具描述由 `coder.py` 的 `AgentSpec.description` 生成，那段写「复杂/多文件才用、简单不要调度」，与 `general.py` 路由提示词（「产出代码文件就必须 dispatch，即使单文件」）冲突 → 模型按工具描述把单文件判成「简单」自行回答。
- **当前处置**：统一 `CODER_SPEC.description` 与 handoff 工具描述前缀为「产出代码文件就派发」。
- **1.0 建议**：路由判定交付结构化元数据（`spec.tags` / 匹配规则）而非纯自然语言，减少提示词漂移；或用手写 router 函数替代「靠 LLM 自选工具」。

### 5. 记忆压缩触发阈值（short + batch = 10）
- **现象**：7 轮及以下不压缩，摘要 / 提取记忆为空。
- **根因**：`maybe_compact` 仅当 `max_active_round >= short + batch` 才压缩；默认 `memory_short_term_rounds=5` + `memory_compact_batch=5` → 首个压缩点在第 10 轮。
- **当前处置**：用户确认保持现状（`batch` 是攒批迟滞，减少频繁调 LLM 压缩）。
- **1.0 建议**：澄清变量语义与可观测性；评估是否改为「超过 short 即压回 short」更直观。当前保留策略。

### 6. `max_active_round` 语义误读（返回最大轮号，非计数）
- **现象**：压缩后用户误读 `剩余active轮数 == 14` 应为 5。
- **根因**：`MemoryDB.max_active_round` 返回 `MAX(round_no)`（最大轮号）；滑动窗口压缩后轮号单调递增，保留最近 short 轮（如 10–14），故返回 14。压缩逻辑依赖「最大轮号」语义算 cutoff，**不能改成计数**。
- **当前处置**：已解释清楚；建议新增 `count_active_rounds`（COUNT DISTINCT round_no WHERE active=1）。
- **1.0 建议**：补 `count_active_rounds`；为 `max_active_round` 加 docstring 强调「返回最大轮号」或在 1.0 重命名避免歧义。

### 7. `create_app_agent` 接线改一半（extra_tools TypeError）
- **现象**：`uv run main.py --new` 报 `TypeError: build_general_agent() got an unexpected keyword argument 'extra_tools'`。
- **根因**：`agents/__init__.py` 改为注入全局工作区只读验收工具 `readonly_tools`，调用 `build_general_agent(registry, extra_tools=readonly_tools)`；但 `general.py` 的签名仍是旧 `(registry)`。
- **当前处置**：`general.build_general_agent` 增加 `extra_tools: list | None = None` 并并入工具集。
- **1.0 建议**：固定 `build_general_agent` 的装配契约，或让 `create_app_agent` 直接构造通用 Agent，避免「两处签名必须手同步」。

### 19. v0.5 SSE `/events` 回放 `final` 不关闭流
- **现象**：任务跑完「之后」才连 `GET /api/chat/turns/{id}/events`，流先回放 `final` 事件，随后不关闭、持续发 `: ping` 心跳，客户端永久挂起。
- **根因**：`server.py:83-84` 回放循环只 `yield` 事件、未对 `final`/`error` 做 `break`；`server.py:92` 的终止判断仅在「实时事件」分支。晚连接（任务已完成）时 `final` 只在 replay 里被 yield 一次，随后的 `while True` 永远等新事件。
- **当前处置**：进行中连接（任务未结束就连）会在 `final` 到达时正常关闭；仅「晚连接」场景挂死。
- **1.0 建议**：回放循环内对 `final`/`error` 也要 break（或抽出统一的「收到终止事件即 break」判断，覆盖 replay 与实时两条路径）。

### 20. v0.5 中文消息输入乱码（mojibake）
- **现象**：`curl.exe -N` 订阅 events 拿到 `event: final`，但 agent 回复称「你的消息显示成乱码（中文变成 `?`），没读到需求」。SSE 输出侧中文正常（curl 渲染正确），说明乱码在输入侧。
- **根因**：用户中文消息经 PowerShell `Invoke-RestMethod -Body '{"message":"中文..."}'` 发出后，到 agent 时已变 `?`。`?` 是「以无法表示中文的编码（cp1252/ascii 之类，`errors='replace'`）编码」的典型产物；SSE 输出用 `json.dumps(..., ensure_ascii=False)` 正确保留中文，已排除输出侧。
- **当前处置**：**根因已确认 = 客户端 PowerShell 内联 `-d` 编码**（非服务端）。证据：`turn-20260908-214210-40bcda` 用 `-d @body.json`（UTF-8 文件）创建后，订阅其 `/events` 拿到 `event: tool_call` 的 `dispatch_to_coder`，`args_brief` 中中文 **番茄钟** 完整无缺 → 服务端存储/解码正确、agent 读中文正确。先前用 PS 内联 `-d "{\"message\":\"...中文...\"}"` 才会把中文烧成 `?`（`?` 是 cp1252/ascii `errors='replace'` 产物）。SSE 输出侧 `json.dumps(..., ensure_ascii=False)` 也一直正确。结论：**服务端无中文乱码问题**。
- **1.0 建议**：① 仅客户端侧修正——发 JSON 一律走 UTF-8 文件（`curl -d @file.json`）或 PS UTF-8 字节数组，禁内联中文 `-d`；② 服务端可加 `charset=utf-8` 显式声明（防御性，非必需）；③ 加一条中文往返冒烟测试固化该结论。

---

## 二、🛡️ 健壮性缺口（当前能跑，遇边界会崩或误导）

### 8. `main.py` 无 API 异常兜底 → 402 / 限流直接崩
- **现象**：`openai.APIStatusError: 402 - Insufficient token quota` 一路冒泡，整屏 traceback 崩掉 `main.py`。
- **根因**：`agent.stream(...)` 外包无 `try/except`，未捕获 API 配额 / 限流 / 网络 / 模型报错。
- **1.0 建议**：在 `main.py` 的 stream 外层捕获 `APIStatusError` / `RateLimitError` / `Timeout`，分类给出友好文案并优雅退出（属 v0.7 可观测性范畴）。

### 9. 生成代码的路径落点不可预测
- **现象**：`workspace/pomodoro.py` 用相对路径 `DATA_FILE="pomodoro_data.json"`，实际 JSON 写到「启动脚本时的当前目录」（项目根），不在 `workspace/`，用户找不到。
- **根因**：生成代码使用相对路径，落点取决于运行时的 CWD。
- **1.0 建议**：① coder 写文件工具强制要求基于 workspace 的绝对 / 相对路径；② 提示词约束「所有文件路径以 workspace 为基」；③ 加 review/linter 步骤检查生成代码的相对路径落点。

### 10. coder 无执行工具 → 编造「验证示例」
- **现象**：coder 回复里的 `add / list / done 999` 输出是模型编的 markdown 演示，没真跑（CODER_TOOLS 只含 `write_file` / `pip_install` 等，无运行命令）。
- **根因**：coder 提示词要求「列验证方法」，但无执行工具，只能靠想象编输出，复杂任务会写出「看似跑过其实没跑」的假日志，有误导性。
- **1.0 建议**：加沙箱化执行工具（`tools/run_cmd.py`，命令白名单 + 超时 + 禁 `rm`/`sudo` 等危险指令），让 coder 真跑真验；提示词加「禁止附未经验证的运行示例」。

### 11. 终端环境混淆（PowerShell vs Git Bash）
- **现象**：`git init && ...`、`mkdir -p ...` 在 PowerShell 5.1 报语法错（`&&` / `-p` 不被支持）。
- **根因**：用户终端默认 PowerShell，而项目命令是 bash 语法。
- **1.0 建议**：README / 贡献指南明确「项目命令统一用 Git Bash」；Makefile 已覆盖 `install/run/test/lint`，引导用户走 `make`。

### 21. v0.5 鉴权缺头返回 422 而非 401
- **现象**：`GET /api/tasks/x` 不带 `X-API-Key` → `422 {"detail":[{"type":"missing","loc":["header","X-API-Key"]...}]}`，用户预期 401。
- **根因**：`server.py:23` `require_api_key` 用 `Header(alias="X-API-Key")`，缺头时 FastAPI 当参数校验失败自动返 422；仅「头存在但值错」才 `raise HTTPException(401)`（`server.py:24-25`）。
- **当前处置**：鉴权生效（请求被拒），仅状态码语义不符直觉。
- **1.0 建议**：用自定义 `APIKeyHeader` 或在依赖里显式判缺头返 401，使「缺头 / 错头」均 401，符合 REST 惯例。

### 24. 交付物无验证关卡（coder 自认「未经实跑」）
- **现象**：`turn-20260908-214210-40bcda` 端到端跑通——中文输入 → 通用 Agent 正确 dispatch → coder 在 `workspace/pomodoro/` 生成 `pomodoro.py` + `tests/test_pomodoro.py` + `pyproject.toml` + `README.md`。但 coder 在 `final` 明确声明「环境无代码执行工具，代码**未经实跑验证**（未附运行输出）」，仅配套了单元测试。
- **根因**：dispatch 管线只有「生成」没有「验收」步骤；coder 工具集（`write_file` 等）无运行能力（同 #10）。本例 coder 已**诚实声明未验证**（比早期编造假日志好），但框架仍会把未验证代码当「完成」交付，质量无把关。
- **当前处置**：链路功能验证通过（调度/中文/文件产出均正常）；交付质量靠用户手动跑 `uv run ... start/status/stats` 复核。
- **1.0 建议**：在 dispatch 收尾加「沙箱自验」步骤——coder 写完先 `run_cmd`（白名单+超时，见 #10）实跑冒烟用例，通过才发 `final`；或加独立 verifier Agent 对交付物做 lint+基础跑测。把「未验证」从「用户事后发现」提前成「交付前拦截」。

---

## 三、🧩 功能缺口 / 潜在增强

### 12. 通用 Agent 只读验收 ≠ 执行
- `readonly_tools` 仅验收不执行；如需通用 Agent 也能跑命令，1.0 考虑复用 #10 的沙箱执行工具（按需放行）。

### 13. 记忆压缩每次都调 LLM
- 每次 `maybe_compact` 都烧 token 调模型。1.0 可加压缩结果缓存 / 增量压缩，降低开销。

### 14. 记忆测试脚本游离在根目录
- 用户自写的 `test.py`（验证 SessionManager）应在 `tests/` 下，用 pytest 管理（对应 v0.6 eval 框架）。

### 15. `tests/debug_dispatch.py` 发起真实 API 调用
- 该脚本用真实 API 做端到端断言，CI 不宜跑。1.0 应标 `@pytest.mark.skip` 或移出常规测试集。

### 16. 路由缺少「置信度 / fallback」
- 当模型既不 dispatch 也不直接答（犹豫）时，无兜底策略。1.0 可加「无法判定则回退通用回答并提示」。

---

## 四、🧹 工程规范

### 17. 默认分支为 `master`
- git 默认分支 `master`（非 `main`）。1.0 考虑 `git branch -m main` 并补 `.gitattributes`（`* text=auto`）统一行尾。

### 18. 核心约定（供 1.0 写进 `CONTRIBUTING.md` / `AGENTS.md`）
1. **运行必用 `uv run`**：直接 `python 脚本.py` 找不到顶层包且缺 venv。
2. **`@tool` 动态描述走 `description=` 参数**，绝不用 f-string docstring。
3. **路由描述与 system prompt 语义必须一致**（spec.description 会进 handoff 工具描述）。
4. **改 `create_app_agent` 必同步 `general.build_general_agent` 签名与工具合并**。
5. **改名 / 合并包先 lint**，防止 `__init__.py` 残留拼接与缩进整段丢失。
6. **项目命令统一 Git Bash**，避免 PowerShell 的 `&&` / `mkdir -p` 语法坑。

### 22. 测试用 PowerShell `Invoke-RestMethod` 需 `-UseBasicParsing`
- **现象**：`Invoke-RestMethod GET /api/chat/turns/{id}` 抛 `NullReferenceException`（FullyQualifiedErrorId: `System.NullReferenceException,Microsoft.PowerShell.Commands.InvokeRestMethodCommand`），无 HTTP 状态码，看似「服务器没返回」。
- **根因**：PowerShell 5.1 的 `Invoke-RestMethod` 默认走 IE 解析引擎（MSHTML），现代 Windows 上直接 NRE；与服务器无关（同接口 curl 正常）。
- **当前处置**：改用 `curl.exe` 或加 `-UseBasicParsing`。
- **1.0 建议**：API 冒烟测试统一用 `curl.exe`（`-N` 看 SSE）；若用 PS，固定 `-UseBasicParsing`。README 测试段注明。

### 23. 测试：PowerShell 内 curl `-d` 内联 JSON 被原生参数传递弄坏
- **现象**：`curl.exe -d '{"message":"中文"}'`（PS 单引号内联）仍报 `422 json_invalid`；改用 `-d "@body.json"`（文件）则正常返回 turn_id。
- **根因**：PowerShell 向原生 exe（curl.exe）传递含 `"` 的参数时会自行加引号 / 转义，内联 JSON 的引号被弄坏；文件方式绕开命令行参数传递。
- **当前处置**：JSON body 一律写文件 + `-d @file.json`（如 `'...' | Out-File -Encoding utf8 body.json`）。
- **1.0 建议**：API 冒烟测试脚本统一用 `@file.json` 传 body，避免 PS 内联引号陷阱（与 #22 同属「测试客户端坑」）。

---

## 版本进度速查
- 已提交：`v0.1`（minimal agent loop）、`v0.2`（工程骨架 + 多 Agent 调度）、`v0.3`（coder workspace + sandbox + 长任务处理）。
- 演进中（未单独提交）：`v0.4`（记忆 + 任务系统）、`v0.5`（RAG）、`v0.6`（eval）、`v0.7`（API）、`v0.8`（可观测性）占位与部分实现已就位。
- 详见 `docs/CHANGELOG.md`。

---

## 五、本轮已收口（v0.6 收尾，已做）

### 25. 生成产物/数据误提交风险（已补 .gitignore）
- **现象**：`data/kb.db`、`knowledge/` 未跟踪且未被 gitignore，commit 易误塞二进制库；`data/agent_pro.db`（记忆/任务 store，运行时生成）虽被跟踪，但同属可重建产物。
- **处置（已做）**：`.gitignore` 增加 `data/*.db` 与 `knowledge/`；并对已跟踪的 `data/agent_pro.db` 执行 `git rm --cached`（保留本地文件，仅停止入库）。此后 `git status` 不再列出这些生成物。
- **注意**：两个库均为可重建（kb 可 `uv run python -m agent_pro.kb reindex` 重生；agent_pro.db 启动时自动建表）。

### 26. `rag/` 死占位与 `kb/` 功能重复（已删）
- **现象**：`src/agent_pro/rag/`（ingest.py/retrieve.py）仅 TODO 空实现，与 v0.6 新建的 `kb/` 功能重叠，无引用、纯死代码。
- **处置（已做）**：用户已手动删除 `rag/` 整目录，删除已 staged（`D src/agent_pro/rag/*`）。`kb/` 为唯一知识库实现。
- **1.0 建议**：在 `CONTRIBUTING.md` 明确「占位模块要么实现要么删，禁止留 TODO 空壳」；删除前已确认无任何 import 引用 `rag` 包。

---

## 六、本轮修复（v0.6 embedding 打通）

### 27. `tiktoken` 阻塞非 OpenAI embedding 模型（已修，验证通过）
- **现象**：vLLM embedding 服务本身通（PowerShell `Invoke-RestMethod` 调 `localhost:8001/v1/embeddings` 正常返回 1024 维向量）；但项目 `uv run python -m agent_pro.kb reindex` 后 `embedded_chunks: 0`、`retrieval_mode: fts-keyword`，向量始终填不进。
- **根因**：`embeddings.py` 用 `langchain_openai.OpenAIEmbeddings`，其 `embed_query` 默认 `check_embedding_ctx_length=True` → 对非 OpenAI 模型名（`Qwen3-Embedding-0.6B`）`tiktoken.encoding_for_model` 抛 KeyError → 回退下载 `cl100k_base` BPE 词表 → 请求 `openaipublic.blob...cl100k_base.tiktoken` 返回 **400**（受限/离线网络），异常被 `ingest.py` 的 `except` 静默吞掉 → `emb=None` 降级 FTS。
- **处置（已做）**：`OpenAIEmbeddings(..., check_embedding_ctx_length=False)` 跳过 tiktoken 分词。`reindex` 复测：8/8 块向量覆盖、`dim=1024`、`retrieval_mode: hybrid`、search 正常返回融合结果。属**硬阻塞 bug**（非优化），故本轮直接修并验证，未延后 1.0。
- **1.0 建议**：① 该 fix 保留；② 顺带把 `ingest.py` 的「单块 embedding 失败静默降级」加日志/计数告警，避免再出现「看起来导入成功实则全 FTS」的假象（与 #24 验证缺失同源）。

### 28. `knowledge/project-notes.txt` 检索内容乱码（疑似 .txt 编码误判，待查）
- **现象**：`search` 命中 [1] `project-notes.txt` 的 content 显示为 `A g e n t P r o` 字符间插空/乱码（类似 UTF-16LE 无 BOM 被当 ASCII/UTF-8 读）。
- **根因（推测，未确认）**：`kb/parsers.py` 对 `.txt` 未显式探测编码（如 chardet / BOM），该文件实际可能是 UTF-16 或其它编码，被默认解码成脏字节。
- **当前处置**：不影响 embedding 主链路（向量维度正确、检索召回正常），仅该单文件内容展示异常。
- **1.0 建议**：`parsers.py` 读文本前做编码探测（BOM 优先 → chardet 兜底），或重存该文件为 UTF-8。需先 `file`/十六进制确认真实编码再定方案。
