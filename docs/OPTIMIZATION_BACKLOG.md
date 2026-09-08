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

---

## 版本进度速查
- 已提交：`v0.1`（minimal agent loop）、`v0.2`（工程骨架 + 多 Agent 调度）、`v0.3`（coder workspace + sandbox + 长任务处理）。
- 演进中（未单独提交）：`v0.4`（记忆 + 任务系统）、`v0.5`（RAG）、`v0.6`（eval）、`v0.7`（API）、`v0.8`（可观测性）占位与部分实现已就位。
- 详见 `docs/CHANGELOG.md`。
