"""手动导入维修 SOP：扫 sop_import/*.csv → 写入 sop.json。

用法：
    cd DataAgent
    # 把 Excel 另存为 CSV（字段含 问题描述，可选 错误码/引起问题类型/排查方法/解决方法 等），
    # 放到 sop_import/ 目录下，然后：
    python import_sop.py

说明：
  - 列名自动映射（兼容不同命名，见 tools/_sop_store.py 的 _ALIASES），未识别的列进 extra。
  - 按 error_code（无码则按 title）去重，重复导入会覆盖更新。
"""
import sys
from pathlib import Path

# 直接以 standalone 方式导入 _sop_store，避免触发 tools/__init__.py 的整套工具扫描
_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
sys.path.insert(0, str(_TOOLS_DIR))

from _sop_store import import_csv  # noqa: E402

_IMPORT_DIR = Path(__file__).resolve().parent / "sop_import"


def main() -> None:
    if not _IMPORT_DIR.exists():
        print(f"没有找到导入目录，请创建 {_IMPORT_DIR} 并放入 CSV 文件。")
        return
    files = sorted(_IMPORT_DIR.glob("*.csv"))
    if not files:
        print(f"导入目录 {_IMPORT_DIR} 里没有 CSV 文件。")
        return
    total_imported = total_skipped = 0
    for f in files:
        try:
            imported, skipped = import_csv(f)
            total_imported += imported
            total_skipped += skipped
            print(f"✅ {f.name}: 导入 {imported} 条，跳过 {skipped} 条")
        except Exception as e:
            print(f"❌ {f.name}: {e}")
    print(f"\n共导入 {total_imported} 条，跳过 {total_skipped} 条。")


if __name__ == "__main__":
    main()
