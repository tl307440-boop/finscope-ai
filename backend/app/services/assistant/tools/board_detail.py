"""`board_detail`：一个板块的详情，回答「这个行业到底怎么样」需要的那几组数。

- summary：行情位置（涨跌幅、换手、资金、涨跌家数）、日线口径区间涨幅与相对沪深 300 的超额、
  估值分位（东财只覆盖二级行业）、成分股数；
- members：成分股里市值最大的几只（涨跌幅、市盈率、市值、资金）；
- sub_industries：成分股按所属细分行业聚合（市值加权涨跌幅、资金）。

数据来自和「产业研究」页面同一个服务（实时为主，实时失败退回最近一次收盘入库快照），
所以只有「现在」的数据：问过去某天的详情要如实拒绝。金额按亿元给出（元 ÷ 10^8，Decimal）。
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from app.providers.errors import ProviderError
from app.repositories.board_market import BoardMarketRepository
from app.schemas.industry_market import BoardDetail, PercentileItem, Section
from app.services.assistant.loop_bridge import call_async
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.fmt import count, plain, yi
from app.services.assistant.tools.live import industry_service, live_only_reason, upstream_reason
from app.services.industry_market import IndustryMarketService

_CODE = re.compile(r"^BK\d{4}$", re.IGNORECASE)
SOURCE = "东方财富板块行情、成分与行业估值（实时；失败时用本系统收盘入库快照）"
MEMBER_LIMIT = 15
_DAILY = "按本系统日线计算"
_EM = "东财口径"


class BoardDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board: str = Field(
        min_length=1,
        max_length=20,
        description="板块名称（如「半导体」）或板块代码（如 BK1036）",
    )
    view: Literal["summary", "members", "sub_industries"] = Field(
        default="summary",
        description="summary 位置与估值；members 市值最大的成分股；sub_industries 细分行业",
    )
    top: int = Field(default=MEMBER_LIMIT, ge=3, le=30, description="members 视图返回几只")
    as_of: date | None = Field(default=None, description="只能是今天；历史日期会被拒绝")


class BoardDetailTool:
    name: ClassVar[str] = "board_detail"
    description: ClassVar[str] = (
        "查一个行业 / 概念板块的详情。summary：行情位置、日线口径区间涨幅、相对沪深 300 的超额、"
        "估值分位（只有二级行业有）；members：市值最大的成分股；sub_industries：细分行业。"
        "只有实时数据，不能回看历史日期。是已经发生的情况，不是预测。"
    )
    args_model: ClassVar[type[BaseModel]] = BoardDetailArgs

    def __init__(self, service: IndustryMarketService | None = None) -> None:
        self._service = service

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, BoardDetailArgs)
        today = ctx.today_cn
        pit = effective_as_of(args.as_of, today)
        refusal = live_only_reason(args.as_of, today, "板块详情")
        if refusal is not None:
            return unavailable(self.name, f"板块详情：{args.board}", pit, refusal, SOURCE)
        code = _resolve(ctx.engine, args.board, today)
        if code is None:
            return unavailable(
                self.name,
                f"板块详情：{args.board}",
                pit,
                f"没有找到名称是「{args.board}」的板块；先用 industry_board 按关键词查到板块名称",
                SOURCE,
            )
        service = self._service or industry_service(ctx.engine)
        try:
            detail = call_async(lambda: service.detail(code), timeout=45.0)
        except ProviderError as error:
            return unavailable(
                self.name, f"板块详情：{args.board}", pit, upstream_reason(error), SOURCE
            )
        return _result(self.name, detail, args, today)


def _resolve(engine: Engine, board: str, today: date) -> str | None:
    text = board.strip()
    if _CODE.match(text):
        return text.upper()
    found = BoardMarketRepository(engine).search_latest(text, today, ["industry", "concept"], 1)
    return found[0].node.code if found else None


def _result(tool: str, detail: BoardDetail, args: BoardDetailArgs, today: date) -> ToolResult:
    board = detail.board
    title = f"板块详情：{board.name}（{board.code}）"
    stored = detail.quote_section.origin == "stored"
    as_of = detail.quote_section.data_date if stored and detail.quote_section.data_date else today
    columns: tuple[Column, ...]
    if args.view == "members":
        columns, rows, empty = MEMBER_COLUMNS, _member_rows(detail, args.top), "没有成分股数据"
        section = detail.members.section
        title += "：成分股（按市值）"
    elif args.view == "sub_industries":
        columns, rows, empty = SUB_COLUMNS, _sub_rows(detail), "这个板块没有细分行业划分"
        section = detail.members.section
        title += "：细分行业"
    else:
        columns, rows, empty = SUMMARY_COLUMNS, _summary_rows(detail), "没有可用的数据"
        section = detail.quote_section
    if not rows:
        reason = section.reason if section.status == "unavailable" and section.reason else empty
        return unavailable(tool, title, as_of, reason, SOURCE)
    notes = _notes(detail, args.view)
    return ToolResult(
        tool=tool,
        title=title,
        columns=columns,
        rows=tuple(rows),
        as_of=as_of,
        source=SOURCE,
        status="stale" if notes else "ok",
        reason="；".join(notes) or None,
    )


def _notes(detail: BoardDetail, view: str) -> list[str]:
    """用到的区块里不是 ok 的，逐个说明；模型据此决定哪些话不能说满。"""

    used: dict[str, Section] = {"行情": detail.quote_section}
    if view == "summary":
        used.update({"日线走势": detail.trend.section, "成分股": detail.members.section})
    else:
        used["成分股"] = detail.members.section
    notes = [
        f"{label}：{section.reason or '数据偏旧'}"
        for label, section in used.items()
        if section.status != "ok"
    ]
    return notes


SUMMARY_COLUMNS = (
    Column("item", "指标", "", "text"),
    Column("value", "数值", "", "number"),
    Column("unit", "单位", "", "text"),
    Column("basis", "口径", "", "text"),
    Column("note", "说明", "", "text"),
)
MEMBER_COLUMNS = (
    Column("code", "代码", "", "text"),
    Column("name", "名称", "", "text"),
    Column("change_pct", "当日涨跌幅", "%", "number"),
    Column("change_5d_pct", "5日涨跌幅", "%", "number"),
    Column("change_60d_pct", "60日涨跌幅", "%", "number"),
    Column("change_ytd_pct", "年初至今涨跌幅", "%", "number"),
    Column("pe_dynamic", "动态市盈率", "倍", "number"),
    Column("pb", "市净率", "倍", "number"),
    Column("market_cap", "总市值", "亿元", "number"),
    Column("main_net_inflow", "当日主力净流入", "亿元", "number"),
    Column("sub_industry", "细分行业", "", "text"),
)
SUB_COLUMNS = (
    Column("name", "细分行业", "", "text"),
    Column("members", "成分股数", "家", "number"),
    Column("market_cap", "总市值", "亿元", "number"),
    Column("weighted_change_pct", "市值加权涨跌幅", "%", "number"),
    Column("main_net_inflow", "当日主力净流入", "亿元", "number"),
)


def _metric(
    item: str,
    value: str | None,
    unit: str,
    basis: str,
    note: str | None = None,
) -> dict[str, str | None]:
    return {"item": item, "value": value, "unit": unit, "basis": basis, "note": note}


def _summary_rows(detail: BoardDetail) -> list[dict[str, str | None]]:
    board = detail.board
    rows = [
        _metric("最新点位", plain(board.last), "点", _EM),
        _metric("当日涨跌幅", plain(board.change_pct), "%", _EM),
        _metric("近5日涨跌幅", plain(board.change_5d_pct), "%", _EM),
        _metric("近10日涨跌幅", plain(board.change_10d_pct), "%", _EM),
        _metric("近60日涨跌幅", plain(board.change_60d_pct), "%", _EM),
        _metric("年初至今涨跌幅", plain(board.change_ytd_pct), "%", _EM),
        _metric("成交额", yi(board.amount), "亿元", _EM),
        _metric("换手率", plain(board.turnover_pct), "%", _EM),
        _metric("板块市盈率TTM", plain(board.pe_ttm), "倍", _EM),
        _metric("总市值", yi(board.market_cap), "亿元", _EM),
        _metric("当日主力净流入", yi(board.main_net_inflow), "亿元", _EM),
        _metric("近5日主力净流入", yi(board.main_net_inflow_5d), "亿元", _EM),
        _metric("上涨家数", count(board.advancers), "家", _EM),
        _metric("下跌家数", count(board.decliners), "家", _EM),
    ]
    rows.extend(_trend_rows(detail))
    rows.extend(_valuation_rows(detail))
    members = detail.members
    if members.section.status != "unavailable":
        rows.append(_metric("成分股总数", count(members.total), "家", _EM))
        rows.append(_metric("成分股停牌数", count(members.suspended), "家", _EM))
    return rows


def _trend_rows(detail: BoardDetail) -> list[dict[str, str | None]]:
    trend = detail.trend
    if trend.section.status == "unavailable":
        return [_metric("日线区间涨幅与超额", None, "", _DAILY, trend.section.reason)]
    returns, bench = trend.returns, trend.benchmark
    return [
        _metric("近20个交易日涨幅", plain(returns.d20), "%", _DAILY),
        _metric("近120个交易日涨幅", plain(returns.d120), "%", _DAILY),
        _metric("近250个交易日涨幅", plain(returns.d250), "%", _DAILY),
        _metric(f"近20个交易日相对{bench}超额", plain(returns.excess_d20), "个百分点", _DAILY),
        _metric(f"近60个交易日相对{bench}超额", plain(returns.excess_d60), "个百分点", _DAILY),
        _metric(f"近250个交易日相对{bench}超额", plain(returns.excess_d250), "个百分点", _DAILY),
    ]


def _valuation_rows(detail: BoardDetail) -> list[dict[str, str | None]]:
    valuation = detail.valuation
    if valuation.section.status == "unavailable":
        return [_metric("估值分位", None, "", "东财行业估值库", valuation.section.reason)]
    years = f"近{valuation.window_years}年"
    basis = f"东财行业估值库，{years}日度样本"
    rows: list[dict[str, str | None]] = []
    for label, item in (("市盈率PE(TTM)", valuation.pe_ttm), ("市净率PB(MRQ)", valuation.pb_mrq)):
        if item is None:
            rows.append(_metric(label, None, "", basis, "样本不足，没有分位"))
            continue
        rows.extend(_percentile_rows(label, item, basis))
    if valuation.loss_members is not None:
        rows.append(_metric("亏损成分股数", count(valuation.loss_members), "家", basis))
    if valuation.members is not None:
        rows.append(_metric("估值样本成分股数", count(valuation.members), "家", basis))
    return rows


def _percentile_rows(label: str, item: PercentileItem, basis: str) -> list[dict[str, str | None]]:
    return [
        _metric(f"{label}当前值", plain(item.current), "倍", basis),
        _metric(f"{label}分位", plain(item.percentile), "%", basis, "不高于当前值的交易日占比"),
        _metric(f"{label}中位数", plain(item.median), "倍", basis),
        _metric(f"{label}20%分位值", plain(item.p20), "倍", basis),
        _metric(f"{label}80%分位值", plain(item.p80), "倍", basis),
    ]


def _member_rows(detail: BoardDetail, top: int) -> list[dict[str, str | None]]:
    if detail.members.section.status == "unavailable":
        return []
    ranked = sorted(
        detail.members.members, key=lambda row: row.market_cap or Decimal(0), reverse=True
    )
    return [
        {
            "code": row.code,
            "name": row.name,
            "change_pct": plain(row.change_pct),
            "change_5d_pct": plain(row.change_5d_pct),
            "change_60d_pct": plain(row.change_60d_pct),
            "change_ytd_pct": plain(row.change_ytd_pct),
            "pe_dynamic": plain(row.pe_dynamic),
            "pb": plain(row.pb),
            "market_cap": yi(row.market_cap),
            "main_net_inflow": yi(row.main_net_inflow),
            "sub_industry": row.sub_industry,
        }
        for row in ranked[:top]
    ]


def _sub_rows(detail: BoardDetail) -> list[dict[str, str | None]]:
    return [
        {
            "name": row.name,
            "members": count(row.members),
            "market_cap": yi(row.market_cap),
            "weighted_change_pct": plain(row.weighted_change_pct),
            "main_net_inflow": yi(row.main_net_inflow),
        }
        for row in detail.members.sub_industries
    ]
