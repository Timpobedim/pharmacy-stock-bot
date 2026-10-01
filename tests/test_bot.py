"""Обработчики Telegram на подменённых объектах: доступ, выбор отчёта, приём файла, расписание."""

from __future__ import annotations

import shutil
from datetime import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from conftest import STOCK_COLUMNS, write_xlsx

from pharmacy_bot.bot import (
    EXPECTED,
    build_application,
    choose_stock,
    daily_report,
    on_document,
    start,
    status_command,
)
from pharmacy_bot.config import Settings
from pharmacy_bot.reports.models import ReportKind
from pharmacy_bot.service import Outcome, ReportService, ServiceError


def make_update(user_id: int, document: Any = None) -> tuple[MagicMock, MagicMock]:
    update = MagicMock()
    update.effective_user.id = user_id
    message = MagicMock()
    message.reply_text = AsyncMock()
    message.reply_document = AsyncMock()
    message.document = document
    update.effective_message = message
    return update, message


def make_context(settings: Settings, service: ReportService) -> MagicMock:
    context = MagicMock()
    context.bot_data = {"settings": settings, "service": service}
    context.user_data = {}
    context.bot.send_message = AsyncMock()
    context.bot.send_document = AsyncMock()
    return context


def replies(message: MagicMock) -> list[str]:
    return [call.args[0] for call in message.reply_text.await_args_list]


async def test_stranger_gets_id_for_administrator(settings: Settings, service: ReportService) -> None:
    update, message = make_update(user_id=99)

    await start(update, make_context(settings, service))

    assert replies(message) == [
        "Бот доступен только сотрудникам сети. Ваш ID: 99 — передайте его администратору."
    ]


async def test_document_after_choice_delivers_summary_and_report(
    settings: Settings, service: ReportService, tmp_path: Path
) -> None:
    source = write_xlsx(
        tmp_path / "src.xlsx", [("Аптека №1", "Омепразол", "A1", 10, 4000, "01.05.2026")], STOCK_COLUMNS
    )
    context = make_context(settings, service)
    update, _ = make_update(user_id=1)
    await choose_stock(update, context)
    assert context.user_data[EXPECTED] is ReportKind.STOCK

    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock(side_effect=lambda target: shutil.copy(source, target))
    document = MagicMock(file_name="остатки 11.09.xlsx", file_size=2048)
    document.get_file = AsyncMock(return_value=telegram_file)
    update, message = make_update(user_id=1, document=document)

    await on_document(update, context)

    texts = replies(message)
    assert texts[0] == "Обрабатываю…"
    assert texts[1].startswith("<b>Неликвид</b> по файлу «остатки 11.09.xlsx»")
    message.reply_document.assert_awaited_once()
    assert EXPECTED not in context.user_data
    assert not [path for path in service.workdir.iterdir() if path.is_file()]


async def test_unsuitable_documents_are_refused_before_download(
    settings: Settings, service: ReportService
) -> None:
    context = make_context(settings, service)
    for document, expected in [
        (MagicMock(file_name="фото.jpg", file_size=10), "Нужен файл Excel"),
        (MagicMock(file_name="огромный.xlsx", file_size=50 * 2**20), "больше 20 МБ"),
    ]:
        document.get_file = AsyncMock()
        update, message = make_update(user_id=1, document=document)

        await on_document(update, context)

        assert expected in replies(message)[0]
        document.get_file.assert_not_awaited()


async def test_status_is_for_admins_only(settings: Settings, service: ReportService) -> None:
    context = make_context(settings, service)
    staff, staff_message = make_update(user_id=1)
    admin, admin_message = make_update(user_id=7)

    await status_command(staff, context)
    await status_command(admin, context)

    assert replies(staff_message) == ["Команда только для администраторов."]
    assert replies(admin_message)[0].startswith("Остатки: нет данных")


async def test_daily_report_posts_results_and_problems(
    settings: Settings, service: ReportService, tmp_path: Path
) -> None:
    settings.report_chat_id = 42
    report = tmp_path / "отчёт.xlsx"
    report.write_bytes(b"xlsx")
    outcome = Outcome(ReportKind.STOCK, "Я.Диск: остатки.xlsx", "<b>Неликвид</b>…", report, None)
    service.process_latest = AsyncMock(
        side_effect=[outcome, ServiceError("В папке нет файлов Excel или zip")]
    )  # type: ignore[method-assign]
    context = make_context(settings, service)

    await daily_report(context)

    messages = [call.args[1] for call in context.bot.send_message.await_args_list]
    assert messages == [
        "<b>Неликвид</b>…",
        "Отчёт «Сроки годности» не построен: В папке нет файлов Excel или zip",
    ]
    context.bot.send_document.assert_awaited_once()


def test_application_registers_handlers_and_daily_job(settings: Settings, service: ReportService) -> None:
    settings.telegram_token = "123456:TEST"
    settings.daily_report_time = time(9, 0)
    settings.report_chat_id = 42

    application = build_application(settings, service)

    assert application.job_queue is not None
    assert len(application.job_queue.get_jobs_by_name("daily-report")) == 1
    assert sum(len(group) for group in application.handlers.values()) == 8
