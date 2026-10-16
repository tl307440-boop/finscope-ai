from datetime import date, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_JWT_SECRET = "replace-with-long-random-secret"
DataMode = Literal["live", "demo"]
AppEnv = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """进程配置。密钥只从环境读取，不写入响应。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "development"
    app_base_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"
    database_url: str = "postgresql+psycopg://finscope:change-me@localhost:5432/finscope"
    jwt_secret: str = PLACEHOLDER_JWT_SECRET
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    # 开放注册：关掉后只有管理员预先创建的账号能登录。界面里的开关（库里的值）优先于这里。
    allow_registration: bool = True
    # 界面填写的密钥在库里用它加密；留空时用 JWT_SECRET 派生。
    settings_encryption_key: str = ""
    # 首个管理员：库里还没有任何管理员时，api 启动会按这三项创建一次（已有管理员则忽略）。
    admin_username: str = ""
    admin_email: str = ""
    admin_password: str = ""
    fred_api_key: str = ""
    # Tiingo 官方免费档（个人研究）：每小时 50 次、每天 1000 次、每月 500 个标的。
    # 留空 = 美股日线不可用。
    tiingo_api_key: str = ""
    sec_user_agent: str = "FinScopeAI research-admin@example.com"
    tushare_token: str = ""
    llm_api_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_dim: int = 1536
    ai_daily_token_budget: int = 200_000
    enable_news: bool = False
    data_mode: DataMode = "live"
    upload_max_mb: int = 30
    upload_max_pages: int = 200
    # 规格书 §12.1：上传检查里包含「解析时间预算」，超时即停止解析并记失败。
    upload_parse_time_budget_seconds: float = 30.0
    chunk_target_tokens: int = 600
    chunk_max_tokens: int = 800
    chunk_overlap_tokens: int = 80
    log_level: str = "INFO"
    worker_heartbeat_interval_seconds: int = 5
    worker_heartbeat_ttl_seconds: int = 30
    worker_service_name: str = "worker"
    # 任务调度（规格书 §11）。调度器只负责往 sync_jobs 写 queued 记录，具体执行在 worker。
    scheduler_enabled: bool = True
    scheduler_timezone: str = "UTC"
    scheduler_run_on_startup: bool = False
    # 触发时刻（本地时区 = scheduler_timezone）。FRED 每天 1 次；SEC 每天 1-2 次（逗号分隔）。
    scheduler_fred_hour: int = 21
    scheduler_fred_minute: int = 30
    scheduler_sec_times: str = "14:00,02:00"
    # 宏观序列回看的自然日天数：FRED 会修订往期值，窗口要盖住最近一次修订。
    scheduler_fred_window_days: int = 45
    # 计算类任务的触发时刻：财报指标与宏观状态各一次，热点事件 P2 更靠后。
    scheduler_metrics_hour: int = 22
    scheduler_metrics_minute: int = 30
    scheduler_macro_regime_hour: int = 23
    scheduler_macro_regime_minute: int = 0
    scheduler_hotspot_hour: int = 23
    scheduler_hotspot_minute: int = 30
    # 已授权的热点来源代码；为空时 refresh_hotspot_events 直接禁用，不猜来源。
    scheduler_hotspot_source: str = "disclosures"
    # 错过触发时刻后的宽限秒数；超过就跳过本轮，避免堆积。
    scheduler_misfire_grace_seconds: int = 300
    # a-stock：收盘后延迟执行的分钟数与交易日判定用的交易所日历代码。
    scheduler_cn_market_exchange: str = "SSE"
    scheduler_us_market_exchange: str = "XNYS"
    scheduler_a_stock_close_delay_minutes: int = 30
    scheduler_a_stock_lag_trading_days: int = 1
    scheduler_fred_series: str = "GDP,UNRATE,PAYEMS,CPIAUCSL,PCEPILFE,FEDFUNDS,DGS2,DGS10,T10Y2Y"
    # 未实现的执行器默认关闭，打开后调度器才会登记对应 job；禁止假成功。
    scheduler_enable_fred: bool = True
    scheduler_enable_a_stock_bars: bool = True
    # SEC / A 股财报 / 美股日线只刷新「被关注过」的公司（自选、已抓取过、有行情的），不全市场轮询。
    scheduler_enable_us_sec_filings: bool = True
    scheduler_enable_cn_statements: bool = True
    scheduler_enable_us_bars: bool = True
    # 全市场证券清单（SEC ticker 表 + 巨潮 A 股列表）每周刷新一次。
    scheduler_enable_universe: bool = True
    scheduler_universe_hour: int = 6
    scheduler_universe_minute: int = 0
    # 中国宏观六序列：东方财富 / 商务部公开接口，每天抓一次（重复抓取由去重键保证幂等）。
    scheduler_enable_china_macro: bool = True
    scheduler_china_macro_hour: int = 1
    scheduler_china_macro_minute: int = 20
    # 美股收盘（美东 16:00）折合北京时间约凌晨 4~5 点，所以 6:30 之后取日线，周二到周六触发。
    scheduler_us_bars_hour: int = 6
    scheduler_us_bars_minute: int = 30
    scheduler_cn_statements_hour: int = 20
    scheduler_cn_statements_minute: int = 30
    # 美股日线每轮最多登记的标的数；Tiingo 免费档每小时只有 50 次请求，留出按需抓取的余量。
    scheduler_us_bars_max_symbols: int = 30
    # 同一公司同一数据集失败后，按需抓取至少隔这么多分钟才允许再试，防止被反复点击打爆上游。
    ondemand_retry_cooldown_minutes: int = 15
    scheduler_enable_financial_metrics: bool = True
    scheduler_enable_macro_regime: bool = True
    scheduler_enable_hotspot_events: bool = True
    # parse_document：上传后立即入队是主路径，这里只登记「兜底扫描」的兜底 job。
    scheduler_enable_parse_document: bool = True
    scheduler_parse_sweep_seconds: int = 300
    login_max_failures: int = 5
    login_failure_window_seconds: int = 300
    # 同一来源 IP 每小时最多尝试注册的次数（成败都计）。
    register_max_per_hour: int = 5

    @field_validator("frontend_origin")
    @classmethod
    def normalize_origins(cls, value: str) -> str:
        return value.strip()

    @field_validator("scheduler_timezone")
    @classmethod
    def normalize_timezone(cls, value: str) -> str:
        zone = value.strip() or "UTC"
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("SCHEDULER_TIMEZONE 不是有效的 IANA 时区") from exc
        return zone

    @model_validator(mode="after")
    def check_runtime_policy(self) -> "Settings":
        if self.worker_heartbeat_ttl_seconds <= self.worker_heartbeat_interval_seconds:
            raise ValueError("WORKER_HEARTBEAT_TTL_SECONDS 必须大于心跳间隔")
        if self.login_max_failures < 1 or self.login_failure_window_seconds < 1:
            raise ValueError("登录失败限制必须为正数")
        if not (0 <= self.chunk_overlap_tokens < self.chunk_target_tokens <= self.chunk_max_tokens):
            raise ValueError("分块长度必须满足重叠小于目标，且目标不超过上限")
        if self.upload_parse_time_budget_seconds <= 0:
            raise ValueError("UPLOAD_PARSE_TIME_BUDGET_SECONDS 必须为正数")
        if self.scheduler_a_stock_close_delay_minutes < 0:
            raise ValueError("SCHEDULER_A_STOCK_CLOSE_DELAY_MINUTES 不能为负数")
        if self.scheduler_a_stock_lag_trading_days < 0:
            raise ValueError("SCHEDULER_A_STOCK_LAG_TRADING_DAYS 不能为负数")
        if self.scheduler_parse_sweep_seconds < 30:
            raise ValueError("SCHEDULER_PARSE_SWEEP_SECONDS 不能小于 30 秒")
        if self.app_env == "production":
            if self.jwt_secret == PLACEHOLDER_JWT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("生产环境 JWT_SECRET 不能使用示例值，且长度至少 32")
            if "change-me" in self.database_url:
                raise ValueError("生产环境 DATABASE_URL 不能使用示例口令")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.frontend_origin.split(",") if item.strip()]

    def scheduler_series_codes(self) -> list[str]:
        """FRED 待同步序列；去重并统一大写，空项丢掉。"""

        seen: dict[str, None] = {}
        for item in self.scheduler_fred_series.split(","):
            code = item.strip().upper()
            if code:
                seen.setdefault(code, None)
        return list(seen)

    def scheduler_sec_slots(self) -> list[tuple[int, int]]:
        """SEC 每天 1–2 次的触发时刻，按 ``HH:MM`` 解析；非法项直接报错，不静默跳过。

        方法名与字段 ``scheduler_sec_times`` 错开，避免同名字段被方法覆盖（ruff F811）。
        """

        slots: list[tuple[int, int]] = []
        for item in self.scheduler_sec_times.split(","):
            raw = item.strip()
            if not raw:
                continue
            hour_text, _, minute_text = raw.partition(":")
            try:
                hour, minute = int(hour_text), int(minute_text)
            except ValueError as exc:
                raise ValueError("SCHEDULER_SEC_TIMES 必须是 HH:MM 逗号分隔") from exc
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("SCHEDULER_SEC_TIMES 的时刻超出范围")
            slots.append((hour, minute))
        if not slots:
            raise ValueError("SCHEDULER_SEC_TIMES 至少需要一个时刻")
        return slots

    def scheduler_fred_start(self, end: date) -> date:
        """FRED 观察窗起点：往回收若干自然日，覆盖往期修订。"""

        return end - timedelta(days=self.scheduler_fred_window_days)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
