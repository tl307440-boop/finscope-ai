import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


@pytest.mark.integration
def test_model_status_hides_secrets_when_unconfigured() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/analysis/model-status")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["available"] is False
    assert "不会生成" in body["data"]["message"]
    raw = response.text
    assert "api_key" not in raw.lower()
    secret = get_settings().llm_api_key
    if secret:
        assert secret not in raw
