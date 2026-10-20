"""给上线语料库之前已经解析好的「我的文档」补一份助手检索用的副本，可重复运行。

    python -m app.scripts.corpus_user_backfill
    python -m app.scripts.corpus_user_backfill --root /app/data/uploads

会重新读原文件解析（解析时间预算、页数上限与上传时相同）；已经有副本的文件跳过。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings
from app.db.session import create_db_engine
from app.services.document_index import backfill_corpus_mirrors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/uploads", help="上传文件目录，默认 data/uploads")
    args = parser.parse_args(argv)
    engine = create_db_engine(get_settings().database_url)
    done, failed = backfill_corpus_mirrors(engine, Path(args.root))
    print(f"补上 {done} 份，失败 {failed} 份（原文件不在或无法解析）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
