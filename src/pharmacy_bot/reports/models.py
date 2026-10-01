from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ReportKind(StrEnum):
    STOCK = "stock"  # выгрузка остатков → неликвид
    EXPIRY = "expiry"  # выгрузка сроков годности → истекающие сроки

    @property
    def label(self) -> str:
        return "Неликвид" if self is ReportKind.STOCK else "Сроки годности"


@dataclass(frozen=True)
class IlliquidRow:
    """Товар аптеки без продаж дольше порога. Серии — одной ячейкой через перенос строки, как в референсе."""

    pharmacy: str
    product: str
    series: tuple[str, ...]
    quantity: int
    amount: float
    last_sale: date | None  # None — не продавался ни разу
    days_without_sales: int | None


@dataclass(frozen=True)
class ExpiryRow:
    pharmacy: str
    product: str
    series: str
    expires: date
    days_left: int
    quantity: int
    amount: float

    @property
    def expired(self) -> bool:
        # «Годен до 11.09» — 11.09 товар ещё можно продать, просрочен он с 12.09.
        return self.days_left < 0

    @property
    def status(self) -> str:
        if self.expired:
            return "просрочено"
        return "последний день" if self.days_left == 0 else f"{self.days_left} дн."


@dataclass(frozen=True)
class PriorityRow:
    """Серия, которая и не продаётся, и скоро истечёт: вернуть поставщику или переместить в первую очередь."""

    pharmacy: str
    product: str
    series: str
    expires: date
    days_left: int
    amount: float
    days_without_sales: int | None
