"""日期时间工具：让 agent 知道"今天是几号"以及数据库各表数据最新到哪天。

用途：当用户没有指定分析时间段时，agent 应调用本工具获取今天日期，
默认分析**当日**的数据。同时返回各表最新数据日期，避免分析空数据
（例如任务表 t_prod_reports 可能滞后多天）。

支持多库：db 参数指定数据库别名（见 tools/_db_config.py）。
"""
import json
from datetime import datetime

import psycopg2
from langchain_core.tools import tool

from tools._db_config import get_database

# 表 → 时间列（用于查最新数据日期）。注：这三张表是 agile_dispatch 库的 schema，
# 其他库可能没有这些表，届时对应项会返回「查询失败」，不影响其余信息。
_TABLES = [
    ("t_prod_reports", "task_start_time"),
    ("t_device_statistical_metrics", "start_at"),
    ("t_device_statistical_metrics_agg", "time_window"),
]


def _latest_dates(db: str) -> dict:
    """查各表最新数据日期。数据库不可达时快速失败，只回退错误说明。"""
    result: dict = {}
    cfg = get_database(db)
    if cfg is None:
        return {"error": f"未知数据库别名 {db!r}（可用库见 list_databases()）"}

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
        return {"error": f"数据库连接失败: {e}"}
    try:
        cur = conn.cursor()
        for table, col in _TABLES:
            try:
                cur.execute(f"SELECT max({col}) FROM {table}")
                latest = cur.fetchone()[0]
                result[f"{table}.{col}"] = str(latest) if latest else None
            except Exception as e:
                result[f"{table}.{col}"] = f"查询失败: {e}"
        return result
    finally:
        conn.close()


@tool
def get_current_date(db: str = "") -> str:
    """获取今天日期和数据库各表的最新数据日期。

    **当用户没有指定分析时间段时，先调用本工具**，拿到今天日期后默认分析
    【当日】的数据。同时返回数据库各表最新数据日期——如果当日没有数据
    （如任务表滞后），应如实告诉用户，并建议分析最新可用日期。

    Args:
        db: 数据库别名（见【可用数据库】清单）。用户在提问里提到某个库名/别名时传对应别名；
            没提或不确定时不传（用默认库）。

    Returns:
        JSON 字符串，含 today（今天日期）、current_time（当前时间）、
        data_latest（各表最新数据日期）。
    """
    now = datetime.now()
    info = {
        "today": now.strftime("%Y-%m-%d"),
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "data_latest": _latest_dates(db),
    }
    return json.dumps(info, ensure_ascii=False, indent=2)
