"""手动导入维修 SOP 知识库。

支持两种来源：
1. Excel（knowledge/*.xlsx）—— 权威源，导入时覆盖整个 sop.json。
   合并单元格结构（一个问题下多行、每行一种「引起问题类型」）会自动按问题聚合。
2. CSV（sop_import/*.csv）—— 追加/更新（按 error_code / title 去重）。

用法：
    cd DataAgent
    python import_sop.py
"""
import sys
from pathlib import Path

# 直接以 standalone 方式导入 _sop_store，避免触发 tools/__init__.py 的整套工具扫描
_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
sys.path.insert(0, str(_TOOLS_DIR))

from _sop_store import import_csv, import_xlsx  # noqa: E402

_KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
_IMPORT_DIR = Path(__file__).resolve().parent / "sop_import"


def main() -> None:
    # 1) Excel：覆盖整个库（权威源）
    xlsx_files = sorted(_KNOWLEDGE_DIR.glob("*.xlsx")) if _KNOWLEDGE_DIR.exists() else []
    if not xlsx_files:
        print(f"knowledge/ 目录下没有 xlsx 文件，跳过 Excel 导入。")
    for f in xlsx_files:
        try:
            n = import_xlsx(f)
            print(f"✅ {f.name}: 导入 {n} 条（覆盖全库）")
        except Exception as e:
            print(f"❌ {f.name}: {e}")

    # 2) CSV：追加/更新
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
