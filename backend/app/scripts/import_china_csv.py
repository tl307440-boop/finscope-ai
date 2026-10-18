"""把中国宏观 CSV 导入数据库（与 POST /api/v1/macro/china/observations 同一套校验与幂等写入）。

用法：python -m app.scripts.import_china_csv seed_data/china_pboc_monthly.csv

只导入文件里列出的、已登记序列的观测值；不合规行逐条打印原因，不会猜测或补齐任何数值。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine
from app.repositories.macro_china import MacroChinaRepository
from app.services.china_macro import CsvObservation, parse_macro_csv


def load_file(path: Path) -> tuple[list[CsvObservation], list[str]]:
    """读取并校验 CSV；文件不存在或无法按 UTF-8 解码时只返回错误，不抛异常。"""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        return [], [f"无法读取 {path}: {error}"]
    return parse_macro_csv(text)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法：python -m app.scripts.import_china_csv <csv 文件>")
        return 2
    rows, errors = load_file(Path(args[0]))
    for message in errors:
        print(f"跳过：{message}")
    if not rows:
        print("没有可导入的行，数据库未改动。")
        return 1
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    try:
        accepted, duplicates = MacroChinaRepository(engine).import_observations(rows)
    finally:
        engine.dispose()
    print(f"入库 {accepted} 条，重复 {len(duplicates)} 条，不合规 {len(errors)} 条。")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
