"""推演编排入口：跑一次并落库、事后结算、网格回测实验。"""

import time as clock
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from app.core.exceptions import AppError
from app.repositories.simulations import Evaluation, SimulationRepository, StoredRun
from app.trading_agents.bridge import Bar, DataStore, FinScopeDataBridge, Subject
from app.trading_agents.memory import reflection_for, settle
from app.trading_agents.pipeline import (
    DISPLAY,
    PipelineConfig,
    direction,
    rating_for,
    run_pipeline,
    to_simulation_result,
)
from app.trading_agents.pipeline import (
    MemoryNote as Note,
)
from sqlalchemy import Engine

MAX_MEMORY = 5
MAX_GRID_RUNS = 240


def resolve(store: DataStore, symbol: str, as_of: date) -> tuple[FinScopeDataBridge, Subject]:
    bridge = FinScopeDataBridge(store, as_of)
    subject = bridge.subject(symbol)
    if subject is None:
        raise AppError(
            code="NOT_FOUND", message=f"标的 {symbol} 不在已收录的证券列表里", status_code=404
        )
    return bridge, subject


def run_and_save(
    engine: Engine,
    *,
    user_id: UUID,
    symbol: str,
    as_of: date,
    config: PipelineConfig,
) -> StoredRun:
    started = clock.perf_counter()
    store = DataStore(engine)
    bridge, subject = resolve(store, symbol, as_of)
    repository = SimulationRepository(engine)
    memory = recall(repository, bridge, subject, user_id) if config.use_memory else []
    output = run_pipeline(bridge, subject, config, memory)
    result = to_simulation_result(output)
    return repository.save(
        user_id=user_id,
        symbol=subject.symbol,
        as_of=as_of,
        result=result,
        rounds=config.max_debate_rounds,
        duration_ms=int((clock.perf_counter() - started) * 1000),
    )


def recall(
    repository: SimulationRepository,
    bridge: FinScopeDataBridge,
    subject: Subject,
    user_id: UUID,
) -> list[Note]:
    """读取 as_of 之前、持有期已走完的同标的决策，结算后作为组合经理的记忆。"""

    notes: list[Note] = []
    bars = bridge.store.bars(subject.instrument_id)
    benchmark_symbol, benchmark = _full_benchmark(bridge, subject)
    for past in repository.past_decisions(user_id, subject.symbol, bridge.as_of):
        if len(notes) >= MAX_MEMORY:
            break
        if past.as_of + timedelta(days=past.holding_days) > bridge.as_of:
            continue
        cached = repository.settled(past.decision_id)
        if cached is not None:
            notes.append(
                Note(
                    past.as_of, past.rating, cached.excess, cached.verdict or "", cached.reflection
                )
            )
            continue
        result = settle(past.rating, bars, benchmark, past.as_of, past.holding_days, bridge.as_of)
        if result is None or result.verdict is None:
            continue
        reflection = reflection_for(past.rating, result, past.drivers, past.risks)
        repository.record_evaluation(
            past.decision_id,
            Evaluation(
                actual=result.actual,
                benchmark_symbol=benchmark_symbol or None,
                benchmark=result.benchmark,
                excess=result.excess,
                verdict=result.verdict,
                reflection=reflection,
                holding_days=past.holding_days,
                entry_date=result.entry_date,
                exit_date=result.exit_date,
                quality_flag="measured",
                note=f"{result.entry_date} 至 {result.exit_date} 的收盘价变化（决策记忆结算）",
            ),
        )
        notes.append(Note(past.as_of, past.rating, result.excess, result.verdict, reflection))
    return notes


def evaluate(engine: Engine, user_id: UUID, run_id: UUID) -> Evaluation | None:
    repository = SimulationRepository(engine)
    found = repository.decision_id(user_id, run_id)
    stored = repository.get_owned(user_id, run_id)
    if found is None or stored is None:
        return None
    _decision_id, rating, holding = found
    store = DataStore(engine)
    bridge = FinScopeDataBridge(store, date.max)
    subject = bridge.subject(stored.symbol)
    if subject is None:
        return _insufficient(stored.as_of, holding, "标的不在已收录的证券列表里")
    bars = store.bars(subject.instrument_id)
    if not bars:
        return _insufficient(stored.as_of, holding, "已入库日线不足以计算区间收益，没有写入收益率")
    benchmark_symbol, benchmark = _full_benchmark(bridge, subject)
    result = settle(rating, bars, benchmark, stored.as_of, holding, bars[-1].trade_date)
    if result is None:
        return _insufficient(
            stored.as_of,
            holding,
            f"持有期 {holding} 天尚未走完或已入库日线不足，没有写入收益率",
        )
    detail_decision = stored.detail.get("decision")
    drivers: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    if isinstance(detail_decision, dict):
        drivers = _texts(detail_decision.get("drivers"))
        risks = _texts(detail_decision.get("risks"))
    return Evaluation(
        actual=result.actual,
        benchmark_symbol=benchmark_symbol or None,
        benchmark=result.benchmark,
        excess=result.excess,
        verdict=result.verdict,
        reflection=reflection_for(rating, result, drivers, risks) if rating != "review" else "",
        holding_days=holding,
        entry_date=result.entry_date,
        exit_date=result.exit_date,
        quality_flag="measured",
        note=f"{result.entry_date.isoformat()} 至 {result.exit_date.isoformat()} 的收盘价变化",
    )


# ---------------------------------------------------------------- 网格回测实验


@dataclass(frozen=True)
class GridRow:
    symbol: str
    as_of: date
    rating: str
    baseline: str
    confidence: Decimal
    actual: Decimal
    excess: Decimal


def run_grid(
    engine: Engine,
    *,
    symbols: list[str],
    start: date,
    end: date,
    step: int,
    holding_days: int,
    max_debate_rounds: int,
) -> dict[str, object]:
    """标的 × 日期网格：每个 as_of 只看当日及以前的数据，事后用真实收盘结算超额。

    对照组是「仅技术面分析师」的单一信号评级，用来做单智能体 vs 多智能体的对比实验。
    """

    store = DataStore(engine)
    config = PipelineConfig(
        max_debate_rounds=max_debate_rounds, holding_days=holding_days, use_memory=False
    )
    rows: list[GridRow] = []
    skipped: list[str] = []
    planned = 0
    for symbol in symbols:
        bridge, subject = resolve(store, symbol, end)
        bars = store.bars(subject.instrument_id)
        benchmark_symbol, benchmark = _full_benchmark(bridge, subject)
        if not bars or not benchmark:
            skipped.append(f"{symbol}：没有已入库日线或基准，无法结算")
            continue
        days = [bar.trade_date for bar in bars if start <= bar.trade_date <= end][::step]
        for day in days:
            planned += 1
            if planned > MAX_GRID_RUNS:
                break
            result = settle("hold", bars, benchmark, day, holding_days, bars[-1].trade_date)
            if result is None or result.excess is None:
                skipped.append(f"{symbol} {day}：持有期尚未走完")
                continue
            output = run_pipeline(FinScopeDataBridge(store, day), subject, config)
            technical = next(item for item in output.analysts if item.analyst == "technical")
            rows.append(
                GridRow(
                    symbol=subject.symbol,
                    as_of=day,
                    rating=output.final_rating,
                    baseline=rating_for(technical.score),
                    confidence=output.confidence,
                    actual=result.actual,
                    excess=result.excess,
                )
            )
        del benchmark_symbol
    return {
        "config": {
            **config.as_dict(),
            "symbols": symbols,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step,
            "benchmark": "000300.SH",
        },
        "samples": len(rows),
        "skipped": skipped[:20],
        "skipped_count": len(skipped),
        "truncated": planned > MAX_GRID_RUNS,
        "modes": [
            _mode(
                "multi_agent",
                "多角色推演（含辩论与风控）",
                [(row.rating, row.excess) for row in rows],
            ),
            _mode(
                "technical_only",
                "仅技术面分析师（单信号对照）",
                [(row.baseline, row.excess) for row in rows],
            ),
        ],
        "by_rating": _by_rating(rows),
        "rows": [
            {
                "symbol": row.symbol,
                "as_of": row.as_of.isoformat(),
                "rating": DISPLAY[row.rating],
                "baseline": DISPLAY[row.baseline],
                "confidence": str(row.confidence),
                "actual_return": str(row.actual),
                "excess_return": str(row.excess),
            }
            for row in rows
        ],
    }


def _mode(code: str, label: str, pairs: list[tuple[str, Decimal]]) -> dict[str, object]:
    calls = [(direction(rating), excess) for rating, excess in pairs if direction(rating) != 0]
    hits = sum(1 for side, excess in calls if side * excess > 0)
    longs = [excess for side, excess in calls if side > 0]
    shorts = [excess for side, excess in calls if side < 0]
    strategy = [direction(rating) * excess for rating, excess in pairs]
    long_mean = _mean(longs)
    short_mean = _mean(shorts)
    return {
        "code": code,
        "label": label,
        "samples": len(pairs),
        "directional_calls": len(calls),
        "hit_rate": None
        if not calls
        else str((Decimal(hits) / Decimal(len(calls))).quantize(Decimal("0.0001"))),
        "long_mean_excess": None if long_mean is None else str(long_mean),
        "short_mean_excess": None if short_mean is None else str(short_mean),
        "long_short_spread": None
        if long_mean is None or short_mean is None
        else str((long_mean - short_mean).quantize(Decimal("0.0001"))),
        "mean_signed_excess": None if not strategy else str(_mean(strategy)),
    }


def _by_rating(rows: list[GridRow]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for rating in ("buy", "overweight", "hold", "underweight", "sell"):
        picked = [row.excess for row in rows if row.rating == rating]
        base = [row.excess for row in rows if row.baseline == rating]
        found.append(
            {
                "rating": DISPLAY[rating],
                "count": len(picked),
                "mean_excess": None if not picked else str(_mean(picked)),
                "baseline_count": len(base),
                "baseline_mean_excess": None if not base else str(_mean(base)),
            }
        )
    return found


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(Decimal("0.0001"))


def _full_benchmark(bridge: FinScopeDataBridge, subject: Subject) -> tuple[str, list[Bar]]:
    """结算用的完整基准序列；推演本身读基准时仍然走按 as_of 截断的 bridge.benchmark。"""

    return FinScopeDataBridge(bridge.store, date.max).benchmark(subject, limit=100000)


def _insufficient(as_of: date, holding: int, note: str) -> Evaluation:
    return Evaluation(
        actual=None,
        benchmark_symbol=None,
        benchmark=None,
        excess=None,
        verdict=None,
        reflection="",
        holding_days=holding,
        entry_date=as_of,
        exit_date=None,
        quality_flag="insufficient",
        note=note,
    )


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item.get("text", "")) for item in value if isinstance(item, dict))
