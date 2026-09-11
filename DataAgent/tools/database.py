"""数据库工具：只读查询 PostgreSQL（支持多库，按别名选择）。

连接参数从环境变量读取，见 tools/_db_config.py 的约定：
  DATABASES=别名:描述,...  声明别名列表；DB_<别名>_HOST/PORT/DBNAME/USER/PASSWORD 每个库的连接。
"""
import psycopg2
import psycopg2.extras
from langchain_core.tools import tool

from tools._db_config import get_database, get_databases


def _connect(db: str):
    """按别名建立连接。返回 (conn, error)；失败时 conn 为 None。"""
    cfg = get_database(db)
    if cfg is None:
        return None, f"未知数据库别名 {db!r}（可用库见 list_databases()）"
    try:
        conn = psycopg2.connect(
            host=cfg["host"],
            port=cfg["port"],
            dbname=cfg["dbname"],
            user=cfg["user"],
            password=cfg["password"],
            connect_timeout=5,  # 连接超时，DB 不可达时快速失败
        )
        return conn, None
    except Exception as e:
        return None, f"连接失败 - {e}"


def test_connection(cfg: dict) -> tuple[bool, str]:
    """测试数据库连接（供网页端「测试连接」使用）。返回 (ok, message)。

    cfg 需含 host/port/dbname/user/password，直接连一次并统计 public schema 表数。
    """
    try:
        conn = psycopg2.connect(
            host=cfg["host"],
            port=cfg["port"],
            dbname=cfg["dbname"],
            user=cfg.get("user", ""),
            password=cfg.get("password", ""),
            connect_timeout=5,
        )
    except Exception as e:
        return False, f"连接失败：{e}"
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        )
        n = cur.fetchone()[0]
        return True, f"连接成功，public schema 下有 {n} 张表"
    except Exception as e:
        return True, f"连接成功（查询表数失败：{e}）"
    finally:
        conn.close()


@tool
def query_database(sql: str, db: str = "") -> str:
    """查询 PostgreSQL 数据库并返回结果。

    用它来探索 schema 和提取数据。只允许 SELECT 查询（只读），其他语句会直接报错。

    Args:
        sql: 要执行的 SELECT 查询语句。
            常用起步查询：
              - 查看所有表：SELECT table_name FROM information_schema.tables WHERE table_schema='public'
              - 查看某表结构：SELECT column_name, data_type FROM information_schema.columns WHERE table_name='xxx'
            数据量大时请用 GROUP BY / ORDER BY ... LIMIT 控制行数。
        db: 数据库别名（见【可用数据库】清单）。用户在提问里提到某个库名/别名时传对应别名；
            没提或不确定时不传（用默认库）。
    """
    sql = sql.strip()
    if not sql.upper().startswith("SELECT"):
        return "Database error: 只允许 SELECT 查询（本 agent 为只读）。"

    conn, err = _connect(db)
    if err:
        return f"Database error: {err}"

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


@tool
def list_databases() -> str:
    """列出所有可用数据库的别名及用途描述（不含密码）。

    当不确定用户提到的库名对应哪个别名时，先调用本工具确认，
    再把别名传给 query_database / get_current_date 的 db 参数。

    Returns:
        每行一个库：别名 + 用途描述。
    """
    dbs = get_databases()
    if not dbs:
        return "未配置任何数据库（请检查 .env 的 DATABASES / DB_* 配置）。"
    lines = []
    for d in dbs:
        desc = f" —— {d['description']}" if d.get("description") else ""
        lines.append(f"- {d['alias']}{desc}")
    return "\n".join(lines)
