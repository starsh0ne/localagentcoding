#!/usr/bin/env python3
"""
简单的命令行待办事项应用
支持添加、列出、完成待办事项
数据以JSON格式持久化存储
"""

import json
import sys
from pathlib import Path

# 数据文件路径
DATA_FILE = Path("todos.json")


def load_todos():
    """从JSON文件加载待办列表，文件不存在则返回空列表"""
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        # 文件损坏时返回空列表
        return []


def save_todos(todos):
    """将待办列表保存到JSON文件"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)


def get_next_id(todos):
    """获取下一个可用的ID（基于最大ID自增）"""
    if not todos:
        return 1
    return max(todo["id"] for todo in todos) + 1


def add_todo(content):
    """添加新的待办事项"""
    todos = load_todos()
    new_todo = {
        "id": get_next_id(todos),
        "content": content,
        "done": False
    }
    todos.append(new_todo)
    save_todos(todos)
    print(f"✅ 已添加待办 #{new_todo['id']}: {content}")


def list_todos():
    """列出所有待办事项"""
    todos = load_todos()
    if not todos:
        print("📝 暂无待办事项")
        return

    print("\n📋 待办列表:")
    print("-" * 50)
    for todo in todos:
        status = "✅" if todo["done"] else "⬜"
        print(f"  {status} #{todo['id']:>3}: {todo['content']}")
    print("-" * 50)
    total = len(todos)
    done_count = sum(1 for t in todos if t["done"])
    print(f"  共 {total} 项 | 已完成 {done_count} 项 | 未完成 {total - done_count} 项\n")


def mark_done(todo_id):
    """标记指定ID的待办为已完成"""
    todos = load_todos()

    for todo in todos:
        if todo["id"] == todo_id:
            if todo["done"]:
                print(f"⚠️  待办 #{todo_id} 已经是完成状态")
                return
            todo["done"] = True
            save_todos(todos)
            print(f"✅ 已完成待办 #{todo_id}: {todo['content']}")
            return

    print(f"❌ 未找到ID为 {todo_id} 的待办事项")


def show_help():
    """显示帮助信息"""
    print("""
📝 命令行待办应用

用法:
  python todo.py add <任务内容>  添加新待办
  python todo.py list            列出所有待办
  python todo.py done <id>       标记待办为完成
  python todo.py help            显示帮助信息

示例:
  python todo.py add "写周报"
  python todo.py add "购买 groceries"
  python todo.py list
  python todo.py done 1
""")


def main():
    """主函数，解析命令行参数并执行对应操作"""
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()

    if command == "add":
        if len(sys.argv) < 3:
            print("❌ 请提供任务内容")
            print("用法: python todo.py add <任务内容>")
            return
        # 合并所有后续参数作为任务内容（支持带空格的任务描述）
        content = " ".join(sys.argv[2:])
        add_todo(content)

    elif command == "list":
        list_todos()

    elif command == "done":
        if len(sys.argv) < 3:
            print("❌ 请提供待办ID")
            print("用法: python todo.py done <id>")
            return
        try:
            todo_id = int(sys.argv[2])
            if todo_id <= 0:
                print("❌ ID必须是正整数")
                return
            mark_done(todo_id)
        except ValueError:
            print("❌ ID必须是整数")

    elif command in ("help", "-h", "--help"):
        show_help()

    else:
        print(f"❌ 未知命令: {command}")
        show_help()


if __name__ == "__main__":
    main()