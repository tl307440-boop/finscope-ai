"""运行时配置：界面里填的值（加密存库）优先，其次环境变量。

api、worker、scheduler 是三个独立进程，各自读库；有效配置缓存 5 秒，所以在界面改完最多等几秒
就在所有进程生效，不用重启容器。
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.secret_box import SecretBox
from app.repositories.app_settings import AppSettingRepository, StoredSetting

logger = logging.getLogger(__name__)

CACHE_SECONDS = 5.0
MAX_VALUE_LENGTH = 512


@dataclass(frozen=True)
class ManagedKey:
    key: str
    field: str
    label: str
    group: str
    secret: bool
    kind: str  # text | url | bool
    help: str
    help_url: str = ""
    placeholder: str = ""


MANAGED: tuple[ManagedKey, ...] = (
    ManagedKey(
        "TIINGO_API_KEY",
        "tiingo_api_key",
        "Tiingo API 密钥",
        "美股行情",
        True,
        "text",
        "美股日线的官方数据源（免费档约 50 次/小时）。不填则美股日线显示「不可用」。",
        "https://www.tiingo.com/account/api/token",
    ),
    ManagedKey(
        "FRED_API_KEY",
        "fred_api_key",
        "FRED API 密钥",
        "美国宏观",
        True,
        "text",
        "不填也能用公开 CSV（只有最新修订值）；填了才有带历史版本的时点数据。",
        "https://fred.stlouisfed.org/docs/api/api_key.html",
    ),
    ManagedKey(
        "SEC_USER_AGENT",
        "sec_user_agent",
        "SEC 访问标识（User-Agent）",
        "美股财报",
        False,
        "text",
        "SEC 要求带联系邮箱，格式如：公司或姓名 邮箱@example.com。",
        "https://www.sec.gov/os/webmaster-faq#developers",
        "FinScopeAI yourname@example.com",
    ),
    ManagedKey(
        "LLM_API_BASE_URL",
        "llm_api_base_url",
        "大模型接口地址",
        "大模型",
        False,
        "url",
        "OpenAI 兼容接口的地址，如 https://api.openai.com/v1 。不填则 AI 段落只走规则分支。",
        "",
        "https://api.openai.com/v1",
    ),
    ManagedKey(
        "LLM_API_KEY",
        "llm_api_key",
        "大模型 API 密钥",
        "大模型",
        True,
        "text",
        "用于 AI 财报解读、问答和推演里的模型节点。",
    ),
    ManagedKey(
        "LLM_MODEL",
        "llm_model",
        "大模型名称",
        "大模型",
        False,
        "text",
        "例如 gpt-4o-mini、deepseek-chat。",
        "",
        "gpt-4o-mini",
    ),
    ManagedKey(
        "ASSISTANT_ENABLED",
        "assistant_enabled",
        "启用 AI 助手（数据工具）",
        "大模型",
        False,
        "bool",
        "打开后问答由模型按需查询已入库的宏观、行情、财务等数据再作答，回答里的数字都要能在查到的"
        "资料里找到；需要模型接口支持工具调用，不支持时自动改用「先规划再作答」。"
        "关闭时问答只转述白名单宏观序列。",
    ),
    ManagedKey(
        "ALLOW_REGISTRATION",
        "allow_registration",
        "开放注册",
        "账号",
        False,
        "bool",
        "打开后登录页显示「注册」；关闭后只有管理员创建的账号能登录。",
    ),
)
_BY_KEY = {item.key: item for item in MANAGED}
_FIELD_TYPES: dict[str, str] = {item.field: item.kind for item in MANAGED}

_cache: dict[str, tuple[float, Settings]] = {}
_cache_lock = threading.Lock()


def managed_key(key: str) -> ManagedKey:
    found = _BY_KEY.get(key)
    if found is None:
        raise AppError(code="NOT_FOUND", message="没有这项配置", status_code=404)
    return found


def _box(base: Settings) -> SecretBox:
    return SecretBox(base.settings_encryption_key or base.jwt_secret)


def _cache_key(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def _decode(base: Settings, stored: dict[str, StoredSetting]) -> tuple[dict[str, str], set[str]]:
    box = _box(base)
    values: dict[str, str] = {}
    broken: set[str] = set()
    for key, row in stored.items():
        if key not in _BY_KEY:
            continue
        plain = box.open(row.value_enc)
        if plain is None:
            broken.add(key)
            logger.warning("app setting undecryptable key=%s", key)
            continue
        values[key] = plain
    return values, broken


def _typed(field: str, raw: str) -> Any:
    if _FIELD_TYPES.get(field) == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return raw


def effective_settings(
    engine: Engine, base: Settings | None = None, *, use_cache: bool = True
) -> Settings:
    """环境变量配置叠上库里的覆盖值；库不可用时退回环境变量，不让功能整体失败。"""

    resolved = base or get_settings()
    cache_key = _cache_key(engine)
    now = time.monotonic()
    if use_cache and base is None:
        with _cache_lock:
            hit = _cache.get(cache_key)
            if hit is not None and hit[0] > now:
                return hit[1]
    try:
        stored = AppSettingRepository(engine).all()
    except Exception:
        logger.warning("app settings unavailable; using environment only")
        return resolved
    values, _broken = _decode(resolved, stored)
    update = {
        _BY_KEY[key].field: _typed(_BY_KEY[key].field, value) for key, value in values.items()
    }
    result = resolved.model_copy(update=update) if update else resolved
    if base is None:
        with _cache_lock:
            _cache[cache_key] = (now + CACHE_SECONDS, result)
    return result


@dataclass(frozen=True)
class IntegrationItem:
    key: str
    label: str
    group: str
    secret: bool
    kind: str
    help: str
    help_url: str
    placeholder: str
    configured: bool
    source: str  # database | environment | none
    preview: str
    updated_at: datetime | None
    updated_by: str | None
    problem: str


def _mask(value: str) -> str:
    return f"••••{value[-4:]}" if len(value) >= 8 else "••••"


class IntegrationService:
    def __init__(self, engine: Engine, base: Settings | None = None) -> None:
        self.engine = engine
        self.base = base or get_settings()
        self.repository = AppSettingRepository(engine)

    def list(self) -> list[IntegrationItem]:
        stored = self.repository.all()
        values, broken = _decode(self.base, stored)
        items: list[IntegrationItem] = []
        for entry in MANAGED:
            row = stored.get(entry.key)
            problem = ""
            if entry.key in values:
                source, shown = "database", values[entry.key]
            else:
                env_value = getattr(self.base, entry.field)
                if entry.kind == "bool":
                    shown, source = ("true" if env_value else "false"), "environment"
                else:
                    shown = str(env_value)
                    source = "environment" if shown.strip() else "none"
                if entry.key in broken:
                    problem = "已保存的值无法解密（加密主密钥变了），请重新填写"
            items.append(
                IntegrationItem(
                    key=entry.key,
                    label=entry.label,
                    group=entry.group,
                    secret=entry.secret,
                    kind=entry.kind,
                    help=entry.help,
                    help_url=entry.help_url,
                    placeholder=entry.placeholder,
                    configured=source != "none",
                    source=source,
                    preview=(_mask(shown) if entry.secret and shown else shown),
                    updated_at=None if row is None else row.updated_at,
                    updated_by=None if row is None else row.updated_by_name,
                    problem=problem,
                )
            )
        return items

    def save(self, key: str, value: str, *, user_id: uuid.UUID | None, now: datetime) -> None:
        entry = managed_key(key)
        cleaned = validate_value(entry, value)
        self.repository.put(
            key=key,
            value_enc=_box(self.base).seal(cleaned),
            is_secret=entry.secret,
            user_id=user_id,
            now=now,
        )
        invalidate()
        logger.info("integration saved key=%s", key)

    def clear(self, key: str) -> bool:
        managed_key(key)
        removed = self.repository.delete(key)
        invalidate()
        logger.info("integration cleared key=%s", key)
        return removed

    def candidate_or_effective(self, key: str, candidate: str | None) -> str:
        """测试连接用：优先用还没保存的候选值，否则用当前生效的值。"""

        entry = managed_key(key)
        if candidate is not None and candidate.strip():
            return validate_value(entry, candidate)
        effective = effective_settings(self.engine, use_cache=False)
        value = getattr(effective, entry.field)
        return str(value).strip()


def validate_value(entry: ManagedKey, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise AppError(code="VALIDATION_ERROR", message="值不能为空", status_code=422)
    if len(cleaned) > MAX_VALUE_LENGTH:
        raise AppError(code="VALIDATION_ERROR", message="值太长", status_code=422)
    if any(ord(char) < 32 for char in cleaned):
        raise AppError(code="VALIDATION_ERROR", message="值里不能有换行或控制字符", status_code=422)
    if entry.kind == "url" and not cleaned.lower().startswith(("http://", "https://")):
        raise AppError(
            code="VALIDATION_ERROR", message="地址要以 http:// 或 https:// 开头", status_code=422
        )
    if entry.kind == "bool" and cleaned.lower() not in {"true", "false"}:
        raise AppError(code="VALIDATION_ERROR", message="只能填 true 或 false", status_code=422)
    if entry.key == "SEC_USER_AGENT" and "@" not in cleaned:
        raise AppError(
            code="VALIDATION_ERROR",
            message="SEC 要求访问标识里带联系邮箱，例如：FinScopeAI you@example.com",
            status_code=422,
        )
    return cleaned.lower() if entry.kind == "bool" else cleaned
