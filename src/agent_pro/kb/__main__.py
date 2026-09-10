# kb/__main__.py | v0.6 诞生(CLI: uv run python -m agent_pro.kb <cmd>)
import argparse
from agent_pro.config import get_settings
from agent_pro.kb.ingest import ingest_dir
from agent_pro.kb.store import get_kb_store


def main():
    ap = argparse.ArgumentParser(prog="agent-pro-kb",
                                 description="私有知识库管理")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import", help="扫描KB_DIR导入(按文档替换, 幂等)")
    sub.add_parser("reindex", help="全量重建(清空索引后重导)")
    sub.add_parser("stats", help="索引统计")
    p = sub.add_parser("search", help="调试检索")
    p.add_argument("query")
    args = ap.parse_args()
    kb_dir = get_settings().kb_dir
    if args.cmd in ("import", "reindex"):
        print(ingest_dir(kb_dir, purge=(args.cmd == "reindex")))
    elif args.cmd == "stats":
        for k, v in get_kb_store().stats().items():
            print(f"{k}: {v}")
    else:  # search
        hits = get_kb_store().search(args.query)
        if not hits:
            print("知识库未命中")
        for i, r in enumerate(hits, 1):
            print(f"\n[{i}] {r['source']} › {r['heading_path']}\n{r['content']}")


if __name__ == "__main__":
    main()