"""所有语料抓取共用的客户端（规格 §11）。

每台主机最多 1 次/秒，响应不超过 5 MB，重定向最多 3 次。目标主机必须在来源白名单里，
解析出的地址不能是私网、回环或链路本地。403 和 429 立刻停止，不重试、不换身份。

新接入的来源用 `get(url, respect_robots=True)`：先读对方的 robots.txt，不允许就不抓。
robots.txt 读不到时按保守处理：401/403 和 5xx、网络失败都当作「全部不许抓」，
其余 4xx（多半是没有这个文件）当作不限制。
"""

import ipaddress
import re
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.providers.robots import RobotsRules

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
MIN_INTERVAL_SECONDS = 1.0
ROBOTS_TTL_SECONDS = 3600.0
ROBOTS_FAILURE_TTL_SECONDS = 300.0
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net"}


class PoliteError(Exception):
    """抓取被拒绝或失败。`code` 为 BLOCKED 时调用方应停止这一来源，不要再试。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Exchange:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


Opener = Callable[[str, str, Mapping[str, str], bytes | None], Exchange]
Resolver = Callable[[str], tuple[str, ...]]
Sleeper = Callable[[float], None]


def contact_email(user_agent: str) -> str | None:
    """从 `SEC_USER_AGENT` 里取出邮箱。空的、或仍是 example.com 这类占位域名，返回 None。

    没有真实联系方式就不抓：对方要能联系到我们。
    """

    found = _EMAIL.search(user_agent)
    if found is None:
        return None
    domain = found.group(1).lower().rstrip(".")
    if domain in _PLACEHOLDER_DOMAINS or domain.endswith(".example"):
        return None
    return found.group(0)


def user_agent_for(email: str) -> str:
    return f"FinScopeAI/1.0 (+{email})"


def address_is_blocked(value: str) -> bool:
    """私网、回环、链路本地、保留地址和多播都不能作为抓取目标。"""

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def resolve_public(host: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise PoliteError("DNS", f"解析不了 {host}") from exc
    found = tuple(dict.fromkeys(str(item[4][0]) for item in infos))
    if not found:
        raise PoliteError("DNS", f"解析不了 {host}")
    return found


@dataclass(frozen=True)
class _RobotsEntry:
    expires_at: float
    rules: RobotsRules | None  # None 表示全部不许抓


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str,
        allowed_hosts: frozenset[str],
        opener: Opener,
        resolve: Resolver = resolve_public,
        sleeper: Sleeper = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_bytes: int = MAX_BYTES,
        min_interval: float = MIN_INTERVAL_SECONDS,
        allowed_suffixes: frozenset[str] = frozenset(),
    ) -> None:
        self._user_agent = user_agent
        self._allowed = frozenset(host.lower().rstrip(".") for host in allowed_hosts)
        # 域名后缀白名单：`gov.cn` 同时放行 `gov.cn` 和它的所有子域名。
        self._suffixes = frozenset(item.lower().strip(".") for item in allowed_suffixes)
        self._opener = opener
        self._resolve = resolve
        self._sleeper = sleeper
        self._clock = clock
        self._max_bytes = max_bytes
        self._min_interval = min_interval
        self._next_at: dict[str, float] = {}
        self._robots: dict[str, _RobotsEntry] = {}

    def get(self, url: str, *, respect_robots: bool = False) -> bytes:
        if respect_robots and not self.robots_allowed(url):
            host = urlsplit(url).hostname or ""
            raise PoliteError("ROBOTS", f"{host} 的 robots.txt 不允许抓取这个地址")
        return self._fetch("GET", url, None)

    def post(self, url: str, content: bytes, content_type: str) -> bytes:
        return self._fetch("POST", url, content, content_type)

    def robots_allowed(self, url: str) -> bool:
        """对方的 robots.txt 是否允许我们抓这个地址；每个主机的结果缓存一小时。"""

        host = self._check(url)
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{host}"
        entry = self._robots.get(origin)
        if entry is None or entry.expires_at <= self._clock():
            entry = self._load_robots(origin)
            self._robots[origin] = entry
        if entry.rules is None:
            return False
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return entry.rules.allows(self._robots_agent(), path)

    def _robots_agent(self) -> str:
        return self._user_agent.split("/", 1)[0].strip().split(" ", 1)[0] or "*"

    def _load_robots(self, origin: str) -> _RobotsEntry:
        try:
            response = self._send("GET", f"{origin}/robots.txt", None, None)
        except PoliteError as exc:
            if exc.code in {"HOST", "SSRF", "URL"}:
                raise
            return self._robots_entry(None, ROBOTS_FAILURE_TTL_SECONDS)
        status = response.status_code
        if status == 200:
            text = response.content.decode("utf-8", errors="replace")
            return self._robots_entry(RobotsRules.parse(text), ROBOTS_TTL_SECONDS)
        if status in {401, 403} or status >= 500:
            return self._robots_entry(None, ROBOTS_FAILURE_TTL_SECONDS)
        return self._robots_entry(RobotsRules([]), ROBOTS_TTL_SECONDS)

    def _robots_entry(self, rules: RobotsRules | None, ttl: float) -> _RobotsEntry:
        return _RobotsEntry(expires_at=self._clock() + ttl, rules=rules)

    def _fetch(
        self, method: str, url: str, content: bytes | None, content_type: str | None = None
    ) -> bytes:
        response = self._send(method, url, content, content_type)
        if response.status_code in {403, 429}:
            raise PoliteError("BLOCKED", f"上游返回 {response.status_code}，停止这一来源")
        if response.status_code != 200:
            raise PoliteError("UPSTREAM", f"上游返回 {response.status_code}")
        if len(response.content) > self._max_bytes:
            raise PoliteError("TOO_LARGE", "响应超过 5 MB")
        return response.content

    def _send(
        self, method: str, url: str, content: bytes | None, content_type: str | None
    ) -> Exchange:
        """发请求并跟随重定向（每一跳都重新检查主机），返回最后一跳的响应，不看状态码。"""

        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            host = self._check(current)
            self._pace(host)
            headers = {"User-Agent": self._user_agent, "Accept": "*/*"}
            if content_type is not None:
                headers["Content-Type"] = content_type
            try:
                response = self._opener(method, current, headers, content)
            except PoliteError:
                raise
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise PoliteError("TIMEOUT", "上游超时") from exc
            except httpx.HTTPError as exc:
                raise PoliteError("UPSTREAM", "上游连接失败") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location") or response.headers.get("Location")
                if not location:
                    raise PoliteError("REDIRECT", "重定向没有目标地址")
                current = urljoin(current, location)
                method, content, content_type = "GET", None, None
                continue
            return response
        raise PoliteError("REDIRECT", "重定向次数过多")

    def _check(self, url: str) -> str:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme not in {"https", "http"} or not host or parts.username or parts.password:
            raise PoliteError("URL", "地址不合法")
        if parts.port not in {None, 80, 443}:
            raise PoliteError("URL", "只访问 80 和 443 端口")
        if host not in self._allowed and not self._suffix_allowed(host):
            raise PoliteError("HOST", f"{host} 不在这个来源的白名单里")
        literal = _literal_ip(host)
        addresses = (literal,) if literal is not None else self._resolve(host)
        if any(address_is_blocked(item) for item in addresses):
            raise PoliteError("SSRF", f"{host} 解析到了不能访问的地址")
        return host

    def _suffix_allowed(self, host: str) -> bool:
        return any(host == suffix or host.endswith("." + suffix) for suffix in self._suffixes)

    def _pace(self, host: str) -> None:
        wait = self._next_at.get(host, 0) - self._clock()
        if wait > 0:
            self._sleeper(wait)
        self._next_at[host] = self._clock() + self._min_interval


def _literal_ip(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def httpx_opener(client: httpx.Client, max_bytes: int = MAX_BYTES) -> Opener:
    """用已建好的 httpx 客户端读响应，自己截断到 `max_bytes`，不跟随重定向。"""

    def open(method: str, url: str, headers: Mapping[str, str], content: bytes | None) -> Exchange:
        with client.stream(method, url, headers=dict(headers), content=content) as response:
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PoliteError("TOO_LARGE", "响应超过 5 MB")
                chunks.append(chunk)
            return Exchange(response.status_code, dict(response.headers), b"".join(chunks))

    return open


def live_client(
    email: str,
    allowed_hosts: frozenset[str],
    timeout: float = 20,
    *,
    allowed_suffixes: frozenset[str] = frozenset(),
) -> PoliteClient:
    raw = httpx.Client(timeout=timeout, follow_redirects=False)
    return PoliteClient(
        user_agent=user_agent_for(email),
        allowed_hosts=allowed_hosts,
        opener=httpx_opener(raw),
        allowed_suffixes=allowed_suffixes,
    )
