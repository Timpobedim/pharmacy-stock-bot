"""Запуск бота: `python -m pharmacy_bot` (настройки — в .env)."""

from __future__ import annotations

from pathlib import Path

from telegram import Update

from pharmacy_bot.bot import build_application
from pharmacy_bot.config import Settings
from pharmacy_bot.logging_setup import setup_logging
from pharmacy_bot.service import ReportService
from pharmacy_bot.sheets import SheetsExporter
from pharmacy_bot.storage import SnapshotStorage
from pharmacy_bot.yadisk import YandexDisk


def main() -> None:
    settings = Settings()
    if not settings.telegram_token:
        raise SystemExit("Задайте TELEGRAM_TOKEN в .env — пример в .env.example")
    setup_logging(Path("logs"))
    exporter = (
        SheetsExporter.from_service_account(
            str(settings.google_credentials_path),
            settings.spreadsheet_id,
            settings.worksheet_name,
            settings.priority_worksheet_name,
        )
        if settings.spreadsheet_id
        else None
    )
    disk = (
        YandexDisk(settings.yandex_oauth_token, settings.yandex_max_mb * 2**20)
        if settings.yandex_oauth_token
        else None
    )
    service = ReportService(settings, SnapshotStorage(settings.data_dir / "snapshots"), exporter, disk)
    build_application(settings, service).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
