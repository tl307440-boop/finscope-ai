"""韭研公社异动解析：签名、解析与服务层的离线测试（样本形状取自 2026-11 实测，已脱敏）。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime

import httpx
import pytest
from app.providers import jygs as src
from app.providers.errors import ProviderError
from app.services import jygs
from app.services.ashare_panorama import ASharePanoramaService
from app.services.jygs_login import JygsLoginManager
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI

_KEY = "qaWVBAV88I8i4X6K"  # 网页公开的窗口密钥（前端脚本里就是明文）


def _page(windows: list[dict[str, str]]) -> bytes:
    digest = src._aes(src._DIGEST_KEY, json.dumps(windows).encode(), encrypt=True).hex()
    return f'<script>window.__NUXT__={{state:{{x:{{digest:"{digest}"}}}}}}</script>'.encode()


_AES_WINDOW = {
    "platform": "web",
    "name": "D",
    "start": "2020-01-01T00:00:00Z",
    "end": "2099-01-01T00:00:00Z",
    "algorithm": "AES",
    "key": _KEY,
}


def test_sign_roundtrip_matches_site_algorithm() -> None:
    windows = src.parse_windows(_page([_AES_WINDOW]))
    assert windows[0].algorithm == "AES" and windows[0].key == _KEY
    now = datetime(2026, 11, 6, 14, 22, 11, tzinfo=SHANGHAI)
    stamp, token = src.sign(windows, now)
    assert stamp == "2026-11-06T06:22:11Z"
    plain = src._aes(_KEY.encode(), bytes.fromhex(token), encrypt=False)
    assert plain == b"jiuyangongshe" + _KEY.encode() + stamp.encode()


def test_sign_refuses_unknown_algorithm_and_expired_window() -> None:
    hmac_window = {**_AES_WINDOW, "algorithm": "HMAC-SHA256"}
    now = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)
    with pytest.raises(ProviderError) as info:
        src.sign(src.parse_windows(_page([hmac_window])), now)
    assert info.value.code == "NOT_SUPPORTED"
    old = {**_AES_WINDOW, "end": "2021-01-01T00:00:00Z"}
    with pytest.raises(ProviderError) as info:
        src.sign(src.parse_windows(_page([old])), now)
    assert info.value.code == "UPSTREAM_CHANGED"


def test_parse_windows_rejects_garbage() -> None:
    with pytest.raises(ProviderError):
        src.parse_windows(b"<html>no digest</html>")
    with pytest.raises(ProviderError):
        src.parse_windows(b'digest:"abcd"')


def test_envelope_login_expired_is_not_401() -> None:
    with pytest.raises(ProviderError) as info:
        src.check_envelope({"msg": "登录失效", "data": {}, "errCode": "1"})
    assert info.value.code == "UPSTREAM_AUTH"
    assert info.value.status_code == 502  # 401 会让前端误踢本站登录
    assert src.check_envelope({"msg": "", "data": [1], "errCode": "0"}) == [1]


def test_api_headers_carry_session_only_when_given() -> None:
    assert "Cookie" not in src.api_headers("t", "k")
    headers = src.api_headers("t", "k", "abc")
    assert headers["Cookie"] == "SESSION=abc" and headers["platform"] == "3"


def _stock(code: str, name: str, **info: object) -> dict[str, object]:
    return {"code": code, "name": name, "article": {"action_info": info}}


_FIELD_DATA = [
    {"date": "2026-11-06", "reason": "", "action_field_id": "", "name": "简图", "count": 0},
    {
        "action_field_id": "f1",
        "name": "光伏",
        "reason": "板块催化",
        "list": [
            _stock(
                "sz002578",
                "闽发铝业",
                time="09:30:09",
                num="10天6板",
                price=501,
                shares_range=1011.0,
                expound="解析正文",
            ),
            _stock("sz002529", "海源复材", time="", num=None, price=672, shares_range=0.0),
            _stock("bj920001", "北交样本", time="09:31:00", price=0, shares_range=None),
        ],
    },
    {
        "action_field_id": "f2",
        "name": "电力",
        "reason": "",
        "list": [_stock("sz002578", "闽发铝业", time="09:40:00", expound="第二个板块")],
    },
]


def test_parse_actions_converts_units_and_skips_placeholders() -> None:
    actions = jygs.parse_actions(_FIELD_DATA)
    assert [s.name for s in actions.sectors] == ["光伏", "电力"]  # 「简图」占位被跳过
    first = actions.sectors[0].stocks
    assert (first[0].code, first[0].price, first[0].change_pct) == ("002578", 5.01, 10.11)
    assert first[0].board_label == "10天6板" and first[0].explain == "解析正文"
    # 无首封时间且涨幅为 0：当作缺失而不是平盘；价格 0 / 涨幅 None 同理
    assert first[1].change_pct is None and first[1].price == 6.72
    assert first[2].price is None and first[2].change_pct is None
    assert actions.total == 4
    # 同一股票在多个板块：保留第一次出现的板块
    assert actions.by_code()["002578"].sector == "光伏"


class _Gateway:
    def __init__(self, field_doc: object, *, expired: bool = False) -> None:
        self.field_doc = field_doc
        self.expired = expired
        self.posts: list[tuple[str, Mapping[str, str]]] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        if "jiuyangongshe.com/action" in url:
            return _page([_AES_WINDOW])
        if "kline/get" in url:
            klines = ["2026-11-05,1,1,1,1,1,1", "2026-11-06,1,1,1,1,1,1"]
            return json.dumps({"data": {"klines": klines}}).encode()
        raise ProviderError(code="NO_ROUTE", message=f"未配置 {url}", status_code=500)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        self.posts.append((url, headers))
        if self.expired:
            return json.dumps({"msg": "登录失效", "data": {}, "errCode": "1"}).encode()
        return json.dumps({"msg": "", "data": self.field_doc, "errCode": "0"}).encode()


def _service(gateway: _Gateway, session: str) -> ASharePanoramaService:
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(
        gateway=gateway,
        cache=TtlCache(),
        now=lambda: now,
        jygs_session=session,
    )


def test_service_returns_sectors_with_signed_logged_in_request() -> None:
    gateway = _Gateway(_FIELD_DATA)
    payload = asyncio.run(_service(gateway, "sess-1").jygs_actions())
    assert payload.tradeDate == "2026-11-06" and payload.totalStocks == 4
    assert payload.sectors[0].stocks[0].code == "002578.SZ"
    assert "韭研公社 异动解析" in payload.meta.sources
    url, headers = gateway.posts[0]
    assert url.endswith("/action/field")
    assert headers["Cookie"] == "SESSION=sess-1" and headers["token"] and headers["timestamp"]


def test_service_without_session_makes_no_request_and_says_so() -> None:
    gateway = _Gateway(_FIELD_DATA)
    payload = asyncio.run(_service(gateway, "").jygs_actions())
    assert payload.sectors == [] and gateway.posts == []
    assert any("扫码登录" in note for note in payload.meta.notes)


def test_service_reports_expired_login_as_unavailable() -> None:
    payload = asyncio.run(_service(_Gateway([], expired=True), "old").jygs_actions())
    assert payload.sectors == []
    assert "韭研公社异动解析" in payload.meta.unavailable
    assert "重新扫码" in payload.meta.unavailable["韭研公社异动解析"]


def test_ttl_is_long_for_past_days() -> None:
    today = date(2026, 11, 6)
    assert jygs.ttl_for(date(2026, 11, 5), today, True) > jygs.ttl_for(today, today, True)


# ───────────── 扫码登录与会话状态 ─────────────


def test_wechat_poll_and_urls() -> None:
    assert src.parse_poll("window.wx_errcode=408;window.wx_code='';") == (408, "")
    assert src.parse_poll("window.wx_errcode=405;window.wx_code='ABC123';") == (405, "ABC123")
    assert src.parse_poll("<html>oops</html>") == (408, "")  # 认不出就当「还在等」，绝不当成成功
    assert src.parse_qr_uuid('<img src="/connect/qrcode/041mbLhM10GCFa1B"/>') == "041mbLhM10GCFa1B"
    assert src.poll_url("U1").endswith("?uuid=U1")
    assert src.poll_url("U1", 404).endswith("uuid=U1&last=404")
    url = src.callback_url("CODE9")
    assert url.startswith("https://web-api.jiuyangongshe.com/jystock-app/open/wx/code?path=")
    assert url.endswith("&code=CODE9&state=state")
    with pytest.raises(ProviderError):
        src.parse_qr_uuid("<html>no qr</html>")


def _login_transport(polls: list[str], *, set_cookie: bool = True) -> httpx.MockTransport:
    remaining = list(polls)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(src.PAGE_URL):
            return httpx.Response(200, content=_page([_AES_WINDOW]))
        if url.endswith("user/login/wx-pc"):
            data = "https://open.weixin.qq.com/connect/qrconnect?appid=x"
            return httpx.Response(200, json={"msg": "", "data": data, "errCode": "0"})
        if url.startswith("https://open.weixin.qq.com/connect/qrconnect"):
            return httpx.Response(200, text='<img src="/connect/qrcode/UUID123"/>')
        if url.startswith("https://open.weixin.qq.com/connect/qrcode/"):
            return httpx.Response(200, content=b"JPEGDATA", headers={"content-type": "image/jpeg"})
        if "lp.open.weixin.qq.com" in url:
            return httpx.Response(200, text=remaining.pop(0))
        if "/open/wx/code" in url:
            headers = {"Location": "https://www.jiuyangongshe.com/?type=wx"}
            if set_cookie:
                headers["Set-Cookie"] = "SESSION=sess-123; Path=/; Max-Age=1000000"
            return httpx.Response(302, headers=headers)
        if url.startswith("https://www.jiuyangongshe.com/?type=wx"):
            return httpx.Response(200, text="ok")
        if url.endswith("action/count-pc"):
            valid = "sess-123" in request.headers.get("cookie", "")
            body = {"msg": "", "data": {}, "errCode": "0"}
            if not valid:
                body = {"msg": "登录失效", "data": {}, "errCode": "1"}
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _run_login(
    polls: list[str], *, set_cookie: bool = True
) -> tuple[str, str, list[tuple[str, datetime | None]]]:
    saved: list[tuple[str, datetime | None]] = []

    async def go() -> tuple[str, str]:
        manager = JygsLoginManager(
            client_factory=lambda: httpx.AsyncClient(
                transport=_login_transport(polls, set_cookie=set_cookie)
            ),
            timeout_seconds=5,
            idle_sleep=0.0,
        )
        attempt = await manager.start(lambda session, expires: saved.append((session, expires)))
        assert attempt.qr_image == b"JPEGDATA" and attempt.qr_mime == "image/jpeg"
        for _ in range(200):
            if attempt.done:
                break
            await asyncio.sleep(0.01)
        return attempt.status, attempt.message

    status, message = asyncio.run(go())
    return status, message, saved


def test_login_flow_saves_session_after_confirm() -> None:
    polls = [
        "window.wx_errcode=408;window.wx_code='';",
        "window.wx_errcode=404;window.wx_code='';",
        "window.wx_errcode=405;window.wx_code='WXCODE';",
    ]
    status, _, saved = _run_login(polls)
    assert status == "confirmed"
    assert len(saved) == 1 and saved[0][0] == "sess-123"
    assert saved[0][1] is not None and saved[0][1] > datetime.now(UTC)  # Max-Age 换成到期时间


def test_login_cancel_and_missing_cookie_do_not_save() -> None:
    status, _, saved = _run_login(["window.wx_errcode=403;window.wx_code='';"])
    assert status == "cancelled" and saved == []
    status, message, saved = _run_login(
        ["window.wx_errcode=405;window.wx_code='WXCODE';"], set_cookie=False
    )
    assert status == "failed" and saved == [] and "没有返回登录会话" in message
    status, _, saved = _run_login(["window.wx_errcode=402;window.wx_code='';"])
    assert status == "expired" and saved == []


def _status_service(gateway: _Gateway, session: str, expires: str = "") -> ASharePanoramaService:
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(
        gateway=gateway,
        cache=TtlCache(),
        now=lambda: now,
        jygs_session=session,
        jygs_session_expires=expires,
    )


def test_status_not_configured_ok_expiring_expired() -> None:
    assert asyncio.run(_status_service(_Gateway([]), "").jygs_status()).state == "not_configured"
    ok = asyncio.run(_status_service(_Gateway([]), "s", "2026-11-20T00:00:00+00:00").jygs_status())
    assert ok.state == "ok" and ok.daysLeft == 13
    soon = asyncio.run(
        _status_service(_Gateway([]), "s", "2026-11-08T00:00:00+00:00").jygs_status()
    )
    assert soon.state == "expiring" and soon.daysLeft == 1 and "1 天" in soon.message
    past = asyncio.run(
        _status_service(_Gateway([]), "s", "2026-11-01T00:00:00+00:00").jygs_status()
    )
    assert past.state == "expired"
    unknown_expiry = asyncio.run(_status_service(_Gateway([]), "s").jygs_status())
    assert unknown_expiry.state == "ok" and unknown_expiry.daysLeft is None


def test_status_detects_server_side_invalid_session_and_never_leaks_it() -> None:
    payload = asyncio.run(_status_service(_Gateway([], expired=True), "secret-abc").jygs_status())
    assert payload.state == "expired"
    assert "secret-abc" not in payload.model_dump_json()
