"""把美联储 RSS 和国家统计局数据发布栏目里最近的正文补进语料库。可重复运行。

    python -m app.scripts.corpus_official_backfill
    python -m app.scripts.corpus_official_backfill --source fed_speeches --limit 20

RSS 和栏目页只有最近一批，不是全历史。需要 SEC_USER_AGENT 里有真实联系邮箱。
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings
from app.db.session import create_db_engine
from app.services.official_texts import FEEDS, sync_official


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="", help="只抓一个来源，默认四个都抓")
    parser.add_argument("--limit", type=int, default=12, help="每个来源最多新写入几篇")
    args = parser.parse_args(argv)
    codes = [args.source] if args.source else list(FEEDS)
    unknown = [code for code in codes if code not in FEEDS]
    if unknown:
        parser.error("没有这个来源：" + "、".join(unknown))
    engine = create_db_engine(get_settings().database_url)
    try:
        for code in codes:
            saved = sync_official(engine, code, limit=args.limit)
            print(f"{code} 新写入 {saved} 篇")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
