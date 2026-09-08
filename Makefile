# agent-pro 便捷命令

install:
	uv pip install -e ".[dev]"

run:
	python main.py

test:
	pytest tests/

lint:
	ruff check src tests

# 各版本里程碑入口（示例）
v01: run

.PHONY: install run test lint v01
