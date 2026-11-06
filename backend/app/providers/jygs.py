"""韭研公社（jiuyangongshe.com）异动解析：只构造请求 / 签名 / 解析，不发请求。

逆向结论（2026-11 实测，网页端 Nuxt 前端）：
- 接口：`POST https://web-api.jiuyangongshe.com/jystock-app/api/v1/<path>`，JSON 体，
  必带请求头 `platform: 3`、`timestamp`（UTC 秒级 ISO，如 `2026-11-06T06:22:11Z`）与 `token`。
- token 怎么来：网页源码里 `window.__NUXT__` 带一段十六进制 `digest`，用固定密钥
  `jiuyangongsheweb`（AES-128-CBC，iv 与密钥相同，PKCS7）解开是一个 JSON 数组，
  每项是 `{start, end, algorithm, key}` 的时间窗；取当前时间落在的那一项。
  `algorithm == "AES"` 时：token = AES-128-CBC(key=窗口密钥, iv=窗口密钥,
  明文 = "jiuyangongshe" + 窗口密钥 + timestamp).hex()。
  其它算法（页面里还有 HMAC / MD5 窗口）没抓到样本，不猜，直接报「暂不支持」。
- 异动解析（涨停原因）三个接口 `action/count-pc`、`action/field`、`action/list` 需要登录：
  请求要带浏览器里 `web-api.jiuyangongshe.com` 域的 `SESSION` Cookie（扫码登录后获得，
  有效期约两周），否则返回 `{"msg":"登录失效","errCode":"1"}`。
  `SESSION` 属于用户个人登录态，只能放本机 `.env` 的 `JYGS_SESSION`，不得入库、不得进日志。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.providers.errors import ProviderError

PAGE_URL = "https://www.jiuyangongshe.com/action"
API_BASE = "https://web-api.jiuyangongshe.com/jystock-app/api/v1/"
PAGE_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
}
_API_HEADERS: Mapping[str, str] = {
    **PAGE_HEADERS,
    "Origin": "https://www.jiuyangongshe.com",
    "Referer": "https://www.jiuyangongshe.com/",
    "platform": "3",
}

_DIGEST_KEY = b"jiuyangongsheweb"
_DIGEST = re.compile(r'digest:"([0-9a-fA-F]+)"')
_TOKEN_PREFIX = b"jiuyangongshe"
LOGIN_EXPIRED = "韭研公社登录态已失效，请重新扫码登录后更新 JYGS_SESSION"
NOT_CONFIGURED = "未配置 JYGS_SESSION（异动解析需要扫码登录韭研公社）"


@dataclass(frozen=True)
class TokenWindow:
    start: str
    end: str
    algorithm: str
    key: str


def _aes(key: bytes, data: bytes, *, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    if encrypt:
        padder = padding.PKCS7(128).padder()
        enc = cipher.encryptor()
        return enc.update(padder.update(data) + padder.finalize()) + enc.finalize()
    dec = cipher.decryptor()
    raw = dec.update(data) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(raw) + unpadder.finalize()


def parse_windows(page: bytes) -> list[TokenWindow]:
    """从页面源码取出并解开 token 时间窗表。"""

    match = _DIGEST.search(page.decode("utf-8", errors="ignore"))
    if match is None:
        raise ProviderError(
            code="INVALID_RESPONSE", message="韭研公社页面里没有找到签名参数", status_code=502
        )
    try:
        rows = json.loads(_aes(_DIGEST_KEY, bytes.fromhex(match.group(1)), encrypt=False))
        return [
            TokenWindow(
                start=str(row["start"]),
                end=str(row["end"]),
                algorithm=str(row["algorithm"]),
                key=str(row["key"]),
            )
            for row in rows
        ]
    except (ValueError, KeyError, TypeError) as error:
        raise ProviderError(
            code="INVALID_RESPONSE", message="韭研公社签名参数解析失败", status_code=502
        ) from error


def timestamp_of(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sign(windows: list[TokenWindow], now: datetime) -> tuple[str, str]:
    """返回 (timestamp, token)。当前窗口不是 AES 时明确报错，不瞎猜。"""

    stamp = timestamp_of(now)
    window = next((w for w in windows if w.start <= stamp <= w.end), None)
    if window is None:
        raise ProviderError(
            code="UPSTREAM_CHANGED", message="韭研公社签名窗口已过期", status_code=502
        )
    if window.algorithm != "AES":
        raise ProviderError(
            code="NOT_SUPPORTED",
            message=f"韭研公社当前签名算法为 {window.algorithm}，暂不支持",
            status_code=502,
        )
    key = window.key.encode()
    token = _aes(key, _TOKEN_PREFIX + key + stamp.encode(), encrypt=True).hex()
    return stamp, token


# ───────────── 微信扫码登录（开放平台标准网页扫码流程，网页端同款） ─────────────
#
# 1. `user/login/wx-pc`（匿名，带签名头）返回微信 qrconnect 页地址；
# 2. 该页 HTML 里有本次二维码的 uuid，二维码图在 `open.weixin.qq.com/connect/qrcode/<uuid>`；
# 3. 长轮询 `lp.open.weixin.qq.com/connect/l/qrconnect?uuid=`：
#    408 还没扫（再轮询）、404 已扫待确认（带 &last=404 再轮询）、405 已确认（带 wx_code）、
#    402 过期、403 取消；
# 4. 405 后访问 `web-api.../open/wx/code?path=...&code=<wx_code>&state=state`，
#    响应链路里会种下 `web-api.jiuyangongshe.com` 域的 `SESSION` Cookie。

WX_PATH = "https://www.jiuyangongshe.com/?type=wx"
_UUID = re.compile(r"/connect/qrcode/([A-Za-z0-9_\-]+)")
_POLL = re.compile(r"wx_errcode\s*=\s*(\d+)\s*;\s*window\.wx_code\s*=\s*'([^']*)'")
WX_HEADERS: Mapping[str, str] = {**PAGE_HEADERS, "Referer": "https://open.weixin.qq.com/"}


def wx_login_body() -> dict[str, str]:
    return {"path": WX_PATH}


def parse_qr_uuid(page_html: str) -> str:
    match = _UUID.search(page_html)
    if match is None:
        raise ProviderError(
            code="INVALID_RESPONSE", message="微信登录页里没有找到二维码", status_code=502
        )
    return match.group(1)


def qr_image_url(uuid: str) -> str:
    return f"https://open.weixin.qq.com/connect/qrcode/{uuid}"


def poll_url(uuid: str, last: int | None = None) -> str:
    suffix = f"&last={last}" if last else ""
    return f"https://lp.open.weixin.qq.com/connect/l/qrconnect?uuid={uuid}{suffix}"


def parse_poll(text: str) -> tuple[int, str]:
    """返回 (wx_errcode, wx_code)。格式不认识时按「还在等」处理，不当成登录成功。"""

    match = _POLL.search(text)
    if match is None:
        return 408, ""
    return int(match.group(1)), match.group(2)


def callback_url(wx_code: str) -> str:
    return (
        f"{API_BASE.removesuffix('api/v1/')}open/wx/code?path={WX_PATH}&code={wx_code}&state=state"
    )


def api_url(path: str) -> str:
    return f"{API_BASE}{path}"


def api_headers(stamp: str, token: str, session: str = "") -> dict[str, str]:
    headers = {**_API_HEADERS, "timestamp": stamp, "token": token}
    if session:
        headers["Cookie"] = f"SESSION={session}"
    return headers


def field_body(day: date) -> bytes:
    return json.dumps({"date": day.isoformat(), "pc": 1}).encode()


def check_envelope(doc: Any) -> Any:
    """校验 `{"msg","data","errCode"}` 信封，返回 data。"""

    if not isinstance(doc, dict):
        raise ProviderError(
            code="INVALID_RESPONSE", message="韭研公社返回格式异常", status_code=502
        )
    if str(doc.get("errCode", "0")) != "0":
        message = str(doc.get("msg") or "")
        if "登录" in message:
            # 不用 401：前端遇到 401 会当成本站登录过期而踢人下线。
            raise ProviderError(code="UPSTREAM_AUTH", message=LOGIN_EXPIRED, status_code=502)
        raise ProviderError(
            code="UPSTREAM_ERROR", message=f"韭研公社返回错误：{message or '未知'}", status_code=502
        )
    return doc.get("data")
