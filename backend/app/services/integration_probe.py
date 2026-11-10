"""配置项的「测试连接」：只回答通不通，不回显密钥，也不回显上游的原始报错正文。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.providers import jygs as jygs_src
from app.providers import stock_boards as sb
from app.providers.corpus_embedding import INDEXED_DIMS, EmbeddingConfig, embed_texts
from app.providers.errors import ProviderError
from app.providers.web_search import (
    BOCHA_URL,
    QIANFAN_URL,
    SearchHit,
    bocha_body,
    bocha_hits,
    brave_hits,
    brave_request,
    qianfan_body,
    qianfan_failure,
    qianfan_hits,
)

TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str


def probe(
    key: str,
    value: str,
    *,
    sec_user_agent: str,
    llm_base_url: str,
    llm_api_key: str = "",
) -> ProbeResult:
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
            return _llm(value, llm_api_key)
        if key == "LLM_MODEL":
            return _llm_model(llm_base_url, llm_api_key, value)
        if key == "CORPUS_EMBEDDING_API_KEY":
            return _zhipu_embedding(value)
        if key == "BOCHA_API_KEY":
            return _bocha(value)
        if key == "BRAVE_API_KEY":
            return _brave(value)
        if key == "BAIDU_QIANFAN_API_KEY":
            return _qianfan(value)
        if key == "JYGS_SESSION":
            return _jygs(value)
        if key == "FUYAO_API_KEY":
            return _fuyao(value)
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


def _zhipu_embedding(api_key: str) -> ProbeResult:
    """向量化两个字，费用可以忽略；同时核对返回的维度是不是索引要的 1024。"""

    config = EmbeddingConfig(
        base_url=get_settings().corpus_embedding_api_base_url.strip().rstrip("/"),
        api_key=api_key,
        model="embedding-3",
        dims=INDEXED_DIMS["embedding-3"],
    )
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            embed_texts(config, ["测试"], client=client)
        except ProviderError as exc:
            return ProbeResult(False, exc.message)
    return ProbeResult(True, "智谱接受了这个密钥，向量维度正确")


def _bocha(key: str) -> ProbeResult:
    if not key.strip():
        return ProbeResult(False, "请先填写博查密钥")
    response = httpx.post(
        BOCHA_URL,
        content=bocha_body("上证指数"),
        headers={
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
    )
    return _search_result(response, "上证指数", bocha_hits)


def _brave(key: str) -> ProbeResult:
    if not key.strip():
        return ProbeResult(False, "请先填写 Brave 密钥")
    url, headers = brave_request("federal reserve")
    response = httpx.get(
        url, headers={**headers, "X-Subscription-Token": key.strip()}, timeout=TIMEOUT
    )
    return _search_result(response, "federal reserve", brave_hits)


def _qianfan(key: str) -> ProbeResult:
    if not key.strip():
        return ProbeResult(False, "请先填写百度千帆密钥")
    response = httpx.post(
        QIANFAN_URL,
        content=qianfan_body("上证指数"),
        headers={
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
    )
    if response.status_code == 200:
        problem = qianfan_failure(response.content)
        if problem is not None:
            code = problem.removeprefix("返回错误码 ").strip()
            return ProbeResult(False, f"试查没有完成，对方返回错误码 {code}")
        return _search_result(response, "上证指数", qianfan_hits)
    return _search_result(response, "上证指数", qianfan_hits)


def _search_result(
    response: httpx.Response, query: str, parse: Callable[[bytes], tuple[SearchHit, ...]]
) -> ProbeResult:
    if response.status_code == 200:
        count = len([hit for hit in parse(response.content) if hit.url])
        if count:
            return ProbeResult(True, f"试查「{query}」，返回了 {count} 条网页")
        return ProbeResult(True, f"密钥能用，试查「{query}」没有返回网页")
    if response.status_code in {401, 403}:
        return ProbeResult(False, "密钥被拒绝，请核对是不是整段复制进来的")
    if response.status_code in {402, 429}:
        return ProbeResult(False, "余额不足，或短时间内请求太多")
    return ProbeResult(False, f"试查没有完成，对方返回 HTTP {response.status_code}")


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


def _llm_model(base_url: str, api_key: str, model: str) -> ProbeResult:
    base = base_url.strip().rstrip("/")
    name = model.strip()
    if not base:
        return ProbeResult(False, "还没有大模型接口地址，这个名称没法单独测")
    if not api_key.strip():
        return ProbeResult(False, "还没有大模型 API 密钥，这个名称没法单独测")
    if not name:
        return ProbeResult(False, "请先填写大模型名称")
    response = httpx.post(
        f"{base}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        json={
            "model": name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        },
        timeout=TIMEOUT,
    )
    shown = name if len(name) <= 40 else name[:40]
    if response.status_code == 200:
        return ProbeResult(True, f"「{shown}」这次测试调用已返回")
    if response.status_code in {401, 403}:
        return ProbeResult(False, "密钥被拒绝，模型名称还没测到")
    if response.status_code in {400, 404}:
        return ProbeResult(False, f"「{shown}」这个名称对方不认")
    return ProbeResult(False, f"测试调用没有完成，对方返回 HTTP {response.status_code}")


def _fuyao(api_key: str) -> ProbeResult:
    """拉一次「同花顺行业板块目录」：信封 code 为 0 且有数据才算密钥有效。"""

    response = httpx.get(
        sb.fuyao_catalog_url("industry"), headers=sb.fuyao_headers(api_key), timeout=TIMEOUT
    )
    if response.status_code in {401, 403}:
        return ProbeResult(False, "扶摇拒绝了这个密钥，请核对是否复制完整")
    if response.status_code != 200:
        return ProbeResult(False, f"扶摇返回 HTTP {response.status_code}")
    try:
        doc = response.json()
    except ValueError:
        return ProbeResult(False, "扶摇返回了无法解析的内容")
    failure = sb.fuyao_envelope_error(doc)
    if failure is not None:
        return ProbeResult(
            False, f"扶摇拒绝了这次请求（错误码 {failure[0]}），请核对密钥是否有效或额度是否用完"
        )
    count = len(sb.parse_fuyao_catalog(doc))
    if count:
        return ProbeResult(True, f"扶摇接受了这个密钥，行业板块目录返回 {count} 个板块")
    return ProbeResult(False, "密钥被接受，但板块目录是空的，请确认账号有该接口的权限")


def _jygs(session: str) -> ProbeResult:
    """用这条会话请求一次「异动解析条数」：能返回数据才算有效。"""

    page = httpx.get(jygs_src.PAGE_URL, headers=jygs_src.PAGE_HEADERS, timeout=TIMEOUT)
    stamp, token = jygs_src.sign(jygs_src.parse_windows(page.content), datetime.now(UTC))
    response = httpx.post(
        jygs_src.api_url("action/count-pc"),
        json={"date": datetime.now(UTC).date().isoformat()},
        headers=jygs_src.api_headers(stamp, token, session),
        timeout=TIMEOUT,
    )
    try:
        jygs_src.check_envelope(response.json())
    except ProviderError as error:
        return ProbeResult(False, error.message)
    except ValueError:
        return ProbeResult(False, "韭研公社返回了无法解析的内容")
    return ProbeResult(True, "韭研公社接受了这条登录会话")
