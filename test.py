from agent_pro.memory.manager import SessionManager
from agent_pro.memory.store import get_memory_db

sm = SessionManager('selftest')
d = [
    ('user','你好'),
    ('assistant','你好！有什么可以帮你？'),
    ('user','我习惯用uv管理Python项目，编辑器用Neovim，Python用3.12'),
    ('assistant','已了解你的工具偏好。'),
    ('user','今天天气不错'),
    ('assistant','是的。'),
    ('user','我在做一个叫agent-pro的多agent调度项目'),
    ('assistant','收到，agent-pro项目。'),
    ('user','项目用SQLite存状态'),
    ('assistant','好的。'),
    ('user','哈哈'),
    ('assistant','嗯。'),
    ('user','明天继续'),
    ('assistant','好的，明天见。')
]

for r,c in d:
    getattr(sm, f'append_{r}')(c)

sm.maybe_compact()
sm.maybe_compact()
db = get_memory_db()

print('== 摘要 ==\n', db.get_summary('selftest'))
print('== 提取的记忆 ==')
for m in db.list_memories():
    print(f"[{m['kind']}] {m['content']}")
print('== 剩余active轮数 ==', db.max_active_round('selftest'))