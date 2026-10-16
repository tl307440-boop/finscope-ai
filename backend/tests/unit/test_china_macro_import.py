from datetime import date

from app.services.china_macro import parse_macro_csv, public_status, series_status

HEADER = "series_code,observation_date,value,unit,source,published_at"

GOOD_ROW = "CPI_CN,2026-01-01,0.8,Percent,国家统计局,2026-02-09"


def test_parse_macro_csv_keeps_valid_rows_and_reports_bad_ones() -> None:
    text = "\n".join(
        [
            HEADER,
            GOOD_ROW,
            "CPI_CN,2026-02-01,不是数字,Percent,国家统计局,2026-03-09",
            "NOT_A_SERIES,2026-01-01,1.0,Percent,国家统计局,2026-02-09",
        ]
    )
    rows, errors = parse_macro_csv(text)
    assert [row.series_code for row in rows] == ["CPI_CN"]
    assert len(errors) == 2
    assert all("第" in item for item in errors)
    assert str(rows[0].value) == "0.8"
    assert rows[0].observation_date == date(2026, 1, 1)


def test_parse_macro_csv_rejects_missing_header() -> None:
    rows, errors = parse_macro_csv("a,b,c\n1,2,3")
    assert rows == []
    assert len(errors) == 1


def test_series_status_only_reports_stored_when_rows_exist() -> None:
    assert series_status("CPI_CN", False) == "unavailable"
    assert series_status("SOCIAL_FINANCING", False) == "unavailable"
    assert series_status("CPI_CN", True) == "stored"


def test_public_status_marks_old_data_stale_without_inventing_values() -> None:
    today = date(2026, 10, 15)
    assert (
        public_status(
            "CPI_CN",
            observation_count=0,
            latest_observation_date=None,
            today=today,
        )
        == "unavailable"
    )
    # 最新观测日距今超过 STALE_DAYS（60 天）就按 stale 处理。
    assert (
        public_status(
            "CPI_CN",
            observation_count=3,
            latest_observation_date=date(2026, 6, 1),
            today=today,
        )
        == "stale"
    )
    assert (
        public_status(
            "CPI_CN",
            observation_count=3,
            latest_observation_date=date(2026, 10, 1),
            today=today,
        )
        == "stored"
    )
