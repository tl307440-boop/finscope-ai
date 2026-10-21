import pytest
from app.providers.polite import (
    Exchange,
    PoliteClient,
    PoliteError,
    address_is_blocked,
    contact_email,
    user_agent_for,
)


class _Script:
    def __init__(self, responses: list[Exchange]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, method: str, url: str, headers: object, content: object) -> Exchange:
        self.calls.append(url)
        return self.responses.pop(0)


def _client(script: _Script, **kwargs: object) -> PoliteClient:
    return PoliteClient(
        user_agent=user_agent_for("ops@finscope.test"),
        allowed_hosts=frozenset({"www.cninfo.com.cn", "static.cninfo.com.cn"}),
        opener=script,
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=lambda _seconds: None,
        clock=lambda: 0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_contact_email_rejects_placeholders() -> None:
    assert contact_email("FinScopeAI research-admin@example.com") is None
    assert contact_email("FinScopeAI") is None
    assert contact_email("FinScopeAI ops@finscope.test") == "ops@finscope.test"


def test_private_and_loopback_addresses_are_blocked() -> None:
    assert address_is_blocked("127.0.0.1")
    assert address_is_blocked("10.0.0.8")
    assert address_is_blocked("192.168.1.1")
    assert address_is_blocked("169.254.1.1")
    assert address_is_blocked("::1")
    assert not address_is_blocked("1.1.1.1")


def test_host_outside_the_allowlist_is_refused_before_any_request() -> None:
    script = _Script([])
    client = _client(script)
    with pytest.raises(PoliteError) as caught:
        client.get("https://evil.example/file.pdf")
    assert caught.value.code == "HOST"
    assert script.calls == []


def test_literal_private_ip_is_refused_even_if_the_name_is_allowed() -> None:
    script = _Script([])
    client = PoliteClient(
        user_agent="x",
        allowed_hosts=frozenset({"127.0.0.1"}),
        opener=script,
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=lambda _seconds: None,
        clock=lambda: 0,
    )
    with pytest.raises(PoliteError) as caught:
        client.get("https://127.0.0.1/file.pdf")
    assert caught.value.code == "SSRF"
    assert script.calls == []


def test_redirect_to_another_host_is_checked_again() -> None:
    script = _Script(
        [Exchange(302, {"location": "https://evil.example/secret"}, b""), Exchange(200, {}, b"no")]
    )
    client = _client(script)
    with pytest.raises(PoliteError) as caught:
        client.get("https://www.cninfo.com.cn/go")
    assert caught.value.code == "HOST"
    assert script.calls == ["https://www.cninfo.com.cn/go"]


def test_forbidden_stops_without_a_second_request() -> None:
    script = _Script([Exchange(403, {}, b"no")])
    client = _client(script)
    with pytest.raises(PoliteError) as caught:
        client.get("https://static.cninfo.com.cn/a.pdf")
    assert caught.value.code == "BLOCKED"
    assert len(script.calls) == 1


def test_same_host_waits_between_requests() -> None:
    slept: list[float] = []
    now = {"t": 0.0}

    def clock() -> float:
        return now["t"]

    def sleeper(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    script = _Script([Exchange(200, {}, b"a"), Exchange(200, {}, b"b")])
    client = PoliteClient(
        user_agent="x",
        allowed_hosts=frozenset({"static.cninfo.com.cn"}),
        opener=script,
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=sleeper,
        clock=clock,
        min_interval=1,
    )
    assert client.get("https://static.cninfo.com.cn/a") == b"a"
    assert client.get("https://static.cninfo.com.cn/b") == b"b"
    assert slept == [1]


def _robots_client(script: _Script, now: dict[str, float] | None = None) -> PoliteClient:
    clock = (lambda: now["t"]) if now is not None else (lambda: 0.0)
    return PoliteClient(
        user_agent=user_agent_for("ops@finscope.test"),
        allowed_hosts=frozenset({"www.example.gov.cn"}),
        opener=script,
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=lambda _seconds: None,
        clock=clock,
    )


def test_robots_disallow_stops_the_page_request() -> None:
    robots = b"User-agent: *\nDisallow: /private/\n"
    script = _Script([Exchange(200, {}, robots)])
    client = _robots_client(script)
    with pytest.raises(PoliteError) as caught:
        client.get("https://www.example.gov.cn/private/a.html", respect_robots=True)
    assert caught.value.code == "ROBOTS"
    assert script.calls == ["https://www.example.gov.cn/robots.txt"]


def test_robots_allow_fetches_the_page_and_caches_the_rules() -> None:
    robots = b"User-agent: *\nDisallow: /private/\n"
    script = _Script([Exchange(200, {}, robots), Exchange(200, {}, b"a"), Exchange(200, {}, b"b")])
    client = _robots_client(script)
    assert client.get("https://www.example.gov.cn/news/1", respect_robots=True) == b"a"
    assert client.get("https://www.example.gov.cn/news/2", respect_robots=True) == b"b"
    assert script.calls.count("https://www.example.gov.cn/robots.txt") == 1


def test_robots_cache_expires_after_an_hour() -> None:
    now = {"t": 0.0}
    robots = b"User-agent: *\nDisallow:\n"
    script = _Script([Exchange(200, {}, robots), Exchange(200, {}, robots)])
    client = _robots_client(script, now)
    assert client.robots_allowed("https://www.example.gov.cn/a")
    now["t"] = 3601.0
    assert client.robots_allowed("https://www.example.gov.cn/a")
    assert len(script.calls) == 2


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_unreadable_robots_means_do_not_fetch(status: int) -> None:
    script = _Script([Exchange(status, {}, b"")])
    client = _robots_client(script)
    assert not client.robots_allowed("https://www.example.gov.cn/a")


def test_missing_robots_file_means_no_restriction() -> None:
    script = _Script([Exchange(404, {}, b"not found")])
    client = _robots_client(script)
    assert client.robots_allowed("https://www.example.gov.cn/a")


def test_robots_network_failure_means_do_not_fetch() -> None:
    class Failing:
        def __call__(self, method: str, url: str, headers: object, content: object) -> Exchange:
            raise TimeoutError

    client = PoliteClient(
        user_agent="FinScopeAI/1.0",
        allowed_hosts=frozenset({"www.example.gov.cn"}),
        opener=Failing(),
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    assert not client.robots_allowed("https://www.example.gov.cn/a")


def test_domain_suffix_allows_subdomains_but_not_look_alikes() -> None:
    script = _Script([Exchange(200, {}, b"ok")])
    client = PoliteClient(
        user_agent="x",
        allowed_hosts=frozenset(),
        allowed_suffixes=frozenset({"gov.cn"}),
        opener=script,
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=lambda _seconds: None,
        clock=lambda: 0,
    )
    assert client.get("https://www.csrc.gov.cn/a") == b"ok"
    for url in ("https://evilgov.cn/a", "https://gov.cn.evil.example/a"):
        with pytest.raises(PoliteError) as caught:
            client.get(url)
        assert caught.value.code == "HOST"


def test_userinfo_and_odd_ports_are_refused() -> None:
    script = _Script([])
    client = _client(script)
    with pytest.raises(PoliteError):
        client.get("https://user:pass@www.cninfo.com.cn/a")
    with pytest.raises(PoliteError):
        client.get("https://www.cninfo.com.cn:8443/a")
    assert script.calls == []
