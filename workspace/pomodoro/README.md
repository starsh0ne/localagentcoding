# 🍅 番茄钟 CLI（单文件 / uv 管理 / Python 3.12）

一个**单文件**番茄钟命令行应用。纯标准库、零第三方依赖（测试除外），
计时基于**持久化的时间戳推算**，因此：

- `start / pause / resume / reset / stats` 可以在任意终端执行；
- 程序被强杀、断电或正常退出后重启，未完成的番茄钟与剩余时间**精确恢复**，不会丢失；
- 今日统计由历史记录（每条含开始/结束时间）自动聚合。

## 文件位置

项目位于工作区 `pomodoro/` 目录：

```
pomodoro/
├── pomodoro.py        # 唯一代码文件（CLI 应用本体）
├── pyproject.toml     # uv 项目配置（Python >=3.12，无运行时依赖）
├── .python-version    # uv 锁定 3.12
├── README.md          # 本文件
└── tests/
    └── test_pomodoro.py  # 纯逻辑单元测试（dev 依赖 pytest）
```

## 运行方式（uv）

```bash
cd pomodoro

uv run python pomodoro.py                 # 交互式 REPL（键盘输入命令）
uv run python pomodoro.py --help          # 查看全部子命令与示例
```

常用示例命令：

```bash
# 开始：默认 25 分钟；也可指定时长与任务标签
uv run python pomodoro.py start
uv run python pomodoro.py start 50
uv run python pomodoro.py start 50 写周报

# 生命周期
uv run python pomodoro.py pause           # 暂停（记录已专注秒数）
uv run python pomodoro.py resume          # 继续
uv run python pomodoro.py reset           # 放弃当前番茄钟（已专注部分记入历史）
uv run python pomodoro.py status          # 状态 + 剩余时间 + 进度条

# 今日统计 / 全部统计
uv run python pomodoro.py stats           # 今天完成数量、累计专注时长、明细
uv run python pomodoro.py stats --all

# 实时倒计时视图（结束时响铃提示；Ctrl+C 仅退出视图，计时不受影响）
uv run python pomodoro.py watch
```

### 交互式 REPL

不带子命令进入。输入命令后回车即可，支持单字母简写：

```
🍅 idle> start 50 写周报
🍅 run 49:51> pause
🍅 pause 49:51> resume
🍅 run 49:47> stats
🍅 idle> quit
```

完整命令：`start [分钟] [标签...]`、`pause(p)`、`resume(r)`、`reset(x)`、
`status(s)`、`watch(w)`、`stats(t)[all]`、`help(h)`、`quit(q)/exit`。

> 注：运行中的番茄钟会显示在提示符里（如 `run 24:58>`），输入任意命令
> （或直接回车刷新）时都会按真实时间结算，因此即使“忘了它”，到期也会补记完成。

## JSON 数据文件

- 默认：`~/.pomodoro.json`
- 覆盖方式：`--file <路径>` 或环境变量 `POMODORO_FILE=<路径>`

```bash
uv run python pomodoro.py --file ./data.json start 25
POMODORO_FILE=./data.json uv run python pomodoro.py stats
```

写入采用“先写临时文件再原子替换”，不会留下写一半的损坏文件；文件损坏时自动
改名备份为 `*.corrupt-<时间戳>` 并从空数据恢复。

### 数据结构

```jsonc
{
  "version": 1,
  "state": {                                   // 当前计时状态（跨重启恢复）
    "status": "running",                        // "idle" | "running" | "paused"
    "duration_minutes": 25,                     // 本番茄钟计划分钟数
    "label": "写周报",                           // 可选任务标签
    "started_at": "2025-01-01T09:00:00+08:00",  // 本番茄钟最初开始时刻
    "running_from": "2025-01-01T09:10:00+08:00",// 最近一次 resume 的时刻
    "elapsed_seconds": 600                      // running_from 之前已专注秒数
  },
  "history": [                                  // 历史记录（今日统计数据源）
    {
      "completed": true,                        // true=完成, false=中途放弃
      "planned_minutes": 25,
      "focus_seconds": 1500,                    // 实际专注秒数
      "started_at": "2025-01-01T09:00:00+08:00",
      "ended_at":   "2025-01-01T09:25:00+08:00",
      "label": "写周报"
    }
  ]
}
```

剩余时间的推算规则（保证重启恢复）：

```
status == running:
    剩余秒数 = duration_minutes*60 − elapsed_seconds − (now − running_from)
status == paused:
    剩余秒数 = duration_minutes*60 − elapsed_seconds
```

## 测试

测试仅覆盖纯逻辑（状态机/剩余推算/统计聚合），dev 组依赖 pytest：

```bash
uv sync --group dev
uv run --group dev pytest
```

## 技术栈

- Python ≥ 3.12（`.python-version` 固定 3.12），uv 管理
- 标准库 only：`argparse / json / datetime / pathlib / subprocess` 等
- 无第三方运行时依赖，因此**无需 `uv add`** 任何包
