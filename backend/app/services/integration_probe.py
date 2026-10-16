"""配置项的「测试连接」：只回答通不通，不回显密钥，也不回显上游的原始报错正文。"""

from dataclasses import dataclass

import httpx

TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str


def probe(key: str, value: str, *, sec_user_agent: str, llm_base_url: str) -> ProbeResult:
    """按配置项类型发一个最轻量的请求。value 是要测试的值（可能还没保存）。"""

    try:
        if key == "TIINGO_API_KEY":
            return _tiingo(value)
        if key == "FRED_API_KEY":
            return _fred(value)
        if key == "SEC_USER_AGENT":
            return _sec(value)
        if key == "LLM_API_KEY":
            return _llm(llm_base_url, value)
        if key == "LLM_API_BASE_URL":
            return _llm(value, "")
    except httpx.TimeoutException:
        return ProbeResult(False, "连接超时，请检查网络或稍后再试")
    except httpx.HTTPError:
        return ProbeResult(False, "无法连接到对方服务器")
    return ProbeResult(False, "这一项没有可用的连接测试")


def _tiingo(token: str) -> ProbeResult:
    response = httpx.get(
        "https://api.tiingo.com/api/test/", params={"token": token}, timeout=TIMEOUT
    )
    if response.status_code == 200:
        return ProbeResult(True, "Tiingo 接受了这个密钥")
    if response.status_code in {401, 403}:
        return ProbeResult(False, "Tiingo 拒绝了这个密钥，请核对是否复制完整")
    return ProbeResult(False, f"Tiingo 返回 HTTP {response.status_code}")


def _fred(key: str) -> ProbeResult:
    response = httpx.get(
        "https://api.stlouisfed.org/fred/series",
        params={"series_id": "GDP", "api_key": key, "file_type": "json"},
        timeout=TIMEOUT,
    )
    if response.status_code == 200:
        return ProbeResult(True, "FRED 接受了这个密钥")
    if response.status_code in {400, 401, 403}:
        return ProbeResult(False, "FRED 拒绝了这个密钥（需要 32 位小写字母数字）")
    return ProbeResult(False, f"FRED 返回 HTTP {response.status_code}")


def _sec(user_agent: str) -> ProbeResult:
    if "@" not in user_agent:
        return ProbeResult(False, "访问标识里需要带联系邮箱")
    response = httpx.get(
        "https://data.sec.gov/submissions/CIK0000320193.json",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if response.status_code == 200:
        return ProbeResult(True, "SEC 接受了这个访问标识")
    if response.status_code in {403, 429}:
        return ProbeResult(False, "SEC 暂时拒绝了请求，请换一个带真实邮箱的标识或稍后再试")
    return ProbeResult(False, f"SEC 返回 HTTP {response.status_code}")


def _llm(base_url: str, api_key: str) -> ProbeResult:
    base = base_url.strip().rstrip("/")
    if not base:
        return ProbeResult(False, "请先填写大模型接口地址")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(f"{base}/models", headers=headers, timeout=TIMEOUT)
    if response.status_code == 200:
        return ProbeResult(True, "模型接口可以访问")
    if response.status_code in {401, 403}:
        return ProbeResult(False, "模型接口拒绝了这个密钥")
    return ProbeResult(False, f"模型接口返回 HTTP {response.status_code}")
