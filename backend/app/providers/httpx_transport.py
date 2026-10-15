import httpx

from app.providers.errors import ProviderTimeout
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
        return RawResponse(
            status_code=response.status_code,
            headers={key: value for key, value in response.headers.items()},
            content=response.content,
        )
