# ============================================================
# task_tools.py | v0.4 诞生（任务工具: 通用侧4个 + coder侧工厂2个）
# 共享状态落地: 计划书/进度经TaskStore共享; 代码在 tasks/<task_id>/
# ============================================================
from langchain.tools import tool
from agent_pro.tasks.store import get_task_store
from agent_pro.api.bus import get_event_bus

def _tree(root, prefix: str = "") -> list[str]:
    """递归文件树"""
    lines = []
    entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())) if root.exists() else []
    for i, e in enumerate(entries):
        last = i == len(entries) - 1
        lines.append(prefix + ("└── " if last else "├── ") + e.name + ("/" if e.is_dir() else ""))
        if e.is_dir():
            lines += _tree(e, prefix + ("    " if last else "│   "))
    return lines


# ---------- 通用agent侧 ----------
@tool
def create_task_plan(title: str, plan: str) -> str:
    """创建开发任务与计划书，返回task_id。plan必须是完整计划书，包含:
    目标 / 文件结构 / 实现步骤 / 验收标准。这是调度coder前的必经步骤；
    之后调用 dispatch_to_coder 时必须携带返回的task_id。"""
    task_id = get_task_store().create_task(title, plan)
    return (f"任务已创建: task_id={task_id}\n交付目录: workspace/tasks/{task_id}/\n"
            f"请携带此task_id调度 dispatch_to_coder 执行。")


@tool
def update_task_plan(task_id: str, plan: str) -> str:
    """修订已有任务的计划书（任务延续、需求变更时使用，如"加个功能"）。
    修订后需携带同一task_id重新调度coder。"""
    store = get_task_store()
    if not store.update_plan(task_id, plan):
        return f"错误: 任务不存在: {task_id}"
    return f"计划书已更新: {task_id}（coder下次调度将读到新计划）"


@tool
def read_task_progress(task_id: str) -> str:
    """查看任务状态、计划书与进度记录。验收或任务延续前先用本工具了解现状。"""
    store = get_task_store()
    row = store.get(task_id)
    if row is None:
        return f"错误: 任务不存在: {task_id}"
    progress = store.progress_list(task_id)
    p_lines = "\n".join(f"- {p['note']}" for p in reversed(progress)) or "(暂无进度)"
    return (f"任务[{task_id}] {row['title']}\n状态: {row['status']}\n"
            f"交付目录: workspace/tasks/{task_id}/\n\n【计划书】\n{row['plan']}\n\n【进度】\n{p_lines}")


@tool
def review_task_files(task_id: str) -> str:
    """验收第一步: 列出任务交付目录的完整文件树与最近进度。
    抽查文件内容请用 read_file(路径前缀 tasks/<task_id>/)。"""
    store = get_task_store()
    row = store.get(task_id)
    if row is None:
        return f"错误: 任务不存在: {task_id}"
    art_dir = store.tasks_dir / task_id
    tree = "\n".join(_tree(art_dir)) or "(交付目录为空——coder尚未产出文件)"
    progress = store.progress_list(task_id, 3)
    p_lines = "\n".join(f"- {p['note']}" for p in reversed(progress)) or "(暂无)"
    return f"任务[{task_id}] 状态: {row['status']}\n交付目录: tasks/{task_id}/\n{tree}\n\n最近进度:\n{p_lines}"


# ---------- coder侧工厂(仅带task_id调度时提供) ----------
def make_task_tools(task_id: str | None) -> list:
    if not task_id:
        return []
    store = get_task_store()

    @tool
    def read_task_plan() -> str:
        """读取当前任务的计划书（目标/文件结构/实现步骤/验收标准），严格照此实现。"""
        row = store.get(task_id)
        if row is None:
            return f"错误: 任务不存在: {task_id}"
        return f"任务[{task_id}] {row['title']}\n【计划书】\n{row['plan']}"

    @tool
    def update_progress(note: str, finished: bool = False) -> str:
        """向共享状态记录进度。note说明: 完成了什么/发现的问题/下一步计划；
        需求不明时在note中写明你的假设。全部交付且自检通过时 finished=True。"""
        pid = store.append_progress(task_id, note, finished)
        # v0.5: 推送到任务事件流(SSE实时可见)
        bus = get_event_bus()
        topic = f"task:{task_id}"
        bus.publish(topic, "progress",
                    {"pid": pid, "note": note, "finished": finished})
        if finished:
            bus.publish(topic, "task_done", {"task_id": task_id})
        return f"进度已记录: {note[:80]}"

    return [read_task_plan, update_progress]