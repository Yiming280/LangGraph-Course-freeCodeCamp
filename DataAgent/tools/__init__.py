"""工具包自动注册（skeleton 核心）。

往本目录丢一个新的工具 .py 文件（如 tools/rag.py），agent 脚本无需任何改动
即可自动识别 —— 这里会用 pkgutil 扫描包内所有模块，收集其中用 @tool 装饰的
工具（langchain BaseTool 实例）。

约定：
  - 模块文件名以下划线开头（如 _helpers.py）会被跳过，用于放辅助代码
  - 某个工具模块 import 出错只会打印告警、自动跳过，不拖垮整个服务

agent 脚本只需：from tools import ALL_TOOLS, TOOL_MAP
"""
import importlib
import logging
import pkgutil
from pathlib import Path

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_PACKAGE = __name__  # "tools"

ALL_TOOLS: list[BaseTool] = []
TOOL_MAP: dict[str, BaseTool] = {}


def _discover_tools() -> None:
    """扫描包内所有模块，收集 @tool 装饰的工具实例。"""
    pkg_path = Path(__file__).parent
    for mod_info in pkgutil.iter_modules([str(pkg_path)]):
        if mod_info.name.startswith("_"):
            continue  # 私有/辅助模块不加载

        try:
            module = importlib.import_module(f"{_PACKAGE}.{mod_info.name}")
        except Exception as e:  # 单个工具模块坏了不影响整体
            logger.warning("工具模块 tools/%s.py 加载失败，已跳过: %s", mod_info.name, e)
            continue

        for obj in vars(module).values():
            if isinstance(obj, BaseTool):
                if obj.name in TOOL_MAP:
                    logger.warning("工具名 %r 重复，已忽略 %s 中的定义", obj.name, mod_info.name)
                    continue
                TOOL_MAP[obj.name] = obj
                ALL_TOOLS.append(obj)


_discover_tools()

logger.info("已注册 %d 个工具: %s", len(ALL_TOOLS), [t.name for t in ALL_TOOLS])

# 兼容 `from tools import *` 场景
__all__ = ["ALL_TOOLS", "TOOL_MAP"]
