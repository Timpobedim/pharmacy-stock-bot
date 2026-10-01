from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из переменных окружения и файла .env (пример — .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_token: str = ""
    allowed_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    spreadsheet_id: str = ""
    google_credentials_path: Path = Path("credentials.json")
    worksheet_name: str = "Отчёт аптек"
    priority_worksheet_name: str = "Приоритет"

    yandex_oauth_token: str = ""
    yandex_stock_dir: str = "disk:/Отчёты аптек/остатки/"
    yandex_expiry_dir: str = "disk:/Отчёты аптек/сроки/"
    yandex_max_mb: int = 100

    illiquid_days: int = 90
    illiquid_min_sum: float = 3000
    expiry_days: int = 30

    daily_report_time: time | None = None
    report_chat_id: int | None = None
    timezone: str = "Europe/Moscow"

    data_dir: Path = Path("data")

    @field_validator("allowed_user_ids", "admin_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [int(part) for part in value.replace(";", ",").split(",") if part.strip()]
        return value

    @field_validator("daily_report_time", "report_chat_id", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    def can_use(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids or user_id in self.admin_user_ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids
