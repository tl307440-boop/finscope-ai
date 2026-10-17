from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

_MAX_POINTS = 3000


class FredSnapshotPush(BaseModel):
    """从能访问 FRED 的机器推送总览用的序列；键必须是总览读取的 FRED 序列代码。"""

    series: dict[str, list[tuple[date, Decimal]]] = Field(min_length=1, max_length=32)

    @field_validator("series")
    @classmethod
    def _points_are_bounded(
        cls, series: dict[str, list[tuple[date, Decimal]]]
    ) -> dict[str, list[tuple[date, Decimal]]]:
        for code, points in series.items():
            if not points or len(points) > _MAX_POINTS:
                raise ValueError(f"{code} 的观测数必须在 1 到 {_MAX_POINTS} 之间")
        return series


class FredSnapshotPushResult(BaseModel):
    series: list[str]
    points: int
