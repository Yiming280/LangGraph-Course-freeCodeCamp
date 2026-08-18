"""日期时间工具：让 agent 知道"今天是几号"以及数据库各表数据最新到哪天。

用途：当用户没有指定分析时间段时，agent 应调用本工具获取今天日期，
默认分析**当日**的数据。同时返回各表最新数据日期，避免分析空数据
（例如任务表 t_prod_reports 可能滞后多天）。
"""
import json
import os
from datetime import datetime

import psycopg2
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

# 表 → 时间列（用于查最新数据日期）
_TABLES = [
    ("t_prod_reports", "task_start_time"),
    ("t_device_statistical_metrics", "start_at"),
    ("t_device_statistical_metrics_agg", "time_window"),
]


def _latest_dates() -> dict:
    """查各表最新数据日期。数据库不可达时快速失败，只回退错误说明。"""
    result: dict = {}
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
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
def get_current_date() -> str:
    """获取今天日期和数据库各表的最新数据日期。

    **当用户没有指定分析时间段时，先调用本工具**，拿到今天日期后默认分析
    【当日】的数据。同时返回数据库各表数据最新日期——如果当日没有数据
    （如任务表滞后），应如实告诉用户，并建议分析最新可用日期。

    Returns:
        JSON 字符串，含 today（今天日期）、current_time（当前时间）、
        data_latest（各表最新数据日期）。
    """
    now = datetime.now()
    info = {
        "today": now.strftime("%Y-%m-%d"),
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "data_latest": _latest_dates(),
    }
    return json.dumps(info, ensure_ascii=False, indent=2)
