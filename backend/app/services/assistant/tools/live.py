"""读实时行情接口的工具共用的部分：时点检查、服务取用、上游异常转成工具失败原因。

实时接口只能回答「现在」。用户要的是过去某一天、而接口给不出那一天时，必须明说，
不能把今天的数据当成当时的数据（规格：时点 `published_at <= as_of`，不用未来数据）。
"""

from datetime import date

from sqlalchemy import Engine

from app.providers.errors import ProviderError
from app.repositories.board_market import BoardMarketRepository
from app.services.industry_market import IndustryMarketService, get_service
from app.services.market_sentiment import MarketSentimentService, get_sentiment_service


def live_only_reason(as_of: date | None, today: date, what: str) -> str | None:
    """要回看过去的日期但数据只有实时时，返回原因；没有冲突返回 None。"""

    if as_of is not None and as_of < today:
        return f"{what}只有实时数据，不能回看 {as_of.isoformat()} 当天的情况"
    return None


def industry_service(engine: Engine | None) -> IndustryMarketService:
    """和页面共用同一个服务实例（共用缓存与限速）；入库快照作为实时失败时的退路。"""

    service = get_service()
    if service.store is None and engine is not None:
        service.store = BoardMarketRepository(engine)
    return service


def sentiment_service() -> MarketSentimentService:
    return get_sentiment_service()


def upstream_reason(error: ProviderError) -> str:
    """上游错误给模型和读者看的原因：只有中文说明，不带地址和密钥。"""

    return f"数据源暂时取不到：{error.message}"
