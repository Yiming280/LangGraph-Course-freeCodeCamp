"""数据库 schema 概览工具：一次性返回某库的全部表名 + 列清单 + 近似行数。

让模型在分析前先拿到 ground-truth schema，避免逐张表猜列名（小模型常见幻觉）。
放在独立模块，靠 tools/__init__.py 的 pkgutil 自动发现注册。
"""
import psycopg2
from langchain_core.tools import tool

from tools._db_config import get_database


@tool
def describe_database(db: str = "") -> str:
    """一次性返回某数据库的完整 schema 概览：所有表名、每张表的列清单、近似行数。

    分析任何数据前，**先调用本工具**拿到表/列的真实清单，再据此写 SQL，
    不要凭印象猜列名。行数为近似值（统计信息），需要精确数量时再对单表 count(*)。

    Args:
        db: 数据库别名（见【可用数据库】清单）。没提或不确定时不传（用默认库）。
    """
    cfg = get_database(db)
    if cfg is None:
        return f"未知数据库别名 {db!r}（可用库见 list_databases()）"
    try:
        conn = psycopg2.connect(
            host=cfg["host"],
            port=cfg["port"],
            dbname=cfg["dbname"],
            user=cfg["user"],
            password=cfg["password"],
            connect_timeout=5,
        )
    except Exception as e:
        return f"连接失败 - {e}"

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.relname AS table_name,
                   COALESCE(st.n_live_tup, 0)::bigint AS approx_rows,
                   (SELECT string_agg(col.column_name, ', ' ORDER BY col.ordinal_position)
                      FROM information_schema.columns col
                     WHERE col.table_schema = 'public' AND col.table_name = c.relname) AS cols
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
              LEFT JOIN pg_stat_user_tables st
                     ON st.relname = c.relname AND st.schemaname = 'public'
             WHERE n.nspname = 'public' AND c.relkind = 'r'
             ORDER BY c.relname
            """
        )
        rows = cur.fetchall()
        lines = [f"库内共 {len(rows)} 张表（行数为近似值）:"]
        for table_name, approx_rows, cols in rows:
            lines.append(f"- {table_name} (~{approx_rows} 行)\n    列: {cols or '(无)'}")
        return "\n".join(lines)
    except Exception as e:
        return f"Database error: {e}"
    finally:
        conn.close()
