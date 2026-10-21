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
    """新增/更新一个数据库，支持改别名（original_alias 传旧别名）。

    密码留空时保留原有密码（按旧别名/现别名查找，先看已保存的，再看 env 种子）。
    """
    stored = _read_stored()
    dbs = _stored_databases(stored)
    alias = (cfg.get("alias") or "").strip()
    original = (cfg.get("original_alias") or "").strip()
    lookup = original or alias

    password = cfg.get("password") or ""
    if not password:
        saved = next((d for d in dbs if d["alias"].lower() == lookup.lower()), None)
        if saved:
            password = saved.get("password", "")
        else:
            seeded = next(
                (d for d in _env_databases() if d["alias"].lower() == lookup.lower()), None
            )
            if seeded:
                password = seeded.get("password", "")

    new_cfg = _normalize_db({**cfg, "alias": alias, "password": password}, "user")

    # 移除旧条目（改名时按旧别名，同名时按现别名），再写入新条目
    drop = lookup.lower()
    dbs = [d for d in dbs if d["alias"].lower() != drop]
    dbs.append(new_cfg)

    deleted = stored.get("deleted_databases", [])
    # 改了别名且旧别名来自 env → 记进 deleted，避免 env 下次又冒出来
    if original and original.lower() != alias.lower():
        if any(d["alias"].lower() == original.lower() for d in _env_databases()):
            if original.lower() not in {a.lower() for a in deleted}:
                deleted.append(original)
    # 现别名不再算「已删除」
    deleted = [a for a in deleted if a.lower() != alias.lower()]

    stored["databases"] = dbs
    stored["deleted_databases"] = deleted
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


# ------------------------------------------------------------
# IoTDB 时序库（多连接，按别名管理）
# ------------------------------------------------------------
def _norm_iotdb(d: dict) -> dict:
    return {
        "alias": (d.get("alias") or "default").strip(),
        "host": (d.get("host") or "").strip(),
        "port": int(d.get("port") or 6667),
        "user": (d.get("user") or "").strip(),
        "password": d.get("password") or "",
    }


def get_iotdbs() -> list[dict]:
    """所有 IoTDB 连接（含 alias）。settings.json 优先；兼容旧单条 iotdb 与 env 种子。"""
    stored = _read_stored()
    if isinstance(stored.get("iotdbs"), list):
        raw = stored["iotdbs"]
    elif isinstance(stored.get("iotdb"), dict):  # 旧单条迁移
        raw = [stored["iotdb"]]
    else:
        raw = []
    out = [_norm_iotdb(d) for d in raw if isinstance(d, dict)]
    if not out:
        out = [_norm_iotdb({**_env_iotdb(), "alias": "default"})]
    return out


def get_iotdb(alias: str = "") -> dict | None:
    """按别名取单个 IoTDB 连接；alias 为空返回第一条（默认）。"""
    iotdbs = get_iotdbs()
    if not iotdbs:
        return None
    if alias:
        for d in iotdbs:
            if d["alias"].lower() == alias.lower():
                return d
        return None
    return iotdbs[0]


def add_or_update_iotdb(cfg: dict) -> None:
    """新增/更新一个 IoTDB 连接（按别名；支持改名 original_alias）。密码留空保留原密码。"""
    stored = _read_stored()
    alias = (cfg.get("alias") or "default").strip()
    original = (cfg.get("original_alias") or "").strip()
    lookup = original or alias
    password = cfg.get("password") or ""
    if not password:
        existing = get_iotdb(lookup)
        if existing:
            password = existing["password"]
    entry = _norm_iotdb({**cfg, "alias": alias, "password": password})
    iotdbs = get_iotdbs()
    for i, d in enumerate(iotdbs):
        if d["alias"].lower() == lookup.lower():
            iotdbs[i] = entry
            break
    else:
        iotdbs.append(entry)
    stored["iotdbs"] = iotdbs
    stored.pop("iotdb", None)  # 清掉旧字段
    _write_stored(stored)


def delete_iotdb(alias: str) -> bool:
    """删除一个 IoTDB 连接。返回是否真的删掉了。"""
    stored = _read_stored()
    iotdbs = get_iotdbs()
    remaining = [d for d in iotdbs if d["alias"].lower() != alias.lower()]
    if len(remaining) == len(iotdbs):
        return False
    stored["iotdbs"] = remaining
    _write_stored(stored)
    return True


# ------------------------------------------------------------
# 本机日志目录（多来源，按别名管理）
# ------------------------------------------------------------
def _split_dirs(dirs) -> list[str]:
    if isinstance(dirs, str):
        dirs = dirs.replace("\n", ",").split(",")
    return [str(x).strip() for x in (dirs or []) if str(x).strip()]


def get_log_sources() -> list[dict]:
    """所有日志来源（含 alias + dirs）。settings.json 优先；兼容旧 log_dirs 与 env 种子。"""
    stored = _read_stored()
    if isinstance(stored.get("log_sources"), list):
        raw = stored["log_sources"]
    else:
        legacy = stored.get("log_dirs")
        raw = [{"alias": "default", "dirs": legacy}] if isinstance(legacy, list) and legacy else []
    out = []
    for d in raw:
        if isinstance(d, dict) and d.get("alias"):
            out.append({"alias": d["alias"].strip(), "dirs": _split_dirs(d.get("dirs"))})
    if not out:
        out = [{"alias": "default", "dirs": _env_log_dirs()}]
    return out


def get_log_dirs(alias: str = "") -> list[str]:
    """按别名取日志目录列表；alias 为空返回第一条（默认）。"""
    sources = get_log_sources()
    if not sources:
        return []
    if alias:
        for s in sources:
            if s["alias"].lower() == alias.lower():
                return s["dirs"]
        return []
    return sources[0]["dirs"]


def add_or_update_log_source(cfg: dict) -> None:
    """新增/更新一个日志来源（按别名；支持改名 original_alias）。"""
    stored = _read_stored()
    alias = (cfg.get("alias") or "default").strip()
    original = (cfg.get("original_alias") or "").strip()
    lookup = original or alias
    entry = {"alias": alias, "dirs": _split_dirs(cfg.get("dirs"))}
    sources = get_log_sources()
    for i, s in enumerate(sources):
        if s["alias"].lower() == lookup.lower():
            sources[i] = entry
            break
    else:
        sources.append(entry)
    stored["log_sources"] = sources
    stored.pop("log_dirs", None)  # 清掉旧字段
    _write_stored(stored)


def delete_log_source(alias: str) -> bool:
    """删除一个日志来源。返回是否真的删掉了。"""
    stored = _read_stored()
    sources = get_log_sources()
    remaining = [s for s in sources if s["alias"].lower() != alias.lower()]
    if len(remaining) == len(sources):
        return False
    stored["log_sources"] = remaining
    _write_stored(stored)
    return True


# ------------------------------------------------------------
# 系统提示词（自定义附加，网页编辑）
# ------------------------------------------------------------
def get_system_prompt_extra() -> str:
    """settings.json 里用户自定义的附加提示词（追加在系统提示词末尾，优先级最高）。"""
    stored = _read_stored()
    val = stored.get("system_prompt_extra", "")
    return val if isinstance(val, str) else ""


def set_system_prompt_extra(text: str) -> None:
    """保存自定义附加提示词；传空字符串视为清空（删除该字段）。"""
    stored = _read_stored()
    text = (text or "").strip()
    if text:
        stored["system_prompt_extra"] = text
    else:
        stored.pop("system_prompt_extra", None)
    _write_stored(stored)
