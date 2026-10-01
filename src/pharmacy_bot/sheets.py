"""Выгрузка в Google Sheets: два блока на одном листе и отдельный лист «Приоритет».

Раскладка повторяет референс: левый блок — неликвид (B2:H), правый — сроки годности (K2:Q),
в строке 2 заголовок блока, в строке 3 названия столбцов, строка 4 — разделитель, данные с 5-й.
В S2:T3 — актуальность каждого снимка. Числа уходят числами, даты — строками «дд.мм.гггг».
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from pharmacy_bot.reports.models import ExpiryRow, IlliquidRow, PriorityRow

ILLIQUID_TITLE = "Неликвид: товары без продаж"
ILLIQUID_COLUMNS = ["Аптека", "Товар", "Серии", "Остаток", "Сумма, ₽", "Последняя продажа", "Дней без продаж"]
EXPIRY_TITLE = "Сроки годности: истекает или истёк"
EXPIRY_COLUMNS = ["Аптека", "Товар", "Серия", "Годен до", "Осталось", "Остаток", "Сумма, ₽"]
PRIORITY_TITLE = "Приоритет: без продаж и скоро истечёт — вернуть поставщику или переместить"
PRIORITY_COLUMNS = ["Аптека", "Товар", "Серия", "Годен до", "Осталось", "Сумма, ₽", "Дней без продаж"]


class WorksheetLike(Protocol):
    """То, что нужно от листа gspread; в тестах подставляется фейк, записывающий вызовы."""

    def batch_clear(self, ranges: Sequence[str]) -> Any: ...

    def update(self, values: list[list[Any]], range_name: str) -> Any: ...

    def clear(self) -> Any: ...


def cell(value: Any) -> str | int | float:
    """Значение для Sheets API: целые — int, дробные — float, даты — строкой, пустое — пустой строкой."""
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return f"{value:%d.%m.%Y}"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return int(value) if value == int(value) else value
    return str(value)


def illiquid_matrix(rows: list[IlliquidRow]) -> list[list[str | int | float]]:
    return [
        [
            cell(v)
            for v in (
                r.pharmacy,
                r.product,
                "\n".join(r.series),
                r.quantity,
                r.amount,
                r.last_sale,
                r.days_without_sales,
            )
        ]
        for r in rows
    ]


def expiry_matrix(rows: list[ExpiryRow]) -> list[list[str | int | float]]:
    return [
        [cell(v) for v in (r.pharmacy, r.product, r.series, r.expires, r.status, r.quantity, r.amount)]
        for r in rows
    ]


def priority_matrix(rows: list[PriorityRow]) -> list[list[str | int | float]]:
    return [
        [
            cell(v)
            for v in (
                r.pharmacy,
                r.product,
                r.series,
                r.expires,
                f"{r.days_left} дн.",
                r.amount,
                r.days_without_sales,
            )
        ]
        for r in rows
    ]


class SheetsExporter:
    def __init__(
        self,
        open_worksheet: Callable[[str], WorksheetLike],
        worksheet_name: str,
        priority_worksheet_name: str,
        url: str = "",
    ) -> None:
        self._open = open_worksheet
        self.worksheet_name = worksheet_name
        self.priority_worksheet_name = priority_worksheet_name
        self.url = url

    @classmethod
    def from_service_account(
        cls, credentials_path: str, spreadsheet_id: str, worksheet_name: str, priority_worksheet_name: str
    ) -> SheetsExporter:
        import gspread  # импорт здесь: без таблицы в настройках библиотека не нужна

        spreadsheet = gspread.service_account(filename=credentials_path).open_by_key(spreadsheet_id)

        def open_worksheet(name: str) -> WorksheetLike:
            try:
                return spreadsheet.worksheet(name)
            except gspread.WorksheetNotFound:
                return spreadsheet.add_worksheet(title=name, rows=1000, cols=26)

        return cls(open_worksheet, worksheet_name, priority_worksheet_name, spreadsheet.url)

    def export(
        self,
        illiquid: list[IlliquidRow],
        expiry: list[ExpiryRow],
        priority: list[PriorityRow],
        freshness: dict[str, str],
    ) -> str:
        """freshness — подписи актуальности: {"Остатки": "11.09.2026 09:05", "Сроки": "…"}."""
        sheet = self._open(self.worksheet_name)
        sheet.batch_clear(["B2:H", "K2:Q", "S2:T3"])
        sheet.update([[ILLIQUID_TITLE], ILLIQUID_COLUMNS], range_name="B2")
        if illiquid:
            sheet.update(illiquid_matrix(illiquid), range_name="B5")
        sheet.update([[EXPIRY_TITLE], EXPIRY_COLUMNS], range_name="K2")
        if expiry:
            sheet.update(expiry_matrix(expiry), range_name="K5")
        sheet.update([[label, value] for label, value in freshness.items()], range_name="S2")

        first = self._open(self.priority_worksheet_name)
        first.clear()
        first.update([[PRIORITY_TITLE], PRIORITY_COLUMNS, *priority_matrix(priority)], range_name="A1")
        return self.url
