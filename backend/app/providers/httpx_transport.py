import httpx

from app.providers.errors import ProviderDisconnected, ProviderTimeout
from app.providers.http_client import OutboundRequest, RawResponse, Transport


class HttpxTransport(Transport):
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def send(self, request: OutboundRequest) -> RawResponse:
        try:
            response = await self.client.request(
                request.method,
                request.url,
                headers=dict(request.headers),
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout from exc
        except httpx.TransportError as exc:
            # 连接被重置、代理不发响应就断流等。这类失败是可重试的，
            # 必须在传输层转成 ProviderDisconnected，否则会冒泡成 500。
            raise ProviderDisconnected from exc
        return RawResponse(
            status_code=response.status_code,
            headers={key: value for key, value in response.headers.items()},
            content=response.content,
        )
