"""Обработка выгрузки — общая для кнопок бота, ежедневной задачи и командной строки."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pharmacy_bot.config import Settings
from pharmacy_bot.excel_export import write_report
from pharmacy_bot.reports.loader import TABLE_SUFFIXES, load_report
from pharmacy_bot.reports.models import ReportKind
from pharmacy_bot.reports.rules import build_expiry, build_illiquid, build_priority
from pharmacy_bot.sheets import SheetsExporter
from pharmacy_bot.storage import SnapshotMeta, SnapshotStorage
from pharmacy_bot.texts import summary
from pharmacy_bot.yadisk import YandexDisk

logger = logging.getLogger(__name__)

INPUT_SUFFIXES = (*TABLE_SUFFIXES, ".zip")


class BusyError(Exception):
    """Одна обработка за раз: второй файл во время первой получает понятный отказ, а не гонку за снимки."""


class ServiceError(Exception):
    """Понятная пользователю ошибка настроек или источника."""


@dataclass(frozen=True)
class Outcome:
    kind: ReportKind
    source: str
    text: str  # HTML-сводка для Telegram
    report_path: Path
    sheet_url: str | None
    sheet_error: str | None = None


def _freshness(meta: SnapshotMeta | None) -> str:
    return "нет данных" if meta is None else f"{meta.processed_at:%d.%m.%Y %H:%M} — {meta.source}"


class ReportService:
    def __init__(
        self,
        settings: Settings,
        storage: SnapshotStorage,
        exporter: SheetsExporter | None = None,
        disk: YandexDisk | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.exporter = exporter
        self.disk = disk
        self._now = now or (lambda: datetime.now(ZoneInfo(settings.timezone)))
        self._lock = asyncio.Lock()
        self.workdir = settings.data_dir / "tmp"
        self.reports_dir = settings.data_dir / "reports"

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def process_file(self, path: Path, source: str, expected: ReportKind | None = None) -> Outcome:
        if self._lock.locked():
            raise BusyError("Уже обрабатываю другой файл — пришлите этот через минуту")
        async with self._lock:
            return await asyncio.to_thread(self._process, path, source, expected)

    async def process_latest(self, kind: ReportKind) -> Outcome:
        """Последний файл из папки Яндекс Диска для этого отчёта."""
        if self.disk is None:
            raise ServiceError("Яндекс Диск не настроен: задайте YANDEX_OAUTH_TOKEN в .env")
        if self.busy:
            raise BusyError("Уже обрабатываю другой файл — попробуйте через минуту")
        folder = (
            self.settings.yandex_stock_dir if kind is ReportKind.STOCK else self.settings.yandex_expiry_dir
        )
        file = await self.disk.latest_file(folder, INPUT_SUFFIXES)
        if file is None:
            raise ServiceError(f"В папке {folder} нет файлов Excel или zip")
        self.workdir.mkdir(parents=True, exist_ok=True)
        target = self.workdir / f"disk_{uuid.uuid4().hex[:8]}{Path(file.name).suffix.lower()}"
        try:
            await self.disk.download(file, target)
            return await self.process_file(target, f"Я.Диск: {file.name}", kind)
        finally:
            target.unlink(missing_ok=True)

    def _process(self, path: Path, source: str, expected: ReportKind | None) -> Outcome:
        settings = self.settings
        now = self._now()
        self.workdir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.workdir) as unpack_dir:
            kind, frame = load_report(path, Path(unpack_dir), expected)
        if kind is ReportKind.STOCK:
            self.storage.save_illiquid(
                build_illiquid(frame, now.date(), settings.illiquid_days, settings.illiquid_min_sum),
                source,
                now,
            )
        else:
            self.storage.save_expiry(build_expiry(frame, now.date(), settings.expiry_days), source, now)
        logger.info("Обработан файл %s как %s", source, kind.value)

        illiquid, stock_meta = self.storage.load_illiquid()
        expiry, expiry_meta = self.storage.load_expiry()
        priority = build_priority(illiquid, expiry)
        report = write_report(
            self.reports_dir / f"Отчёт аптек {now:%Y-%m-%d %H-%M-%S}.xlsx", illiquid, expiry, priority
        )

        sheet_url, sheet_error = None, None
        if self.exporter is not None:
            try:
                sheet_url = self.exporter.export(
                    illiquid,
                    expiry,
                    priority,
                    {"Остатки:": _freshness(stock_meta), "Сроки:": _freshness(expiry_meta)},
                )
            except Exception as exc:  # граница интеграции: сбой Google не должен терять готовый отчёт
                logger.exception("Не удалось обновить Google-таблицу")
                sheet_error = f"Google-таблица не обновлена: {exc}"

        days = settings.illiquid_days if kind is ReportKind.STOCK else settings.expiry_days
        text = summary(
            kind,
            source,
            illiquid,
            expiry,
            priority,
            complete=stock_meta is not None and expiry_meta is not None,
            days=days,
        )
        return Outcome(kind, source, text, report, sheet_url, sheet_error)

    def status(self) -> str:
        _, stock_meta = self.storage.load_illiquid()
        _, expiry_meta = self.storage.load_expiry()
        return (
            f"Остатки: {_freshness(stock_meta)}\n"
            f"Сроки годности: {_freshness(expiry_meta)}\n"
            f"Сейчас {'идёт обработка' if self.busy else 'свободен'}; "
            f"Яндекс Диск {'подключён' if self.disk else 'не настроен'}; "
            f"Google-таблица {'подключена' if self.exporter else 'не настроена'}."
        )
