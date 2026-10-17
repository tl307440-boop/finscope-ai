"""在能访问 FRED 的机器上抓取总览用的序列，推送到服务器（服务器直连 FRED 不通时用）。

    $env:FINSCOPE_URL = "http://121.199.8.235"
    $env:FINSCOPE_ADMIN_USER = "admin"
    $env:FINSCOPE_ADMIN_PASSWORD = "..."
    python -m app.scripts.push_fred_snapshots [--only UNRATE,CPIAUCSL] [--dry-run]

数据全部来自 FRED 公开 CSV，不生成任何数字；服务器端只接受总览使用的序列代码，
推送是幂等的（同一序列整体覆盖）。账号必须是管理员。
"""

import argparse
import os
import sys
from datetime import date, timedelta
from typing import Any

import httpx

from app.providers.fred_public import graph_csv_url, parse_graph_csv, public_realtime_start
from app.services.cockpit import _FRED_HEADERS, _FRED_LOOKBACK_DAYS, FRED_SERIES
from app.utils.time import utc_now


def fetch_series(client: httpx.Client, code: str, today: date) -> list[list[str]]:
    url = graph_csv_url(
        series_id=code,
        observation_start=today - timedelta(days=_FRED_LOOKBACK_DAYS),
        observation_end=today,
    )
    response = client.get(url, headers=_FRED_HEADERS)
    response.raise_for_status()
    rows = parse_graph_csv(
        response.content, series_id=code, realtime_start=public_realtime_start(utc_now())
    )
    return [[row.observation_date.isoformat(), str(row.value)] for row in rows]


def login(client: httpx.Client, base_url: str, username: str, password: str) -> str:
    response = client.post(
        f"{base_url}/api/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    token: Any = response.json()["data"]["access_token"]
    return str(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="只推送这些序列代码，逗号分隔")
    parser.add_argument("--dry-run", action="store_true", help="只抓取，不推送")
    args = parser.parse_args(argv)

    wanted = [code.strip() for code in args.only.split(",") if code.strip()] or list(FRED_SERIES)
    unknown = sorted(set(wanted) - set(FRED_SERIES))
    if unknown:
        print(f"不是总览使用的序列：{', '.join(unknown)}", file=sys.stderr)
        return 2

    today = date.today()
    series: dict[str, list[list[str]]] = {}
    with httpx.Client(timeout=30.0) as client:
        for code in wanted:
            try:
                series[code] = fetch_series(client, code, today)
            except (httpx.HTTPError, ValueError) as error:
                print(f"  {code}: 抓取失败（{error}）", file=sys.stderr)
                continue
            print(f"  {code}: {len(series[code])} 个观测，最新 {series[code][-1][0]}")

        if not series:
            print("没有抓到任何序列，不推送。", file=sys.stderr)
            return 1
        if args.dry_run:
            print("dry-run：不推送。")
            return 0

        base_url = os.environ.get("FINSCOPE_URL", "").rstrip("/")
        username = os.environ.get("FINSCOPE_ADMIN_USER", "")
        password = os.environ.get("FINSCOPE_ADMIN_PASSWORD", "")
        if not (base_url and username and password):
            print(
                "请设置 FINSCOPE_URL / FINSCOPE_ADMIN_USER / FINSCOPE_ADMIN_PASSWORD。",
                file=sys.stderr,
            )
            return 2
        token = login(client, base_url, username, password)
        response = client.post(
            f"{base_url}/api/v1/admin/dashboard-fred-snapshots",
            json={"series": series},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.is_error:
            print(f"推送失败：HTTP {response.status_code} {response.text[:300]}", file=sys.stderr)
            return 1
        data = response.json()["data"]
        print(f"已推送 {len(data['series'])} 个序列，共 {data['points']} 个观测。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
