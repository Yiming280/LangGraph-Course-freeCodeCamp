"""供料拓扑工具：把产线"谁给谁供料"的静态关系提供给模型，用于待料/瓶颈归因。

拓扑数据文件：domain/supply_chain.json（业务人员可直接编辑，无需改代码）。

关键设计：**每次调用都重新读文件、不缓存**，改 JSON 后下一次调用即读到新值——
供料关系是数据、不是代码，改 coverage / supply_chain 只需改 JSON，本文件不用动。

工具会自动注册进 tools 包（见 tools/__init__.py 的 pkgutil 扫描），无需改 agent 脚本。
"""
import json
from pathlib import Path

from langchain_core.tools import tool

_JSON_FILE = Path(__file__).resolve().parents[1] / "domain" / "supply_chain.json"


def _load() -> dict:
    """每次调用读文件，不缓存——保证改 JSON 后无需重启即生效。"""
    try:
        return json.loads(_JSON_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": f"供料拓扑文件未找到: {_JSON_FILE}"}
    except json.JSONDecodeError as e:
        return {"error": f"供料拓扑文件解析失败（检查 JSON 格式）: {e}"}


def _build(data: dict) -> dict:
    """从 JSON 构建正查/反查索引（纯内存、每次重建，数据量很小）。"""
    coverage = data.get("coverage", {})
    flows = data.get("supply_chain", {}).get("flows", [])

    # coverage 正查：车辆 -> [工位]；反查：工位 -> [车辆]
    vehicle_to_stations: dict[str, list[str]] = {v: list(s) for v, s in coverage.items()}
    station_to_vehicles: dict[str, list[str]] = {}
    for v, stations in coverage.items():
        for s in stations:
            station_to_vehicles.setdefault(s, []).append(v)

    # flows 正查：from -> [to]；反查：to -> [from]
    from_to: dict[str, list[str]] = {}
    to_from: dict[str, list[str]] = {}
    for f in flows:
        from_to.setdefault(f["from"], []).append(f["to"])
        to_from.setdefault(f["to"], []).append(f["from"])

    # 终端工位：只作为 coverage 值出现（既不是车辆、也不在物流流里），即 CNC 工位 A~F 行。
    # 这些节点靠 coverage 反查找供料车；中转台 ZZT / 车 / 人工供料都属于物流层，走 supply_chain。
    flow_nodes = set(from_to) | set(to_from)
    coverage_keys = set(vehicle_to_stations)
    terminal_stations = {
        s for s in station_to_vehicles if s not in coverage_keys and s not in flow_nodes
    }

    return {
        "coverage": vehicle_to_stations,
        "station_to_vehicles": station_to_vehicles,
        "from_to": from_to,
        "to_from": to_from,
        "terminal_stations": terminal_stations,
    }


def _known_nodes(idx: dict) -> set[str]:
    """拓扑中出现的所有节点名（用于判断 node 是否在拓扑内）。"""
    nodes = set(idx["coverage"]) | set(idx["station_to_vehicles"])
    nodes |= set(idx["from_to"]) | set(idx["to_from"])
    return nodes


def _feeder(node: str, idx: dict) -> list[dict]:
    """谁给 node 供料：工位→供料车；车辆→上游来源。"""
    out = []
    if node in idx["station_to_vehicles"]:
        out.append({"level": "直接供料车", "nodes": idx["station_to_vehicles"][node]})
    if node in idx["to_from"]:
        out.append({"level": "上游来源", "nodes": idx["to_from"][node]})
    return out


def _served(node: str, idx: dict) -> list[dict]:
    """node 服务哪些下游：车辆→覆盖工位；中转台/AGV→下游去向。"""
    out = []
    if node in idx["coverage"]:
        out.append({"level": "覆盖工位", "nodes": idx["coverage"][node]})
    if node in idx["from_to"]:
        out.append({"level": "下游去向", "nodes": idx["from_to"][node]})
    return out


def _path(node: str, idx: dict) -> list[list[str]]:
    """从 node 沿反向边回溯到源头，返回所有到达源头的供料链（链内防环）。

    分层回溯：
    - 终端工位（CNC A~F 行）：用 coverage 反查找供料车；
    - 物流节点（车/中转台/人工供料）：用 supply_chain 反查（to -> from）。
    """
    chains: list[list[str]] = []

    def dfs(cur: str, chain: list[str]) -> None:
        upstream: list[str] = []
        if cur in idx["to_from"]:
            upstream += idx["to_from"][cur]
        if cur in idx["terminal_stations"]:
            upstream += idx["station_to_vehicles"][cur]
        upstream = [u for u in upstream if u != cur]  # 排除自环
        if not upstream:
            chains.append(chain)  # 到源头（人工供料 / 中转台）
            return
        for up in upstream:
            if up in chain:  # 环：截断并标记
                chains.append(chain + [f"{up}(环,已访问)"])
                continue
            dfs(up, chain + [up])

    dfs(node, [node])
    # 去重（保持顺序）
    unique: list[list[str]] = []
    for c in chains:
        if c not in unique:
            unique.append(c)
    return unique


@tool
def get_supply_chain(node: str = "", direction: str = "all") -> str:
    """查询产线供料拓扑（谁给谁供料），用于待料/瓶颈归因的相关性分析。

    **分析某 CNC 待料/瓶颈前务必先调用本工具**，找到它的供料 AMR 及上游链。

    Args:
        node: 要查询的节点名，如工位 "A03"、中转台 "ZZT02"、车辆 "AMR-HL-01"、
            或 "人工供料"。留空则返回整个拓扑概览。
        direction: 查询方向，可选：
            - "feeder"：谁给 node 供料（CNC→它的供料车；车→上游车辆/中转台/人工供料）
            - "served"：node 服务哪些下游（车→覆盖的工位列表；中转台/AGV→下游车辆）
            - "path"：node 回溯到源头（人工供料/中转台）的完整供料链
            - "all"（默认）：以上全部 + 相邻节点

    Returns:
        JSON 字符串，含查询结果、db_matching 提示（拓扑节点如何对应数据库字段）、
        以及 data_quality（已知数据库质量问题，分析时指出异常并建议核查）。
    """
    data = _load()
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    idx = _build(data)
    direction = (direction or "all").strip().lower()
    if direction not in ("feeder", "served", "path", "all"):
        direction = "all"

    result: dict = {
        "node": node or "(全拓扑)",
        "direction": direction,
        "db_matching": data.get("db_matching", {}),
        "data_quality": data.get("data_quality", {}),
    }

    if not node:
        result["nodes"] = data.get("nodes", {})
        result["coverage"] = {v: s for v, s in idx["coverage"].items()}
        result["flows"] = data.get("supply_chain", {}).get("flows", [])
    else:
        if node not in _known_nodes(idx):
            result["warning"] = (
                f"节点 {node!r} 不在供料拓扑内（拓扑只覆盖 HL/WL 车 + ZZT，"
                f"其余 AMR-CS/AX/HP、AGV-CS、BI/BJ/BK/AQ 等未收录）。"
            )
        if direction in ("feeder", "all"):
            result["feeder"] = _feeder(node, idx) or f"拓扑中无 {node} 的上游"
        if direction in ("served", "all"):
            result["served"] = _served(node, idx) or f"拓扑中无 {node} 的下游"
        if direction in ("path", "all"):
            result["path"] = _path(node, idx)

    return json.dumps(result, ensure_ascii=False, indent=2)
