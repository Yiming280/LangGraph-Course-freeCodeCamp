"""数据库工具：只读查询 PostgreSQL。

连接参数从环境变量读取（DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD）。
为了独立可复用，这里自己也 load_dotenv()，即使被单独 import 也能读到配置。
"""
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()


@tool
def query_database(sql: str) -> str:
    """查询 PostgreSQL 数据库并返回结果。

    用它来探索 schema 和提取数据。只允许 SELECT 查询（只读），其他语句会直接报错。

    Args:
        sql: 要执行的 SELECT 查询语句。
            常用起步查询：
              - 查看所有表：SELECT table_name FROM information_schema.tables WHERE table_schema='public'
              - 查看某表结构：SELECT column_name, data_type FROM information_schema.columns WHERE table_name='xxx'
            数据量大时请用 GROUP BY / ORDER BY ... LIMIT 控制行数。
    """
    sql = sql.strip()
    if not sql.upper().startswith("SELECT"):
        return "Database error: 只允许 SELECT 查询（本 agent 为只读）。"

    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=5,  # 连接超时，DB 不可达时快速失败
        )
    except Exception as e:
        return f"Database error: 连接失败 - {e}"
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql)
        rows = cur.fetchall()
        if not rows:
            return "(Query returned 0 rows)"
        return f"({len(rows)} rows)\n" + str(rows[:50])  # 最多返回 50 行，控制上下文
    except Exception as e:
        return f"Database error: {e}"
    finally:
        conn.close()
