"""Apache IoTDB 时序数据库工具：只读查询。

IoTDB 记录产线工位状态变换（root.iot 库，TTL=7 天，时间精度毫秒）。
连接参数从 tools/_settings.py 读取（网页可编辑，settings.json 覆盖 env 种子），
默认 10.8.22.20:6667 / root / root（IoTDB 默认账号）：
    IOTDB_HOST / IOTDB_PORT / IOTDB_USER / IOTDB_PASSWORD

数据模型：
    root.iot.device.<设备ID>.group.<工位组ID>.<测点>
    多台设备，每台设备下有多个工位组（group）；每个 group 下 11 个测点：
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

from tools._settings import get_iotdb, get_iotdbs

load_dotenv()

# 只读白名单：仅放行这些查询前缀，其余（INSERT/DELETE/CREATE/DROP/ALTER/SET…）一律拒绝
_READONLY_RE = re.compile(r"^\s*(SELECT|SHOW|COUNT|DESCRIBE|LIST|EXPLAIN)\b", re.IGNORECASE)

# 上下文保护：最多返回 10 行 / 10 列 / 6000 字符，超长结果省略并提示总数。
# _MAX_CHARS 限的是「喂回模型的完整结果」——太大（如 2 万字符）会把上下文撑爆，
# 加剧思考模型在后续轮次被截断（只思考、无回答）的概率，这里收紧到 6000。
_MAX_ROWS = 10
_MAX_COLS = 10
_MAX_CHARS = 6000


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


def _run_query(sql: str, alias: str = "") -> tuple:
    """执行一条只读语句，返回 (DataFrame|None, error_str)；成功时 error_str 为空。

    从 _query 里拆出来，供需要拿到原始 DataFrame 再自己汇总的工具复用
    （如 list_iotdb_devices 要去重到设备级）。
    """
    cfg = get_iotdb(alias)
    if cfg is None:
        return None, f"IoTDB error: 未知连接别名 {alias!r}（可用连接见 list_iotdb_sources）"
    session = Session(cfg["host"], cfg["port"], cfg["user"], cfg["password"])
    try:
        session.open(enable_rpc_compression=False)
    except Exception as e:
        return None, f"IoTDB error: 连接失败 - {e}"
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
        return None, f"IoTDB error: {e}"
    finally:
        try:
            session.close()
        except Exception:
            pass
    return df, ""


def _query(sql: str, alias: str = "") -> str:
    """执行一条只读语句，返回格式化字符串；失败返回错误字符串（不抛异常）。"""
    df, err = _run_query(sql, alias)
    if err:
        return err
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
def list_iotdb_sources() -> str:
    """列出所有已配置的 IoTDB 连接（别名 + 主机），用于选择用哪个时序库。

    不确定有哪些 IoTDB 连接、或用户提到某个具体时序库时，先调用本工具拿到别名，
    再把别名传给 query_iotdb / list_iotdb_devices / show_iotdb_timeseries 的 alias 参数。
    """
    iotdbs = get_iotdbs()
    if not iotdbs:
        return "未配置任何 IoTDB 连接。"
    lines = []
    for d in iotdbs:
        tag = "（默认）" if iotdbs.index(d) == 0 else ""
        lines.append(f"- {d['alias']}{tag} —— {d['host']}:{d['port']} / {d['user']}")
    return "\n".join(lines)


@tool
def query_iotdb(sql: str = "", alias: str = "") -> str:
    """执行 Apache IoTDB 只读查询（SELECT / SHOW / COUNT / DESCRIBE / LIST）。

    先 list_iotdb_sources 看有哪些连接，再 list_iotdb_databases / list_iotdb_devices /
    show_iotdb_timeseries 摸清结构，再用本工具查数据。
    时间戳单位毫秒；root.iot 数据 TTL=7 天，太老的数据查不到。

    Args:
        sql: IoTDB 原生 SQL（只读）。常用：
             - 看某工位最新状态：SELECT LAST * FROM root.iot.device.<设备ID>.group.*
             - 查某测点近期时序：SELECT * FROM root.iot.** WHERE time > <毫秒时间戳> LIMIT 100
             数据量大务必加 LIMIT，避免结果过大。
        alias: 可选，IoTDB 连接别名（见 list_iotdb_sources）；不传用默认（第一个）。
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
    return _query(sql, alias)


@tool
def list_iotdb_databases(alias: str = "") -> str:
    """列出指定 IoTDB 连接的所有 database（库）。

    业务数据在 root.iot；root.__audit 是密码历史，与业务无关。

    Args:
        alias: 可选，IoTDB 连接别名；不传用默认（第一个）。
    """
    return _query("SHOW DATABASES", alias)


def _with_wildcard(p: str) -> str:
    """路径补全：未以 * 结尾时追加 .**，匹配该前缀下的所有设备/测点。"""
    return p + ".**" if p and not p.endswith("*") else p


def _summarize_devices(df) -> str:
    """SHOW DEVICES 结果 → 按设备级（device）去重汇总。

    SHOW DEVICES 返回的是「设备.group.<工位组ID>」粒度，一台设备带 7~10 个组，
    全列出来是几十行 UUID 噪音、撑爆上下文。这里按 device 前缀去重，只保留
    设备数 + 每台设备几个组，同时给出可直接传给 show_iotdb_timeseries / query_iotdb
    的路径前缀。
    """
    if df is None or len(df) == 0:
        return "(0 rows)"
    col = "Device" if "Device" in df.columns else df.columns[0]
    devices: dict[str, int] = {}
    for v in df[col].astype(str):
        # root.iot.device.<设备ID>.group.<工位组ID> → 截到 device 级
        key = v.split(".group.")[0] if ".group." in v else v
        devices[key] = devices.get(key, 0) + 1
    lines = [f"共 {len(devices)} 台设备，{sum(devices.values())} 个工位组："]
    for dev, n in devices.items():
        lines.append(f"- {dev}（{n} 个组）")
    return "\n".join(lines)


@tool
def list_iotdb_devices(path: str = "", alias: str = "") -> str:
    """列出指定 IoTDB 连接的设备（device）清单，用于摸清有哪些工位/设备。

    Args:
        path: 可选，指定前缀路径（如 root.iot 或某 device 路径）；不传则列全部设备。
        alias: 可选，IoTDB 连接别名；不传用默认（第一个）。
    """
    p = _with_wildcard((path or "").strip())
    df, err = _run_query(f"SHOW DEVICES {p or 'root.**'}", alias)
    if err:
        return err
    return _summarize_devices(df)


@tool
def show_iotdb_timeseries(path: str = "", alias: str = "") -> str:
    """查看指定 IoTDB 连接的 timeseries（测点）结构：路径、数据类型等。

    Args:
        path: 可选，指定前缀（如 root.iot 或某 device/group 路径）；不传则列全部。
        alias: 可选，IoTDB 连接别名；不传用默认（第一个）。
    """
    p = _with_wildcard((path or "").strip())
    return _query(f"SHOW TIMESERIES {p or 'root.**'}", alias)
