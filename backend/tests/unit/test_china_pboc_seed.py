from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.scripts.import_china_csv import load_file
from app.services.china_macro import china_series_catalog, parse_macro_csv

SEED = Path(__file__).resolve().parents[2] / "seed_data" / "china_pboc_monthly.csv"
PBOC = {"SOCIAL_FINANCING_STOCK_CN", "ENT_MLT_LOAN_CN", "GOV_BOND_NET_CN"}


def test_seed_file_passes_the_same_validation_as_the_admin_import() -> None:
    rows, errors = load_file(SEED)
    assert errors == []
    assert len(rows) == 60
    assert {row.series_code for row in rows} == PBOC
    manual = {spec.series_code for spec in china_series_catalog() if spec.manual_only}
    assert manual >= PBOC
    for row in rows:
        assert row.observation_date.day == 1
        assert row.published_at >= row.observation_date
        assert "中国人民银行" in row.source
    keys = [(row.series_code, row.observation_date) for row in rows]
    assert len(keys) == len(set(keys))


def test_seed_values_match_the_official_report_text() -> None:
    rows, _ = load_file(SEED)

    def series(code: str) -> dict[date, Decimal]:
        return {row.observation_date: row.value for row in rows if row.series_code == code}

    # 2026 年 9 月报告：社融存量同比 7.1%；
    # 前三季度政府债券净融资 10.3 万亿、企业中长期贷款增加 6.53 万亿。
    assert series("SOCIAL_FINANCING_STOCK_CN")[date(2026, 9, 1)] == Decimal("7.1")
    gov = series("GOV_BOND_NET_CN")
    assert round(sum(v for d, v in gov.items() if d.year == 2026) / 10000, 1) == Decimal("10.3")
    loans = series("ENT_MLT_LOAN_CN")
    assert round(sum(v for d, v in loans.items() if d.year == 2026) / 10000, 2) == Decimal("6.53")
    # 净值可以为负，不被当成缺失
    assert loans[date(2025, 7, 1)] < 0
    # 公布日取官方发布日：2026 年 9 月数据 10 月 14 日公布
    published = {row.published_at for row in rows if row.observation_date == date(2026, 9, 1)}
    assert published == {date(2026, 10, 14)}


def test_dropped_series_and_bad_rows_are_rejected_not_guessed() -> None:
    text = (
        "series_code,observation_date,value,unit,source,published_at\n"
        "DR007_CN,2026-09-01,1.4,Percent,中国人民银行,2026-10-14\n"
        "OMO_7D_CN,2026-09-01,1.4,Percent,中国人民银行,2026-10-14\n"
        "GOV_BOND_NET_CN,2026-09-01,abc,亿元,中国人民银行,2026-10-14\n"
        "GOV_BOND_NET_CN,2026-09-01,100,亿元,,2026-10-14\n"
        "GOV_BOND_NET_CN,2026-09-01,100,亿元,中国人民银行,2026-08-01\n"
    )
    rows, errors = parse_macro_csv(text)
    assert rows == []
    assert len(errors) == 5


def test_missing_or_unreadable_file_reports_an_error_instead_of_raising(tmp_path: Path) -> None:
    rows, errors = load_file(tmp_path / "nope.csv")
    assert rows == []
    assert len(errors) == 1
    broken = tmp_path / "broken.csv"
    broken.write_bytes(b"\xff\xfe\x00bad")
    rows, errors = load_file(broken)
    assert rows == []
    assert errors
