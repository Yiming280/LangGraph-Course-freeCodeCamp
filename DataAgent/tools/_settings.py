"""统一设置存储：数据库 / IoTDB / 日志目录，全部可网页编辑，落盘 settings.json。

合并规则（每次读取即时计算，改完无需重启即可生效）：
  - .env 是「种子 / 默认值」。
  - settings.json 里出现的数据库按别名覆盖 env 同名库；env 里被删除的别名记进
    deleted_databases，保证删除跨重启持久（否则每次启动又冒出来）。
  - iotdb / log_dirs：settings.json 有值则覆盖 env，否则回退 env。
  - 旧 databases.json（网页新增库）在首次写入时自动迁移进 settings.json。

本模块以下划线开头，不会被 tools/__init__.py 当作工具扫描。
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_SETTINGS_FILE = Path(__file__).resolve().parent.parent / "settings.json"
_LEGACY_DB_FILE = Path(__file__).resolve().parent.parent / "databases.json"


# ------------------------------------------------------------
# env 种子解析（.env 里的默认值）
# ------------------------------------------------------------
def _env_databases() -> list[dict]:
    """解析 DATABASES + DB_<别名>_* → 数据库列表（兼容旧单库 DB_* 配置）。"""
    raw = os.getenv("DATABASES", "").strip()
    aliases: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        alias, _, desc = item.partition(":")
        aliases.append((alias.strip(), desc.strip()))

    dbs: list[dict] = []
    for alias, desc in aliases:
        prefix = f"DB_{alias.upper()}_"
        host = os.getenv(prefix + "HOST")
        if not host:
            continue
        dbs.append(
            {
                "alias": alias,
                "description": desc,
                "host": host,
                "port": os.getenv(prefix + "PORT", "5432"),
                "dbname": os.getenv(prefix + "DBNAME", alias),
                "user": os.getenv(prefix + "USER", ""),
                "password": os.getenv(prefix + "PASSWORD", ""),
            }
        )

    # 旧单库配置回退
    if not dbs:
        host = os.getenv("DB_HOST")
        if host:
            dbs.append(
                {
                    "alias": os.getenv("DB_NAME", "default"),
                    "description": "默认库（旧 DB_* 配置）",
                    "host": host,
                    "port": os.getenv("DB_PORT", "5432"),
                    "dbname": os.getenv("DB_NAME", ""),
                    "user": os.getenv("DB_USER", ""),
                    "password": os.getenv("DB_PASSWORD", ""),
                }
            )
    return dbs


def _env_iotdb() -> dict:
    return {
        "host": os.getenv("IOTDB_HOST", "10.8.22.20"),
        "port": int(os.getenv("IOTDB_PORT", "6667")),
        "user": os.getenv("IOTDB_USER", "root"),
        "password": os.getenv("IOTDB_PASSWORD", "root"),
    }


def _env_log_dirs() -> list[str]:
    raw = os.getenv("LOG_DIRS", "").strip() or os.getenv("LOG_DIR", "/var/log").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


# ------------------------------------------------------------
# settings.json 读写
# ------------------------------------------------------------
def _read_stored() -> dict:
    """读取 settings.json；不存在或损坏返回 {}。"""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_stored(data: dict) -> None:
    _SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _legacy_user_databases() -> list[dict]:
    """旧 databases.json 里的网页新增库（迁移用）。"""
    if not _LEGACY_DB_FILE.exists():
        return []
    try:
        data = json.loads(_LEGACY_DB_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("alias")]


def _stored_databases(stored: dict) -> list[dict]:
    """settings.json 里的数据库列表；还没写进 settings.json 时回退到旧 databases.json。"""
    if "databases" in stored:
        return [d for d in stored["databases"] if isinstance(d, dict) and d.get("alias")]
    return _legacy_user_databases()


def _normalize_db(d: dict, source: str) -> dict:
    return {
        "alias": (d.get("alias") or "").strip(),
        "description": (d.get("description") or "").strip(),
        "host": (d.get("host") or "").strip(),
        "port": str(d.get("port") or "5432").strip(),
        "dbname": (d.get("dbname") or "").strip(),
        "user": (d.get("user") or "").strip(),
        "password": d.get("password") or "",
        "source": source,
    }


# ------------------------------------------------------------
# 读取（env 种子 + settings.json 合并）
# ------------------------------------------------------------
def get_databases() -> list[dict]:
    """所有数据库（含密码），带 source 标记（env=来自环境变量 / user=网页配置）。"""
    stored = _read_stored()
    deleted = {a.lower() for a in stored.get("deleted_databases", []) if a}

    merged: list[dict] = []
    for d in _env_databases():
        if d["alias"].lower() in deleted:
            continue
        merged.append(_normalize_db(d, "env"))
    for d in _stored_databases(stored):
        norm = _normalize_db(d, "user")
        for i, m in enumerate(merged):
            if m["alias"].lower() == norm["alias"].lower():
                merged[i] = norm
                break
        else:
            merged.append(norm)
    return merged


def get_database(alias: str) -> dict | None:
    dbs = get_databases()
    if not dbs:
        return None
    if not alias:
        default = get_default_alias()
        for d in dbs:
            if d["alias"] == default:
                return d
        return dbs[0]
    for d in dbs:
        if d["alias"].lower() == alias.lower():
            return d
    return None


def get_default_alias() -> str:
    dbs = get_databases()
    if not dbs:
        return ""
    stored = _read_stored()
    default = (stored.get("default_database") or os.getenv("DEFAULT_DATABASE", "")).strip()
    if default:
        for d in dbs:
            if d["alias"].lower() == default.lower():
                return d["alias"]
    return dbs[0]["alias"]


def databases_summary() -> str:
    """生成注入系统提示词的「可用数据库」清单文本。"""
    dbs = get_databases()
    if not dbs:
        return ""
    default = get_default_alias()
    lines = [
        "",
        "【可用数据库】（query_database / get_current_date 用 db 参数指定别名；不传或传空则用默认库）",
    ]
    for d in dbs:
        tag = "（默认）" if d["alias"] == default else ""
        desc = f"：{d['description']}" if d.get("description") else ""
        lines.append(f"- {d['alias']}{tag}{desc}")
    return "\n".join(lines)


# ------------------------------------------------------------
# 写（网页编辑）
# ------------------------------------------------------------
def add_or_update_database(cfg: dict) -> None:
    """新增/更新一个数据库。密码留空时保留原有密码（先看已保存的，再看 env 种子）。"""
    stored = _read_stored()
    dbs = _stored_databases(stored)
    alias = cfg["alias"]

    password = cfg.get("password") or ""
    if not password:
        saved = next((d for d in dbs if d["alias"].lower() == alias.lower()), None)
        if saved:
            password = saved.get("password", "")
        else:
            seeded = next(
                (d for d in _env_databases() if d["alias"].lower() == alias.lower()), None
            )
            if seeded:
                password = seeded.get("password", "")

    new_cfg = _normalize_db({**cfg, "password": password}, "user")
    existing = next((d for d in dbs if d["alias"].lower() == alias.lower()), None)
    if existing:
        dbs[dbs.index(existing)] = new_cfg
    else:
        dbs.append(new_cfg)

    stored["databases"] = dbs
    # 编辑后，该别名不再算「已删除」
    stored["deleted_databases"] = [
        a for a in stored.get("deleted_databases", []) if a.lower() != alias.lower()
    ]
    _write_stored(stored)


def delete_database(alias: str) -> bool:
    """删除一个数据库（env 或网页配置都可删）。返回是否真的删掉了。"""
    before = {d["alias"].lower() for d in get_databases()}
    if alias.lower() not in before:
        return False
    stored = _read_stored()
    stored["databases"] = [
        d
        for d in _stored_databases(stored)
        if d.get("alias", "").lower() != alias.lower()
    ]
    deleted = stored.get("deleted_databases", [])
    if alias.lower() not in {a.lower() for a in deleted}:
        deleted.append(alias)
    stored["deleted_databases"] = deleted
    _write_stored(stored)
    return True


def get_iotdb() -> dict:
    """返回 IoTDB 连接参数（settings.json 覆盖 env）。"""
    stored = _read_stored()
    env = _env_iotdb()
    s = stored.get("iotdb")
    if not isinstance(s, dict) or not (s.get("host") or "").strip():
        return env
    return {
        "host": (s.get("host") or env["host"]).strip(),
        "port": int(s.get("port") or env["port"]),
        "user": (s.get("user") or env["user"]).strip(),
        "password": s.get("password") if s.get("password") is not None else env["password"],
    }


def save_iotdb(cfg: dict) -> None:
    """保存 IoTDB 连接参数。密码留空时保留原密码。"""
    stored = _read_stored()
    password = cfg.get("password") or ""
    if not password:
        password = get_iotdb()["password"]
    stored["iotdb"] = {
        "host": (cfg.get("host") or "").strip(),
        "port": int(cfg.get("port") or 6667),
        "user": (cfg.get("user") or "").strip(),
        "password": password,
    }
    _write_stored(stored)


def get_log_dirs() -> list[str]:
    """返回日志白名单目录列表（settings.json 覆盖 env）。"""
    stored = _read_stored()
    dirs = stored.get("log_dirs")
    if isinstance(dirs, list) and dirs:
        return [str(d).strip() for d in dirs if str(d).strip()]
    return _env_log_dirs()


def save_log_dirs(dirs: list[str]) -> None:
    """保存日志白名单目录列表（逗号/换行分隔后清理）。"""
    stored = _read_stored()
    stored["log_dirs"] = [d.strip() for d in dirs if d and d.strip()]
    _write_stored(stored)
