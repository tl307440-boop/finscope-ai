"""东方财富账号：扫码登录与读取自选分组。

协议来自对东财网页前端的逆向分析（docs/research/东方财富账号绑定逆向分析.md），
并已用真实账号端到端验证：
- exaccount2 登录页取防伪令牌，GenerateQrCode 取二维码 PNG，轮询 GetQrCodeLoginStatus；
- 扫码成功后登录态在响应的 Set-Cookie 里（ct / ut / pi / uidal 等，域 .eastmoney.com），
  响应体只有 UID 与昵称；
- 带这组 cookie 调 myfavor webouter 读分组和成员。

这是东财网页自用的非公开接口，随时可能变；所以解析和错误分类都集中在这里。
cookie 等同账号登录凭证：本模块不记录、不打印任何 cookie 值。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.providers.errors import ProviderError

LOGIN_BASE = "https://exaccount2.eastmoney.com"
FAVOR_BASE = "https://myfavor.eastmoney.com/v4/webouter/"
# 东财网页内置的固定 appkey（不是用户密钥），见逆向分析文档。
APP_KEY = "e9166c7e9cdfad3aa3fd7d93b757e9b1"
USER_AGENT = "Mozilla/5.0 (FinScope AI; personal research)"
# 只保留登录会话相关的 cookie；扫码用的 _qct / _qcu 等不需要。
SESSION_COOKIES = ("ct", "ut", "pi", "uidal", "sid", "mtp", "vtpst")
# 二维码约 2 分钟失效；留一点余量，超过就让用户重新生成。
QR_TTL_SECONDS = 150

_LOGIN_REQUEST = {
    "agentPageUrl": "https://passport2.eastmoney.com/pub/LoginAgent",
    "redirectUrl": "https://quote.eastmoney.com/zixuan/?from=home",
    "callBack": "LoginCallBack",
    "redirectFunc": "PageRedirect",
    "data": {
        "domainName": "passport2.eastmoney.com",
        "deviceType": "Web",
        "productType": "UserPassport",
        "version": "0.0.1",
    },
    "type": None,
}
_TOKEN = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_PNG_MAGIC = b"\x89PNG"


class EastmoneyLoginExpired(ProviderError):
    """东财登录态失效（cookie 过期或被下线），需要用户重新扫码。"""


@dataclass
class QrLogin:
    """一次进行中的扫码登录；内部持有这次会话的 cookie jar，用完要 close。"""

    client: httpx.AsyncClient
    token: str
    png: bytes
    started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started > QR_TTL_SECONDS

    async def close(self) -> None:
        await self.client.aclose()


@dataclass(frozen=True)
class QrPoll:
    state: str  # waiting | expired | success
    uid: str = ""
    alias: str = ""
    cookies: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FavorGroup:
    gid: str
    name: str
    # 东财 secid（市场.代码）；读取失败时为 None，调用方应保持该分组原样，不要当成空分组。
    secids: tuple[str, ...] | None
    error: str = ""


class EastmoneyAccount:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._transport = transport
        self._timeout = timeout

    def _client(self, cookies: Mapping[str, str] | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT},
            cookies=dict(cookies) if cookies else None,
            follow_redirects=True,
        )

    async def start_qr(self) -> QrLogin:
        client = self._client()
        try:
            request = urllib.parse.quote(json.dumps(_LOGIN_REQUEST, separators=(",", ":")))
            page = await client.get(f"{LOGIN_BASE}/home/login4?request={request}")
            _ensure_ok(page)
            token = _TOKEN.search(page.text)
            if token is None:
                raise ProviderError(
                    code="EASTMONEY_LOGIN_CHANGED", message="东财登录页结构变了，暂时无法扫码绑定"
                )
            image = await client.get(
                f"{LOGIN_BASE}/jsonapi/GenerateQrCode", params={"rnd": str(time.time())}
            )
            _ensure_ok(image)
            if not image.content.startswith(_PNG_MAGIC):
                raise ProviderError(
                    code="EASTMONEY_LOGIN_CHANGED",
                    message="东财二维码接口返回异常，暂时无法扫码绑定",
                )
            return QrLogin(client=client, token=token.group(1), png=image.content)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise _transport_error(exc) from exc
        except ProviderError:
            await client.aclose()
            raise

    async def poll_qr(self, login: QrLogin) -> QrPoll:
        if login.expired:
            return QrPoll(state="expired")
        data = _LOGIN_REQUEST["data"]
        assert isinstance(data, dict)
        headers = {
            "RequestVerificationToken": login.token,
            "X-Requested-With": "XMLHttpRequest",
            **{key: str(value) for key, value in data.items()},
        }
        try:
            response = await login.client.post(
                f"{LOGIN_BASE}/JsonAPI/GetQrCodeLoginStatus", headers=headers
            )
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from exc
        _ensure_ok(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                code="EASTMONEY_LOGIN_CHANGED", message="东财扫码状态接口返回异常"
            ) from exc
        if not isinstance(body, dict):
            raise ProviderError(code="EASTMONEY_LOGIN_CHANGED", message="东财扫码状态接口返回异常")
        if body.get("rc"):
            result = body.get("result")
            result = result if isinstance(result, dict) else {}
            cookies = {
                cookie.name: cookie.value
                for cookie in login.client.cookies.jar
                if cookie.name in SESSION_COOKIES and cookie.value
            }
            if not all(name in cookies for name in ("ct", "ut")):
                raise ProviderError(
                    code="EASTMONEY_LOGIN_CHANGED", message="东财登录成功但没有返回登录凭证"
                )
            return QrPoll(
                state="success",
                uid=str(result.get("UID") or ""),
                alias=str(result.get("Alias") or ""),
                cookies=cookies,
            )
        if body.get("errorcode") == 20:
            return QrPoll(state="expired")
        # 40 = 还没扫；其它未知编号按「等待」处理，由二维码有效期兜底。
        return QrPoll(state="waiting")

    async def fetch_groups(self, cookies: Mapping[str, str]) -> list[FavorGroup]:
        """读取全部自选分组及成员。登录失效抛 EastmoneyLoginExpired。"""

        async with self._client(cookies) as client:
            listing = await self._favor(client, "ggdefstkindexinfos", {})
            groups_raw = (listing.get("data") or {}).get("ginfolist")
            if not isinstance(groups_raw, list):
                raise ProviderError(
                    code="EASTMONEY_FAVOR_CHANGED", message="东财自选分组接口返回异常"
                )
            groups: list[FavorGroup] = []
            for raw in groups_raw:
                if not isinstance(raw, dict) or not raw.get("gid"):
                    continue
                gid = str(raw["gid"])
                name = str(raw.get("gname") or gid)
                try:
                    members = await self._favor(client, "gstkinfos", {"g": gid})
                except EastmoneyLoginExpired:
                    raise
                except ProviderError as exc:
                    groups.append(FavorGroup(gid=gid, name=name, secids=None, error=exc.message))
                    continue
                rows = (members.get("data") or {}).get("stkinfolist") or []
                secids = tuple(dict.fromkeys(_secid(row) for row in rows if _secid(row)))
                groups.append(FavorGroup(gid=gid, name=name, secids=secids))
                await asyncio.sleep(0.2)
            return groups

    async def _favor(
        self, client: httpx.AsyncClient, path: str, params: Mapping[str, str]
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                FAVOR_BASE + path,
                params={"appkey": APP_KEY, **params},
                headers={"Referer": "https://quote.eastmoney.com/zixuan/"},
            )
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from exc
        _ensure_ok(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                code="EASTMONEY_FAVOR_CHANGED", message="东财自选接口返回异常"
            ) from exc
        if not isinstance(body, dict):
            raise ProviderError(code="EASTMONEY_FAVOR_CHANGED", message="东财自选接口返回异常")
        state = body.get("state")
        if state == 0:
            return body
        message = str(body.get("message") or "")
        if "CUToken" in message or state == -2:
            raise EastmoneyLoginExpired(
                code="EASTMONEY_LOGIN_EXPIRED",
                message="东财登录已失效，请重新扫码",
                status_code=401,
            )
        raise ProviderError(
            code="EASTMONEY_FAVOR_ERROR", message=f"东财自选接口返回 state={state}", status_code=502
        )


def _secid(row: object) -> str:
    """'1$600519$12345' -> '1.600519'（与前端脚本同样的取法）。"""

    if not isinstance(row, dict):
        return ""
    security = str(row.get("security") or "")
    head, sep, _ = security.rpartition("$")
    return head.replace("$", ".") if sep else ""


def _ensure_ok(response: httpx.Response) -> None:
    if response.status_code != 200:
        raise ProviderError(
            code="EASTMONEY_UNAVAILABLE",
            message=f"东财接口暂时不可用（HTTP {response.status_code}）",
            status_code=502,
        )


def _transport_error(exc: httpx.HTTPError) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(code="UPSTREAM_TIMEOUT", message="东财接口超时", status_code=504)
    return ProviderError(code="UPSTREAM_DISCONNECTED", message="东财接口连接中断", status_code=502)
