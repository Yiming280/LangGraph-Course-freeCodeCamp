"""向后兼容垫片：数据库配置逻辑已迁移到 tools/_settings.py。

保留本文件，让 tools/database.py / tools/current_time.py / agent.py 里旧的
`from tools._db_config import ...` 继续可用。新代码请直接 import tools._settings。
"""
from tools._settings import (  # noqa: F401
    add_or_update_database as add_or_update_user_database,
    databases_summary,
    delete_database as delete_user_database,
    get_database,
    get_databases,
    get_default_alias,
)
