"""Тексты бота. Сводки — в HTML-разметке Telegram, всё, что пришло из файлов, экранируется."""

from __future__ import annotations

from collections import defaultdict
from html import escape

from pharmacy_bot.reports.models import ExpiryRow, IlliquidRow, PriorityRow, ReportKind

BTN_STOCK = "📦 Неликвид"
BTN_EXPIRY = "⏳ Сроки годности"
BTN_DISK = "☁️ Взять с Я.Диска"
BTN_HELP = "❓ Помощь"

START = (
    "Здравствуйте! Я собираю два ежедневных отчёта аптечной сети:\n"
    f"{BTN_STOCK} — товары без продаж, которые замораживают деньги;\n"
    f"{BTN_EXPIRY} — серии, у которых истекает или истёк срок годности.\n\n"
    "Выберите отчёт и пришлите выгрузку из учётной системы или возьмите последний файл с Яндекс Диска."
)

HELP = (
    "Как пользоваться:\n"
    "1. Нажмите нужный отчёт.\n"
    "2. Пришлите файл .xlsx, .xls или zip до 20 МБ — или нажмите «☁️ Взять с Я.Диска».\n"
    "3. Получите сводку, Excel-отчёт и обновлённую Google-таблицу.\n\n"
    "Файл можно прислать и без выбора отчёта: тип определится по столбцам.\n"
    "Лист «Приоритет» появляется, когда есть обе выгрузки: там серии, которые не продаются и скоро истекут."
)

NO_ACCESS = "Бот доступен только сотрудникам сети. Ваш ID: {user_id} — передайте его администратору."


def plural(count: int, forms: tuple[str, str, str]) -> str:
    """plural(1, ("серия", "серии", "серий")) → «1 серия», 3 → «3 серии», 11 → «11 серий»."""
    tail = count % 100
    if 11 <= tail <= 14:
        form = forms[2]
    elif count % 10 == 1:
        form = forms[0]
    elif 2 <= count % 10 <= 4:
        form = forms[1]
    else:
        form = forms[2]
    return f"{count} {form}"


SERIES = ("серия", "серии", "серий")
POSITIONS = ("позиция", "позиции", "позиций")


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def in_pharmacies(count: int) -> str:
    return "в 1 аптеке" if count == 1 else f"в {count} аптеках"


def _top_pharmacies(rows: list[IlliquidRow], limit: int = 3) -> str:
    totals: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        totals[row.pharmacy] += row.amount
    top = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    return "; ".join(f"{escape(name)} — {money(amount)}" for name, amount in top)


def stock_summary(source: str, rows: list[IlliquidRow], days: int) -> str:
    if not rows:
        return (
            f"<b>Неликвид</b> по файлу «{escape(source)}»: товаров без продаж {days}+ дней выше порога нет."
        )
    positions = plural(len(rows), POSITIONS)
    total = money(sum(row.amount for row in rows))
    where = in_pharmacies(len({row.pharmacy for row in rows}))
    return (
        f"<b>Неликвид</b> по файлу «{escape(source)}»\n"
        f"Без продаж {days}+ дней: {positions} на {total} {where}.\n"
        f"Больше всего: {_top_pharmacies(rows)}."
    )


def expiry_summary(source: str, rows: list[ExpiryRow], days: int) -> str:
    expired = [row for row in rows if row.expired]
    soon = [row for row in rows if not row.expired]
    lines = [f"<b>Сроки годности</b> по файлу «{escape(source)}»"]
    if expired:
        amount = money(sum(row.amount for row in expired))
        lines.append(f"⚠️ Просрочено: {plural(len(expired), SERIES)} на {amount} — снять с продажи.")
    soon_amount = money(sum(row.amount for row in soon))
    lines.append(f"Истекает в ближайшие {days} дней: {plural(len(soon), SERIES)} на {soon_amount}.")
    return "\n".join(lines)


def priority_summary(rows: list[PriorityRow], complete: bool) -> str:
    if not complete:
        return "Приоритет посчитается, когда будут обе выгрузки — остатки и сроки годности."
    if not rows:
        return "Приоритет: среди товаров без продаж нет серий с истекающим сроком."
    return (
        f"Приоритет: {plural(len(rows), SERIES)} без продаж с истекающим сроком "
        f"на {money(sum(row.amount for row in rows))} — вернуть поставщику или переместить."
    )


def summary(
    kind: ReportKind,
    source: str,
    illiquid: list[IlliquidRow],
    expiry: list[ExpiryRow],
    priority: list[PriorityRow],
    *,
    complete: bool,
    days: int,
) -> str:
    head = (
        stock_summary(source, illiquid, days)
        if kind is ReportKind.STOCK
        else expiry_summary(source, expiry, days)
    )
    return f"{head}\n\n{priority_summary(priority, complete)}"
