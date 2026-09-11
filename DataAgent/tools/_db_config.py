"""多数据库配置：从环境变量读取「别名 → 连接参数」。

下划线开头 = 辅助模块，不会被 tools/__init__.py 当作工具扫描（见该文件约定）。

.env 配置约定：
    # 别名列表（逗号分隔），格式 别名:描述（描述可选，帮助模型理解库的用途）
    DATABASES=agile_dispatch:AGV柔性产线调度库,warehouse:仓库库存库
    # 默认库（用户没提库名时用；不设则用列表第一个）
    DEFAULT_DATABASE=agile_dispatch
    # 每个别名的连接参数（别名转大写）
    DB_AGILE_DISPATCH_HOST=10.8.8.233
    DB_AGILE_DISPATCH_PORT=15432
    DB_AGILE_DISPATCH_DBNAME=agile_dispatch
    DB_AGILE_DISPATCH_USER=postgres
    DB_AGILE_DISPATCH_PASSWORD=xxx

兼容旧配置：若 DATABASES 未设置，回退到单库的 DB_HOST/PORT/NAME/USER/PASSWORD。
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 每个库需要的连接字段
_FIELDS = ("HOST", "PORT", "DBNAME", "USER", "PASSWORD")

# 网页端新增/编辑的数据库，持久化到该文件（与 .env 里的库合并，网页新增同名时覆盖 env）。
_USER_DB_FILE = Path(__file__).resolve().parent.parent / "databases.json"


def _parse_aliases() -> list[tuple[str, str]]:
    """解析 DATABASES 环境变量 → [(别名, 描述), ...]"""
    raw = os.getenv("DATABASES", "").strip()
    if not raw:
        return []
    out: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            alias, desc = item.split(":", 1)
        else:
            alias, desc = item, ""
        out.append((alias.strip(), desc.strip()))
    return out


def _conn_for(alias: str) -> dict | None:
    """按别名读连接参数；该别名未配置 HOST 时返回 None。"""
    prefix = f"DB_{alias.upper()}_"
    host = os.getenv(prefix + "HOST")
    if not host:
        return None
    return {
        "alias": alias,
        "description": "",
        "host": host,
        "port": os.getenv(prefix + "PORT", "5432"),
        "dbname": os.getenv(prefix + "DBNAME", alias),
        "user": os.getenv(prefix + "USER", ""),
        "password": os.getenv(prefix + "PASSWORD", ""),
    }


def _fallback_single() -> list[dict]:
    """兼容旧单库配置（DB_HOST/PORT/NAME/USER/PASSWORD）。"""
    host = os.getenv("DB_HOST")
    if not host:
        return []
    return [
        {
            "alias": os.getenv("DB_NAME", "default"),
            "description": "默认库（旧 DB_* 配置）",
            "host": host,
            "port": os.getenv("DB_PORT", "5432"),
            "dbname": os.getenv("DB_NAME", ""),
            "user": os.getenv("DB_USER", ""),
            "password": os.getenv("DB_PASSWORD", ""),
        }
    ]


def _load_user_databases() -> list[dict]:
    """读取网页端新增的数据库（databases.json）。文件缺失/损坏时返回空列表。"""
    if not _USER_DB_FILE.exists():
        return []
    try:
        data = json.loads(_USER_DB_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("alias")]


def _save_user_databases(dbs: list[dict]) -> None:
    """把网页端新增的数据库写回 databases.json。"""
    _USER_DB_FILE.write_text(
        json.dumps(dbs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_user_databases() -> list[dict]:
    """返回网页端新增的数据库列表（原始，含密码，仅后端内部使用）。"""
    return _load_user_databases()


def add_or_update_user_database(cfg: dict) -> None:
    """新增/更新一个网页端数据库。密码留空时保留原有密码。"""
    dbs = _load_user_databases()
    alias = cfg["alias"]
    existing = next((d for d in dbs if d["alias"].lower() == alias.lower()), None)
    password = cfg.get("password") or ""
    if existing and not password:
        password = existing.get("password", "")
    new_cfg = {
        "alias": alias,
        "description": (cfg.get("description") or "").strip(),
        "host": cfg["host"],
        "port": str(cfg.get("port", "5432")).strip() or "5432",
        "dbname": (cfg.get("dbname") or "").strip() or alias,
        "user": (cfg.get("user") or "").strip(),
        "password": password,
    }
    if existing:
        dbs[dbs.index(existing)] = new_cfg
    else:
        dbs.append(new_cfg)
    _save_user_databases(dbs)


def delete_user_database(alias: str) -> bool:
    """删除一个网页端数据库（env 里的库不在该列表，删不掉）。返回是否真的删除了。"""
    dbs = _load_user_databases()
    remaining = [d for d in dbs if d["alias"].lower() != alias.lower()]
    if len(remaining) == len(dbs):
        return False
    _save_user_databases(remaining)
    return True


def get_databases() -> list[dict]:
    """返回所有已配置数据库（env + 网页新增），带 source 标记（env/user）。

    网页新增的同名库覆盖 env 里的同名库；合并结果仍含 password，仅后端使用。
    """
    dbs: list[dict] = []
    for alias, desc in _parse_aliases():
        conn = _conn_for(alias)
        if conn:
            conn["description"] = desc
            conn["source"] = "env"
            dbs.append(conn)
    if not dbs:
        dbs = _fallback_single()
        for d in dbs:
            d["source"] = "env"

    merged: list[dict] = list(dbs)
    for ud in _load_user_databases():
        ud = dict(ud)
        ud["source"] = "user"
        for i, d in enumerate(merged):
            if d["alias"].lower() == ud["alias"].lower():
                merged[i] = ud
                break
        else:
            merged.append(ud)
    return merged


def get_default_alias() -> str:
    """返回默认库别名（前端展示「默认」标签用）。"""
    dbs = get_databases()
    if not dbs:
        return ""
    default = os.getenv("DEFAULT_DATABASE", "").strip()
    if default:
        for d in dbs:
            if d["alias"].lower() == default.lower():
                return d["alias"]
    return dbs[0]["alias"]


def get_database(alias: str) -> dict | None:
    """按别名取单个库；alias 为空则取默认库。找不到返回 None。"""
    dbs = get_databases()
    if not dbs:
        return None
    if not alias:
        default = os.getenv("DEFAULT_DATABASE", "").strip()
        if default:
            for d in dbs:
                if d["alias"].lower() == default.lower():
                    return d
        return dbs[0]
    for d in dbs:
        if d["alias"].lower() == alias.lower():
            return d
    return None


def databases_summary() -> str:
    """生成注入系统提示词的「可用数据库」清单文本。"""
    dbs = get_databases()
    if not dbs:
        return ""
    default = os.getenv("DEFAULT_DATABASE", "").strip() or dbs[0]["alias"]
    lines = [
        "",
        "【可用数据库】（query_database / get_current_date 用 db 参数指定别名；不传或传空则用默认库）",
    ]
    for d in dbs:
        tag = "（默认）" if d["alias"] == default else ""
        desc = f"：{d['description']}" if d.get("description") else ""
        lines.append(f"- {d['alias']}{tag}{desc}")
    return "\n".join(lines)
