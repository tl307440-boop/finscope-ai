"""首次回填「自定义数据分析」的历史数据（默认 5 年），可中断、可重复运行。

    python -m app.scripts.backfill_custom_analysis
    python -m app.scripts.backfill_custom_analysis --years 5 --cross-section-days 200

全市场截面每个交易日要请求 11–12 页，限速下 5 年大约需要一两个小时；
已入库的交易日会跳过，所以中途停掉再跑只会接着补，不会重复抓取。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.config import get_settings
from app.db.session import create_db_engine
from app.repositories.custom_analysis import CustomAnalysisRepository
from app.services.custom_analysis_rules import WINDOW_YEARS
from app.services.custom_analysis_sync import sync_custom_analysis
from app.services.market_cache import PacedGateway
from app.utils.time import utc_now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=WINDOW_YEARS, help="回填年数，默认 5")
    parser.add_argument(
        "--cross-section-days",
        type=int,
        default=None,
        help="本次最多补多少个交易日的全市场截面；不填则一次补完",
    )
    parser.add_argument("--interval", type=float, default=0.4, help="相邻请求最小间隔（秒）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    engine = create_db_engine(get_settings().database_url)
    report = asyncio.run(
        sync_custom_analysis(
            CustomAnalysisRepository(engine),
            PacedGateway(min_interval=args.interval, timeout=30.0),
            utc_now(),
            years=args.years,
            max_cross_section_days=args.cross_section_days,
            progress=lambda message: print(message, flush=True),
        )
    )
    print("写入行数：", report.rows)
    if report.failures:
        print("未完成：", *report.failures, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
