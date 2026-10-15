from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.config import Settings


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    id: UUID
    username: str
    email: str
    role: Literal["researcher", "admin"]


class PreferencePublic(BaseModel):
    theme: Literal["dark", "light"]
    rise_color_scheme: Literal["cn", "us"]
    timezone: str
    default_market: Literal["CN", "US"]
    default_lookback: str


class PreferenceUpdate(BaseModel):
    theme: Literal["dark", "light"] | None = None
    rise_color_scheme: Literal["cn", "us"] | None = None
    timezone: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_/+-]+$",
    )
    default_market: Literal["CN", "US"] | None = None
    default_lookback: Literal["1m", "3m", "1y", "all"] | None = None

    def changes(self) -> dict[str, str]:
        return {key: value for key, value in self.model_dump().items() if value is not None}


class LoginData(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserPublic


class MeData(BaseModel):
    user: UserPublic
    preferences: PreferencePublic


class ProviderCredential(BaseModel):
    code: str
    credential_required: bool
    credential_present: bool


class ProviderList(BaseModel):
    providers: list[ProviderCredential]


def list_provider_credentials(settings: Settings) -> ProviderList:
    return ProviderList(
        providers=[
            ProviderCredential(
                code="fred",
                credential_required=True,
                credential_present=bool(settings.fred_api_key),
            ),
            ProviderCredential(
                code="sec_edgar",
                credential_required=True,
                credential_present=bool(settings.sec_user_agent),
            ),
            ProviderCredential(
                code="tushare",
                credential_required=True,
                credential_present=bool(settings.tushare_token),
            ),
            ProviderCredential(
                code="akshare",
                credential_required=False,
                credential_present=False,
            ),
        ]
    )
