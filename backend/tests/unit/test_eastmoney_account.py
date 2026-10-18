"""东财扫码登录与自选读取：用 MockTransport 复现已验证过的协议，不访问外网。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from app.providers import eastmoney_account as acc
from app.providers.eastmoney_account import EastmoneyAccount, EastmoneyLoginExpired
from app.providers.errors import ProviderError

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32
LOGIN_HTML = (
    '<form><input name="__RequestVerificationToken" type="hidden" value="tok-123" /></form>'
)
COOKIE_HEADERS = [
    ("set-cookie", "ct=CT-VALUE; Domain=.eastmoney.com; Path=/"),
    ("set-cookie", "ut=UT-VALUE; Domain=.eastmoney.com; Path=/"),
    ("set-cookie", "pi=PI-VALUE; Domain=.eastmoney.com; Path=/"),
    ("set-cookie", "uidal=UIDALnick; Domain=.eastmoney.com; Path=/"),
    ("set-cookie", "_qct=gone; Domain=exaccount2.eastmoney.com; Path=/"),
    ("set-cookie", "tracker=ignored; Domain=.eastmoney.com; Path=/"),
]


def _account(handler: Callable[[httpx.Request], httpx.Response]) -> EastmoneyAccount:
    return EastmoneyAccount(transport=httpx.MockTransport(handler))


def _login_handler(statuses: list[httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.lower()
        if path == "/home/login4":
            return httpx.Response(200, text=LOGIN_HTML)
        if path == "/jsonapi/generateqrcode":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if path == "/jsonapi/getqrcodeloginstatus":
            # 与真实接口一致：缺令牌头或不是 XHR 请求会被防伪校验拒绝。
            assert request.headers["RequestVerificationToken"] == "tok-123"
            assert request.headers["X-Requested-With"] == "XMLHttpRequest"
            assert request.headers["domainName"] == "passport2.eastmoney.com"
            return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"unexpected {request.url}")

    return handler


def _json(body: dict[str, object], headers: list[tuple[str, str]] | None = None) -> httpx.Response:
    return httpx.Response(200, json=body, headers=headers or [])


def test_qr_flow_waits_then_returns_only_session_cookies() -> None:
    waiting = _json({"rc": False, "errorcode": 40, "error": "未获取到用户", "result": None})
    success = _json(
        {"rc": True, "errorcode": 0, "result": {"UID": "1234567890123456", "Alias": "老王"}},
        COOKIE_HEADERS,
    )

    async def run() -> None:
        account = _account(_login_handler([waiting, success]))
        login = await account.start_qr()
        try:
            assert login.png == PNG and login.token == "tok-123"
            assert (await account.poll_qr(login)).state == "waiting"
            done = await account.poll_qr(login)
            assert done.state == "success"
            assert done.uid == "1234567890123456" and done.alias == "老王"
            # 只留登录会话 cookie；扫码用的 _qct 和无关的 tracker 不带走。
            assert done.cookies == {
                "ct": "CT-VALUE",
                "ut": "UT-VALUE",
                "pi": "PI-VALUE",
                "uidal": "UIDALnick",
            }
        finally:
            await login.close()

    asyncio.run(run())


def test_qr_expired_by_upstream_and_by_local_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _json({"rc": False, "errorcode": 20, "error": "已超时"})

    async def run() -> None:
        account = _account(_login_handler([expired]))
        login = await account.start_qr()
        try:
            assert (await account.poll_qr(login)).state == "expired"
            monkeypatch.setattr(acc, "QR_TTL_SECONDS", -1)
            assert login.expired
            assert (await account.poll_qr(login)).state == "expired"
        finally:
            await login.close()

    asyncio.run(run())


def test_login_without_session_cookies_is_an_error_not_a_binding() -> None:
    odd = _json({"rc": True, "result": {"UID": "1", "Alias": "x"}}, [])

    async def run() -> None:
        account = _account(_login_handler([odd]))
        login = await account.start_qr()
        try:
            with pytest.raises(ProviderError) as caught:
                await account.poll_qr(login)
            assert caught.value.code == "EASTMONEY_LOGIN_CHANGED"
        finally:
            await login.close()

    asyncio.run(run())


def test_start_qr_reports_changed_login_page_and_bad_image() -> None:
    def no_token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html></html>")

    def not_png(request: httpx.Request) -> httpx.Response:
        if request.url.path.lower() == "/home/login4":
            return httpx.Response(200, text=LOGIN_HTML)
        return httpx.Response(200, content=b"<html>blocked</html>")

    async def run() -> None:
        for handler in (no_token, not_png):
            with pytest.raises(ProviderError) as caught:
                await _account(handler).start_qr()
            assert caught.value.code == "EASTMONEY_LOGIN_CHANGED"

    asyncio.run(run())


def test_transport_failures_become_provider_errors() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("reset")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    async def run() -> None:
        with pytest.raises(ProviderError) as disconnected:
            await _account(boom).start_qr()
        assert disconnected.value.code == "UPSTREAM_DISCONNECTED"
        with pytest.raises(ProviderError) as timeout:
            await _account(slow).fetch_groups({"ct": "a", "ut": "b"})
        assert timeout.value.code == "UPSTREAM_TIMEOUT"

    asyncio.run(run())


def _favor_handler(
    members: dict[str, httpx.Response | dict[str, object]],
    groups: dict[str, object] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    listing = groups or {
        "state": 0,
        "data": {"ginfolist": [{"gid": "11", "gname": "自选股"}, {"gid": "22", "gname": "债券"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["appkey"] == acc.APP_KEY
        # 登录态靠 cookie 带过去，不是靠 URL 参数。
        assert "CT-VALUE" in request.headers["cookie"]
        if request.url.path.endswith("/ggdefstkindexinfos"):
            return httpx.Response(200, json=listing)
        gid = request.url.params["g"]
        value = members[gid]
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    return handler


COOKIES = {"ct": "CT-VALUE", "ut": "UT-VALUE"}


def _members(*securities: str) -> dict[str, object]:
    return {"state": 0, "data": {"stkinfolist": [{"security": s} for s in securities]}}


def test_fetch_groups_converts_security_to_secid_and_dedupes() -> None:
    handler = _favor_handler(
        {
            "11": _members("1$600519$12345", "90$BK0473$9", "105$NVDA$1", "1$600519$12345"),
            "22": _members("171$US10Y$3"),
        }
    )
    groups = asyncio.run(_account(handler).fetch_groups(COOKIES))
    assert [(g.gid, g.name) for g in groups] == [("11", "自选股"), ("22", "债券")]
    assert groups[0].secids == ("1.600519", "90.BK0473", "105.NVDA")
    assert groups[1].secids == ("171.US10Y",)


def test_fetch_groups_keeps_a_failed_group_apart_instead_of_calling_it_empty() -> None:
    handler = _favor_handler(
        {
            "11": {"state": -9, "message": "系统繁忙"},
            "22": _members("1$511880$1"),
        }
    )
    groups = asyncio.run(_account(handler).fetch_groups(COOKIES))
    assert groups[0].secids is None and groups[0].error
    assert groups[1].secids == ("1.511880",)


def test_empty_group_is_empty_but_known() -> None:
    handler = _favor_handler({"11": {"state": 0, "data": {"stkinfolist": []}}, "22": _members()})
    groups = asyncio.run(_account(handler).fetch_groups(COOKIES))
    assert [g.secids for g in groups] == [(), ()]


def test_expired_login_is_distinguished_from_other_failures() -> None:
    expired = httpx.Response(200, json={"state": -2, "message": "CUToken 为空"})

    def handler(request: httpx.Request) -> httpx.Response:
        return expired

    with pytest.raises(EastmoneyLoginExpired) as caught:
        asyncio.run(_account(handler).fetch_groups(COOKIES))
    assert caught.value.code == "EASTMONEY_LOGIN_EXPIRED"

    mid = _favor_handler({"11": expired, "22": _members()})
    with pytest.raises(EastmoneyLoginExpired):
        asyncio.run(_account(mid).fetch_groups(COOKIES))


def test_unexpected_shapes_and_http_errors_are_provider_errors() -> None:
    for response in (
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["list"]),
        httpx.Response(200, json={"state": 0, "data": {"ginfolist": "oops"}}),
        httpx.Response(503, text="down"),
        httpx.Response(200, content=json.dumps({"state": 5, "message": "x"}).encode()),
    ):
        with pytest.raises(ProviderError):
            asyncio.run(_account(lambda request, r=response: r).fetch_groups(COOKIES))
