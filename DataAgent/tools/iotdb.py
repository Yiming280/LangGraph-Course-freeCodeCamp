"""Apache IoTDB 时序数据库工具：只读查询。

IoTDB 记录产线工位状态变换（root.iot 库，TTL=7 天，时间精度毫秒）。
连接参数从 tools/_settings.py 读取（网页可编辑，settings.json 覆盖 env 种子），
默认 10.8.22.20:6667 / root / root（IoTDB 默认账号）：
    IOTDB_HOST / IOTDB_PORT / IOTDB_USER / IOTDB_PASSWORD

数据模型（已探明）：
    root.iot.device.<设备ID>.group.<工位组ID>.<测点>
    33 个设备，每个 group 下 11 个测点：
      Station(工位名/TEXT)  ProductionLine(产线/TEXT)  State(状态/INT32)
      AutoMode / Trigger / HeartBeat / AllowDocking / DockingFinish /
      LowerMaterialPresent / UpperMaterialPresent / WorksationAlarm（布尔）
    root.__audit 是密码历史，与业务无关，勿用。
"""
import re
from datetime import datetime

from dotenv import load_dotenv
from langchain_core.tools import tool

from iotdb.Session import Session

from tools._settings import get_iotdb

load_dotenv()

# 只读白名单：仅放行这些查询前缀，其余（INSERT/DELETE/CREATE/DROP/ALTER/SET…）一律拒绝
_READONLY_RE = re.compile(r"^\s*(SELECT|SHOW|COUNT|DESCRIBE|LIST|EXPLAIN)\b", re.IGNORECASE)

# 上下文保护：最多返回 10 行 / 10 列 / 20000 字符，超长结果省略并提示总数
_MAX_ROWS = 10
_MAX_COLS = 10
_MAX_CHARS = 20000


def test_iotdb_connection(cfg: dict) -> tuple[bool, str]:
    """测试 IoTDB 连接（供网页「测试连接」使用）。返回 (ok, message)。

    cfg 需含 host/port/user/password，连一次并列出 database 数。
    """
    try:
        session = Session(
            cfg["host"],
            int(cfg.get("port") or 6667),
            cfg.get("user") or "",
            cfg.get("password") or "",
        )
    except Exception as e:
        return False, f"参数错误：{e}"
    try:
        session.open(enable_rpc_compression=False)
    except Exception as e:
        return False, f"连接失败：{e}"
    try:
        ds = session.execute_query_statement("SHOW DATABASES")
        try:
            n = len(ds.todf())
        finally:
            try:
                ds.close_operation_handle()
            except Exception:
                pass
        return True, f"连接成功，共 {n} 个 database"
    except Exception as e:
        return False, f"连接成功但查询失败：{e}"
    finally:
        try:
            session.close()
        except Exception:
            pass


def _query(sql: str) -> str:
    """执行一条只读语句，返回格式化字符串；失败返回错误字符串（不抛异常）。"""
    cfg = get_iotdb()
    session = Session(cfg["host"], cfg["port"], cfg["user"], cfg["password"])
    try:
        session.open(enable_rpc_compression=False)
    except Exception as e:
        return f"IoTDB error: 连接失败 - {e}"
    try:
        ds = session.execute_query_statement(sql)
        try:
            df = ds.todf()
        finally:
            try:
                ds.close_operation_handle()
            except Exception:
                pass
    except Exception as e:
        return f"IoTDB error: {e}"
    finally:
        try:
            session.close()
        except Exception:
            pass
    return _format(df)


def _fmt_ts(v):
    """毫秒时间戳 → 可读时间字符串（转换失败原样返回）。"""
    try:
        n = int(v)
        return datetime.fromtimestamp(n / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return v


def _format(df) -> str:
    """DataFrame → 精简字符串；时间列转可读，行数/列数/长度三重截断。"""
    if df is None or len(df) == 0:
        return "(0 rows)"
    if "Time" in df.columns:
        try:
            df = df.copy()
            df["Time"] = df["Time"].map(_fmt_ts)
        except Exception:
            pass
    total_rows = len(df)
    total_cols = len(df.columns)
    # 宽表（如 SELECT * 出几百列）先截列，否则 to_string 会把每行渲染成几万字符
    if total_cols > _MAX_COLS:
        df = df.iloc[:, :_MAX_COLS]
    text = df.head(_MAX_ROWS).to_string(index=False)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n…(结果过长，已截断)"
    if total_rows > _MAX_ROWS:
        text += f"\n…(共 {total_rows} 行，仅显示前 {_MAX_ROWS} 行)"
    if total_cols > _MAX_COLS:
        text += f"\n…(共 {total_cols} 列，仅显示前 {_MAX_COLS} 列)"
    return text


@tool
def query_iotdb(sql: str = "") -> str:
    """执行 Apache IoTDB 只读查询（SELECT / SHOW / COUNT / DESCRIBE / LIST）。

    先 list_iotdb_databases / list_iotdb_devices / show_iotdb_timeseries 摸清结构，
    再用本工具查数据。时间戳单位毫秒；root.iot 数据 TTL=7 天，太老的数据查不到。

    Args:
        sql: IoTDB 原生 SQL（只读）。常用：
             - 看某工位最新状态：SELECT LAST * FROM root.iot.device.<设备ID>.group.*
             - 查某测点近期时序：SELECT * FROM root.iot.** WHERE time > <毫秒时间戳> LIMIT 100
             数据量大务必加 LIMIT，避免结果过大。
    """
    sql = (sql or "").strip()
    if not sql:
        return (
            "请提供 sql 参数（要查询的 IoTDB 语句）。示例：\n"
            "  SELECT LAST * FROM root.iot.**\n"
            "  SELECT * FROM root.iot.** WHERE time > 1780000000000 LIMIT 100"
        )
    if not _READONLY_RE.match(sql):
        return "IoTDB error: 只读工具，仅允许 SELECT / SHOW / COUNT / DESCRIBE / LIST 查询，不接受写操作。"
    return _query(sql)


@tool
def list_iotdb_databases() -> str:
    """列出 IoTDB 所有 database（库）。

    业务数据在 root.iot；root.__audit 是密码历史，与业务无关。
    """
    return _query("SHOW DATABASES")


def _with_wildcard(p: str) -> str:
    """路径补全：未以 * 结尾时追加 .**，匹配该前缀下的所有设备/测点。"""
    return p + ".**" if p and not p.endswith("*") else p


@tool
def list_iotdb_devices(path: str = "") -> str:
    """列出 IoTDB 设备（device）清单，用于摸清有哪些工位/设备。

    Args:
        path: 可选，指定前缀路径（如 root.iot 或某 device 路径）；不传则列全部设备。
    """
    p = _with_wildcard((path or "").strip())
    return _query(f"SHOW DEVICES {p or 'root.**'}")


@tool
def show_iotdb_timeseries(path: str = "") -> str:
    """查看 timeseries（测点）结构：路径、数据类型等。

    Args:
        path: 可选，指定前缀（如 root.iot 或某 device/group 路径）；不传则列全部。
    """
    p = _with_wildcard((path or "").strip())
    return _query(f"SHOW TIMESERIES {p or 'root.**'}")
