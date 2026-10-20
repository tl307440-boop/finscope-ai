from collections.abc import Iterator

import pytest
from app.core.config import reset_settings_cache
from app.main import create_app
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


def test_large_json_is_compressed_for_clients_that_accept_gzip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    reset_settings_cache()
    try:
        client = TestClient(create_app())
        plain = client.get("/openapi.json", headers={"Accept-Encoding": "identity"})
        packed = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    finally:
        reset_settings_cache()
    assert plain.status_code == packed.status_code == 200
    assert "content-encoding" not in plain.headers
    assert packed.headers["content-encoding"] == "gzip"
    assert packed.json() == plain.json()  # httpx 自动解压后内容一致


def test_event_stream_is_never_compressed() -> None:
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

    def chunks() -> Iterator[bytes]:
        for index in range(50):
            yield f"data: {'x' * 100} {index}\n\n".encode()

    @app.get("/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/event-stream")

    response = TestClient(app).get("/stream", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
