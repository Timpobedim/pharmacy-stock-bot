from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from pharmacy_bot.config import Settings
from pharmacy_bot.service import ReportService
from pharmacy_bot.storage import SnapshotStorage

TODAY = date(2026, 9, 11)
NOW = datetime(2026, 9, 11, 9, 30)

STOCK_COLUMNS = ["Аптека", "Товар", "Серия", "Остаток", "Сумма, руб", "Дата последней продажи"]
EXPIRY_COLUMNS = ["Аптека", "Наименование", "Серия", "Годен до", "Кол-во", "Сумма"]
CANONICAL_STOCK = ["аптека", "товар", "серия", "остаток", "сумма", "последняя продажа"]
CANONICAL_EXPIRY = ["аптека", "товар", "серия", "срок годности", "остаток", "сумма"]


def write_xlsx(path: Path, rows: list[tuple[Any, ...]], columns: list[str]) -> Path:
    pd.DataFrame(rows, columns=columns).to_excel(path, index=False)
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_token="",
        allowed_user_ids=[1],
        admin_user_ids=[7],
        data_dir=tmp_path / "data",
        illiquid_days=90,
        illiquid_min_sum=3000,
        expiry_days=30,
    )


@pytest.fixture
def service(settings: Settings) -> ReportService:
    return ReportService(settings, SnapshotStorage(settings.data_dir / "snapshots"), now=lambda: NOW)
