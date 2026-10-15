from app.scripts.seed import run
from app.services.heartbeat_rules import ProbeResult
from pytest import CaptureFixture


class StaticProbe:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result

    def check(self) -> ProbeResult:
        return self.result


def test_seed_refuses_to_invent_data_when_database_is_down(capsys: CaptureFixture[str]) -> None:
    code = run(StaticProbe(ProbeResult(status="down", reason="database_unavailable")))
    assert code == 1
    assert "未写入" in capsys.readouterr().out


def test_seed_does_not_insert_market_fixtures(capsys: CaptureFixture[str]) -> None:
    code = run(StaticProbe(ProbeResult(status="up", latency_ms=1)))
    captured = capsys.readouterr().out
    assert code == 0
    assert "不写入" in captured
    assert "600519" not in captured
    assert "演示行情已导入" not in captured
