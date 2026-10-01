"""Telegram-интерфейс на python-telegram-bot: кнопки отчётов, приём файлов, Яндекс Диск, расписание."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from pharmacy_bot import texts
from pharmacy_bot.config import Settings
from pharmacy_bot.reports.loader import TableError
from pharmacy_bot.reports.models import ReportKind
from pharmacy_bot.service import INPUT_SUFFIXES, BusyError, Outcome, ReportService, ServiceError
from pharmacy_bot.yadisk import YandexDiskError

logger = logging.getLogger(__name__)

KEYBOARD = ReplyKeyboardMarkup(
    [[texts.BTN_STOCK, texts.BTN_EXPIRY], [texts.BTN_DISK, texts.BTN_HELP]], resize_keyboard=True
)
EXPECTED = "expected_kind"
TELEGRAM_FILE_LIMIT = 20 * 1024 * 1024  # больше Bot API скачать не даёт

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]


def _service(context: ContextTypes.DEFAULT_TYPE) -> ReportService:
    service: ReportService = context.bot_data["service"]
    return service


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings: Settings = context.bot_data["settings"]
    return settings


def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[Any, Any]:
    return context.user_data if context.user_data is not None else {}


def staff_only(handler: Handler) -> Handler:
    """Доступ только сотрудникам из ALLOWED_USER_IDS и администраторам."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is not None and _settings(context).can_use(user.id):
            await handler(update, context)
            return
        if update.effective_message is not None:
            user_id = user.id if user is not None else "—"
            await update.effective_message.reply_text(texts.NO_ACCESS.format(user_id=user_id))

    return wrapper


async def _deliver(update: Update, context: ContextTypes.DEFAULT_TYPE, job: Awaitable[Outcome]) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text("Обрабатываю…")
    try:
        outcome = await job
    except BusyError as exc:
        await message.reply_text(str(exc))
        return
    except (TableError, ServiceError, YandexDiskError) as exc:
        await message.reply_text(f"Не получилось: {exc}")
        return
    await message.reply_text(outcome.text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    with outcome.report_path.open("rb") as report:
        await message.reply_document(report, filename=outcome.report_path.name)
    if outcome.sheet_url:
        await message.reply_text(
            f"Google-таблица обновлена: {outcome.sheet_url}", disable_web_page_preview=True
        )
    elif outcome.sheet_error:
        await message.reply_text(outcome.sheet_error)
    _user_data(context).pop(EXPECTED, None)


@staff_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text(texts.START, reply_markup=KEYBOARD)


@staff_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text(texts.HELP, reply_markup=KEYBOARD)


def _chooser(kind: ReportKind, prompt: str) -> Handler:
    @staff_only
    async def choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _user_data(context)[EXPECTED] = kind
        if update.effective_message is not None:
            await update.effective_message.reply_text(prompt)

    return choose


choose_stock = _chooser(
    ReportKind.STOCK,
    "Пришлите выгрузку остатков (.xlsx, .xls или zip до 20 МБ) или нажмите «☁️ Взять с Я.Диска».",
)
choose_expiry = _chooser(
    ReportKind.EXPIRY,
    "Пришлите выгрузку сроков годности (.xlsx, .xls или zip до 20 МБ) или нажмите «☁️ Взять с Я.Диска».",
)


@staff_only
async def from_disk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    kind = _user_data(context).get(EXPECTED)
    if kind is None:
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                "Сначала выберите отчёт: «📦 Неликвид» или «⏳ Сроки годности»."
            )
        return
    await _deliver(update, context, _service(context).process_latest(kind))


@staff_only
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.document is None:
        return
    document = message.document
    name = document.file_name or "file.xlsx"
    if Path(name).suffix.lower() not in INPUT_SUFFIXES:
        await message.reply_text("Нужен файл Excel (.xlsx, .xls) или zip с таблицей внутри.")
        return
    if document.file_size and document.file_size > TELEGRAM_FILE_LIMIT:
        await message.reply_text(
            "Файл больше 20 МБ: положите его на Яндекс Диск и нажмите «☁️ Взять с Я.Диска»."
        )
        return
    service = _service(context)
    service.workdir.mkdir(parents=True, exist_ok=True)
    target = service.workdir / f"tg_{uuid.uuid4().hex[:8]}{Path(name).suffix.lower()}"
    try:
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(target)
        await _deliver(update, context, service.process_file(target, name, _user_data(context).get(EXPECTED)))
    finally:
        target.unlink(missing_ok=True)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if message is None:
        return
    if user is None or not _settings(context).is_admin(user.id):
        await message.reply_text("Команда только для администраторов.")
        return
    await message.reply_text(_service(context).status())


async def daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ежедневная задача: свежие файлы с Яндекс Диска → сводки и отчёт в рабочий чат."""
    settings, service = _settings(context), _service(context)
    if settings.report_chat_id is None:
        return
    for kind in (ReportKind.STOCK, ReportKind.EXPIRY):
        try:
            outcome = await service.process_latest(kind)
        except (TableError, ServiceError, YandexDiskError, BusyError) as exc:
            await context.bot.send_message(
                settings.report_chat_id, f"Отчёт «{kind.label}» не построен: {exc}"
            )
            continue
        await context.bot.send_message(settings.report_chat_id, outcome.text, parse_mode=ParseMode.HTML)
        with outcome.report_path.open("rb") as report:
            await context.bot.send_document(
                settings.report_chat_id, report, filename=outcome.report_path.name
            )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Необработанная ошибка в обработчике", exc_info=context.error)


def build_application(
    settings: Settings, service: ReportService
) -> Application[Any, Any, Any, Any, Any, Any]:
    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data.update(service=service, settings=settings)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.Text([texts.BTN_STOCK]), choose_stock))
    application.add_handler(MessageHandler(filters.Text([texts.BTN_EXPIRY]), choose_expiry))
    application.add_handler(MessageHandler(filters.Text([texts.BTN_DISK]), from_disk))
    application.add_handler(MessageHandler(filters.Text([texts.BTN_HELP]), help_command))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_error_handler(on_error)
    if settings.daily_report_time and settings.report_chat_id and application.job_queue is not None:
        run_at = settings.daily_report_time.replace(tzinfo=ZoneInfo(settings.timezone))
        application.job_queue.run_daily(daily_report, time=run_at, name="daily-report")
    return application
