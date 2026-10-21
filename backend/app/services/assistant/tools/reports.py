"""`research_reports`：东方财富研报中心的研报索引（只有元数据，没有正文）。

行业研报按行业名称查（先用本系统入库的板块快照把名称对到板块代码），个股研报按 6 位代码查。
每条只有标题、券商、评级、研究员、日期和原文链接；研报正文的版权归券商，这里不取、不存，
也不取盈利预测。时点：只返回 `published_on <= as_of` 的条目，东财接口本身按日期区间查询，
所以可以回看过去的日期。
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers.errors import ProviderError
from app.repositories.board_market import BoardMarketRepository
from app.services.assistant.loop_bridge import call_async
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.research_reports import ResearchReportService, get_report_service

MAX_BOARDS = 3
MAX_ROWS = 15
SOURCE = "东方财富研报中心（只含标题、机构、评级、日期，不含正文）"
_WHY_EMPTY = (
    "东财研报按二级行业归档，一级行业和概念板块通常查不到；"
    "可以换个更具体的行业名称，或者查龙头公司的个股研报"
)


class ResearchReportsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry: str | None = Field(
        default=None, min_length=1, max_length=20, description="行业名称关键词，如「半导体」"
    )
    stock_code: str | None = Field(
        default=None, pattern=r"^\d{6}$", description="A 股 6 位代码，如 688981；和 industry 二选一"
    )
    days: int = Field(default=60, ge=7, le=180, description="回看多少天，默认 60")
    as_of: date | None = Field(default=None, description="只看这个日期当天及之前发布的研报")

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "ResearchReportsArgs":
        if (self.industry is None) == (self.stock_code is None):
            raise ValueError("industry 和 stock_code 要给一个，且只给一个")
        if self.industry is not None:
            self.industry = self.industry.strip()
            if not self.industry:
                raise ValueError("industry 不能是空白")
        return self


@dataclass(frozen=True)
class _Item:
    published: date
    info_code: str
    row: dict[str, str | None]


class ResearchReportsTool:
    name: ClassVar[str] = "research_reports"
    description: ClassVar[str] = (
        "查东方财富研报中心的研报索引：给行业名称（如「半导体」）查行业研报，或给 6 位 A 股代码"
        "查个股研报。只有标题、券商、评级、研究员、日期和原文链接，没有正文：只能转述标题和评级，"
        "不要猜研报的内容或观点。默认查最近 60 天，最多 15 条，按日期从新到旧。"
    )
    args_model: ClassVar[type[BaseModel]] = ResearchReportsArgs

    def __init__(self, service: ResearchReportService | None = None) -> None:
        self._service = service

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, ResearchReportsArgs)
        pit = effective_as_of(args.as_of, ctx.today_cn)
        start = pit - timedelta(days=args.days)
        service = self._service or get_report_service()
        if args.stock_code is not None:
            title = f"个股研报：{args.stock_code}"
            targets = [("stock", args.stock_code, args.stock_code)]
        else:
            title = f"行业研报：{args.industry}"
            targets = _industry_targets(ctx, args.industry or "", pit)
            if not targets:
                return unavailable(
                    self.name,
                    title,
                    pit,
                    f"没有名称包含「{args.industry}」的行业板块；{_WHY_EMPTY}",
                    SOURCE,
                )
        try:
            items, failures, stale = call_async(
                lambda: _collect(service, targets, start, pit), timeout=40.0
            )
        except ProviderError as error:
            return unavailable(
                self.name, title, pit, f"研报接口暂时取不到：{error.message}", SOURCE
            )
        if not items:
            reason = failures[0] if failures else f"最近 {args.days} 天没有查到研报；{_WHY_EMPTY}"
            return unavailable(self.name, title, pit, reason, SOURCE)
        ordered = sorted(items, key=lambda item: (item.published, item.info_code), reverse=True)
        shown = ordered[:MAX_ROWS]
        notes = [f"取不到：{message}" for message in failures]
        if stale:
            notes.append("上游刷新失败，展示的是上次抓取的列表")
        return ToolResult(
            tool=self.name,
            title=title,
            columns=COLUMNS,
            rows=tuple(item.row for item in shown),
            as_of=shown[0].published,
            source=SOURCE,
            status="stale" if notes else "ok",
            reason="；".join(notes) or None,
            truncated=max(0, len(ordered) - MAX_ROWS),
        )


COLUMNS = (
    Column("published", "发布日", "", "date"),
    Column("org", "券商", "", "text"),
    Column("rating", "评级", "", "text"),
    Column("title", "标题", "", "text"),
    Column("researcher", "研究员", "", "text"),
    Column("scope", "对象", "", "text"),
    Column("url", "原文链接", "", "text"),
)


def _industry_targets(ctx: ToolContext, keyword: str, pit: date) -> list[tuple[str, str, str]]:
    """名称 → 行业板块代码：优先二级行业（东财研报按二级行业归档），最多 3 个。"""

    found = BoardMarketRepository(ctx.engine).search_latest(keyword, pit, ["industry"], 8)
    ranked = sorted(found, key=lambda item: item.node.level != 2)  # 稳定排序：同级保持原顺序
    return [("industry", item.node.code, item.node.name) for item in ranked[:MAX_BOARDS]]


async def _collect(
    service: ResearchReportService,
    targets: list[tuple[str, str, str]],
    start: date,
    end: date,
) -> tuple[list[_Item], list[str], bool]:
    items: dict[str, _Item] = {}
    failures: list[str] = []
    stale = False
    for kind, code, label in targets:
        try:
            if kind == "stock":
                stocks = await service.stock(code, start, end)
                stale = stale or stocks.stale
                for report in stocks.value:
                    _keep(items, report.published_on, end, report.info_code, _row(report, label))
            else:
                industries = await service.industry(code, start, end)
                stale = stale or industries.stale
                for entry in industries.value:
                    _keep(items, entry.published_on, end, entry.info_code, _row(entry, label))
        except ProviderError as error:
            failures.append(f"{label}：{error.message}")
    return list(items.values()), failures, stale


def _keep(
    items: dict[str, _Item],
    published: date,
    end: date,
    info_code: str,
    row: dict[str, str | None],
) -> None:
    if published > end:  # 时点：不用 as_of 之后发布的
        return
    items.setdefault(info_code, _Item(published, info_code, row))


def _row(report: Any, label: str) -> dict[str, str | None]:
    scope = getattr(report, "stock_name", None)
    return {
        "published": report.published_on.isoformat(),
        "org": report.org_name,
        "rating": report.rating,
        "title": report.title,
        "researcher": report.researcher,
        "scope": f"{scope}（{report.stock_code}）" if scope else label,
        "url": report.url,
    }
