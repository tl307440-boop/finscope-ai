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
def test_assessment_before_any_release_does_not_invent_scores() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/macro/assessments", params={"as_of": "1776-07-04"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["regime_code"] == "insufficient_data"
    assert data["growth"]["reason"] == "insufficient_data"
    assert "value" not in data["growth"]
    assert "不是经济定论" in data["notice"]
