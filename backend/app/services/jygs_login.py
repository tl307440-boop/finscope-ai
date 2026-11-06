"""韭研公社扫码登录：后台走完微信开放平台的网页扫码流程，拿到 `SESSION` 后交给 `saver` 保存。

- 同一时间只保留一次登录尝试（再点「扫码登录」会作废上一次）。
- 状态机：waiting（等扫码）→ scanned（已扫，等在手机上确认）→ confirmed（已保存）；
  也可能 expired / cancelled / failed。`message` 只写给用户看的中文说明，不含会话值。
- 会话值只在内存里短暂经过：写入 `saver`（加密存库）后不保留，更不会写日志。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookiejar import Cookie
from typing import Literal

import httpx

from app.providers import jygs as src
from app.providers.errors import ProviderError
from app.services.ashare_calc import load_json

logger = logging.getLogger(__name__)

LoginStatus = Literal["waiting", "scanned", "confirmed", "expired", "cancelled", "failed"]
Saver = Callable[[str, datetime | None], None]
ClientFactory = Callable[[], httpx.AsyncClient]

LOGIN_TIMEOUT_SECONDS = 150.0  # 微信二维码约两分钟失效
POLL_TIMEOUT_SECONDS = 35.0
_SESSION_COOKIE = "SESSION"
_API_HOST = "web-api.jiuyangongshe.com"


@dataclass
class LoginAttempt:
    login_id: str
    qr_image: bytes
    qr_mime: str
    status: LoginStatus = "waiting"
    message: str = "请用微信扫描二维码"
    expires_at: datetime | None = None
    started: float = field(default_factory=time.monotonic)

    @property
    def done(self) -> bool:
        return self.status in {"confirmed", "expired", "cancelled", "failed"}


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0), trust_env=False, follow_redirects=False)


def pick_session_cookie(cookies: httpx.Cookies) -> Cookie | None:
    """优先取 web-api 域的 SESSION（接口认的就是它）。"""

    found: list[Cookie] = [c for c in cookies.jar if c.name == _SESSION_COOKIE and c.value]
    for cookie in found:
        if cookie.domain.lstrip(".") == _API_HOST:
            return cookie
    return found[0] if found else None


class JygsLoginManager:
    def __init__(
        self,
        *,
        client_factory: ClientFactory = _default_client,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout_seconds: float = LOGIN_TIMEOUT_SECONDS,
        poll_timeout: float = POLL_TIMEOUT_SECONDS,
        idle_sleep: float = 0.5,
    ) -> None:
        self._factory = client_factory
        self._now = now
        self._timeout = timeout_seconds
        self._poll_timeout = poll_timeout
        self._idle_sleep = idle_sleep
        self._attempt: LoginAttempt | None = None
        self._task: asyncio.Task[None] | None = None

    def status(self, login_id: str) -> LoginAttempt | None:
        attempt = self._attempt
        return attempt if attempt is not None and attempt.login_id == login_id else None

    async def start(self, saver: Saver) -> LoginAttempt:
        await self.cancel()
        async with self._factory() as client:
            stamp, token = src.sign(await self._windows(client), self._now())
            response = await client.post(
                src.api_url("user/login/wx-pc"),
                json=src.wx_login_body(),
                headers=src.api_headers(stamp, token),
            )
            qrconnect = src.check_envelope(load_json(response.content, "韭研公社登录"))
            if not isinstance(qrconnect, str) or not qrconnect.startswith("https://"):
                raise ProviderError(
                    code="INVALID_RESPONSE", message="韭研公社没有返回登录地址", status_code=502
                )
            page = await client.get(qrconnect, headers=src.WX_HEADERS)
            uuid_ = src.parse_qr_uuid(page.text)
            image = await client.get(src.qr_image_url(uuid_), headers=src.WX_HEADERS)
            if image.status_code != 200 or not image.content:
                raise ProviderError(
                    code="UPSTREAM_ERROR", message="获取微信二维码失败", status_code=502
                )
        attempt = LoginAttempt(
            login_id=uuid.uuid4().hex,
            qr_image=image.content,
            qr_mime=image.headers.get("content-type", "image/jpeg").split(";")[0],
        )
        self._attempt = attempt
        self._task = asyncio.create_task(self._run(attempt, uuid_, saver))
        return attempt

    async def cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _windows(self, client: httpx.AsyncClient) -> list[src.TokenWindow]:
        page = await client.get(src.PAGE_URL, headers=src.PAGE_HEADERS)
        return src.parse_windows(page.content)

    async def _run(self, attempt: LoginAttempt, uuid_: str, saver: Saver) -> None:
        try:
            await self._poll(attempt, uuid_, saver)
        except asyncio.CancelledError:
            if not attempt.done:
                attempt.status, attempt.message = "cancelled", "已被新的登录请求取代"
            raise
        except ProviderError as error:
            attempt.status, attempt.message = "failed", error.message
        except Exception:
            logger.exception("jygs login crashed")
            attempt.status, attempt.message = "failed", "登录过程出错，请重试"

    async def _poll(self, attempt: LoginAttempt, uuid_: str, saver: Saver) -> None:
        deadline = time.monotonic() + self._timeout
        last: int | None = None
        async with self._factory() as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(
                        src.poll_url(uuid_, last),
                        headers=src.WX_HEADERS,
                        timeout=self._poll_timeout,
                    )
                except httpx.TimeoutException:
                    continue
                code, wx_code = src.parse_poll(response.text)
                if code == 404:
                    attempt.status, attempt.message = "scanned", "已扫码，请在手机微信上点「允许」"
                    last = 404
                elif code == 405 and wx_code:
                    await self._finish(client, attempt, wx_code, saver)
                    return
                elif code == 403:
                    attempt.status, attempt.message = "cancelled", "你已取消这次登录"
                    return
                elif code == 402:
                    break
                else:
                    await asyncio.sleep(self._idle_sleep)  # 408 等待 / 不认识的返回：别空转
        attempt.status, attempt.message = "expired", "二维码已过期，请重新获取"

    async def _finish(
        self, client: httpx.AsyncClient, attempt: LoginAttempt, wx_code: str, saver: Saver
    ) -> None:
        await client.get(src.callback_url(wx_code), headers=src.PAGE_HEADERS, follow_redirects=True)
        cookie = pick_session_cookie(client.cookies)
        if cookie is None:
            raise ProviderError(
                code="UPSTREAM_ERROR", message="微信已确认，但韭研没有返回登录会话", status_code=502
            )
        stamp, token = src.sign(await self._windows(client), self._now())
        check = await client.post(
            src.api_url("action/count-pc"),
            json={"date": self._now().date().isoformat()},
            headers=src.api_headers(stamp, token, cookie.value or ""),
        )
        src.check_envelope(load_json(check.content, "韭研公社登录校验"))  # 失效会抛 ProviderError
        expires = (
            datetime.fromtimestamp(cookie.expires, UTC) if cookie.expires is not None else None
        )
        await asyncio.to_thread(saver, cookie.value or "", expires)
        attempt.expires_at = expires
        attempt.status, attempt.message = "confirmed", "登录成功，已保存会话"


_manager: JygsLoginManager | None = None


def get_login_manager() -> JygsLoginManager:
    global _manager
    if _manager is None:
        _manager = JygsLoginManager()
    return _manager
