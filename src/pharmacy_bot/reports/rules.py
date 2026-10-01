"""Правила отчётов: неликвид, истекающие сроки и их пересечение.

Даты в таблицах — datetime64 (NaT у остатков значит «ни разу не продавался»), в строки отчёта
попадают обычные `date`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from pharmacy_bot.reports.models import ExpiryRow, IlliquidRow, PriorityRow


def _day(value: Any) -> date | None:
    return None if pd.isna(value) else pd.Timestamp(value).date()


def _series(values: pd.Series) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if pd.notna(value)}))


def build_illiquid(frame: pd.DataFrame, today: date, days: int, min_sum: float) -> list[IlliquidRow]:
    """Товары без продаж `days` дней и дольше (или ни разу не продававшиеся) дороже `min_sum` по аптеке.

    Как в референсе: строки группируются, серии собираются в одну ячейку, группа проходит порог по сумме.
    """
    border = pd.Timestamp(today - timedelta(days=days))
    last_sale = frame["последняя продажа"]
    stale = frame[last_sale.isna() | (last_sale <= border)]
    if stale.empty:
        return []
    grouped = (
        stale.groupby(["аптека", "товар"], as_index=False)
        .agg(
            series=("серия", _series),
            quantity=("остаток", "sum"),
            amount=("сумма", "sum"),
            last_sale=("последняя продажа", "max"),
        )
        .sort_values(["аптека", "amount"], ascending=[True, False])
    )
    rows = []
    for record in grouped.to_dict("records"):
        if record["amount"] <= min_sum:
            continue
        sold = _day(record["last_sale"])
        rows.append(
            IlliquidRow(
                pharmacy=str(record["аптека"]),
                product=str(record["товар"]),
                series=tuple(record["series"]),
                quantity=int(record["quantity"]),
                amount=round(float(record["amount"]), 2),
                last_sale=sold,
                days_without_sales=None if sold is None else (today - sold).days,
            )
        )
    return rows


def build_expiry(frame: pd.DataFrame, today: date, days: int) -> list[ExpiryRow]:
    """Серии, у которых срок годности истекает в ближайшие `days` дней или уже истёк."""
    border = pd.Timestamp(today + timedelta(days=days))
    soon = frame[frame["срок годности"] <= border].sort_values(["срок годности", "аптека", "товар"])
    rows = []
    for record in soon.to_dict("records"):
        expires = pd.Timestamp(record["срок годности"]).date()
        rows.append(
            ExpiryRow(
                pharmacy=str(record["аптека"]),
                product=str(record["товар"]),
                series=str(record["серия"]),
                expires=expires,
                days_left=(expires - today).days,
                quantity=int(record["остаток"]),
                amount=round(float(record["сумма"]), 2),
            )
        )
    return rows


def build_priority(illiquid: list[IlliquidRow], expiry: list[ExpiryRow]) -> list[PriorityRow]:
    """Пересечение, как «24 часа × карта тары» в референсе: серия не продаётся и скоро истечёт."""
    stale = {(row.pharmacy, row.product): row for row in illiquid}
    rows = []
    for item in expiry:
        match = stale.get((item.pharmacy, item.product))
        if match is not None and item.series in match.series:
            rows.append(
                PriorityRow(
                    pharmacy=item.pharmacy,
                    product=item.product,
                    series=item.series,
                    expires=item.expires,
                    days_left=item.days_left,
                    amount=item.amount,
                    days_without_sales=match.days_without_sales,
                )
            )
    return rows
