"""Снимки на диске и раскладка выгрузки в Google Sheets."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW

from pharmacy_bot.reports.models import ExpiryRow, IlliquidRow, PriorityRow
from pharmacy_bot.sheets import EXPIRY_TITLE, ILLIQUID_TITLE, PRIORITY_TITLE, SheetsExporter, cell
from pharmacy_bot.storage import SnapshotStorage

STALE = IlliquidRow("Аптека №1", "Омепразол", ("A1", "A2"), 15, 3500.0, date(2026, 5, 1), 133)
NEVER_SOLD = IlliquidRow("Аптека №2", "Мометазон", ("M1",), 8, 4600.5, None, None)
SOON = ExpiryRow("Аптека №1", "Омепразол", "A2", date(2026, 9, 1), -10, 5, 1500.0)
FIRST = PriorityRow("Аптека №1", "Омепразол", "A2", date(2026, 9, 1), -10, 1500.0, 133)


def test_snapshots_round_trip_without_temporary_files(tmp_path: Path) -> None:
    storage = SnapshotStorage(tmp_path)

    storage.save_illiquid([STALE, NEVER_SOLD], "остатки.xlsx", NOW)
    storage.save_expiry([SOON], "сроки.xlsx", NOW)

    illiquid, stock_meta = storage.load_illiquid()
    expiry, expiry_meta = storage.load_expiry()
    assert illiquid == [STALE, NEVER_SOLD]
    assert expiry == [SOON]
    assert stock_meta is not None
    assert (stock_meta.source, stock_meta.processed_at, stock_meta.rows) == ("остатки.xlsx", NOW, 2)
    assert expiry_meta is not None
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_snapshot_is_empty(tmp_path: Path) -> None:
    assert SnapshotStorage(tmp_path).load_expiry() == ([], None)


class FakeWorksheet:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def batch_clear(self, ranges: Sequence[str]) -> None:
        self.calls.append(("batch_clear", list(ranges)))

    def update(self, values: list[list[Any]], range_name: str) -> None:
        self.calls.append(("update", range_name, values))

    def clear(self) -> None:
        self.calls.append(("clear",))


def test_export_writes_two_blocks_and_priority_sheet() -> None:
    sheets = {"Отчёт аптек": FakeWorksheet(), "Приоритет": FakeWorksheet()}
    exporter = SheetsExporter(sheets.__getitem__, "Отчёт аптек", "Приоритет", url="https://docs.google.com/x")

    url = exporter.export([STALE], [SOON], [FIRST], {"Остатки:": "11.09.2026 09:30 — остатки.xlsx"})

    assert url == "https://docs.google.com/x"
    main = sheets["Отчёт аптек"].calls
    assert main[0] == ("batch_clear", ["B2:H", "K2:Q", "S2:T3"])
    updates = {call[1]: call[2] for call in main if call[0] == "update"}
    assert updates["B2"][0] == [ILLIQUID_TITLE]
    assert updates["B5"] == [["Аптека №1", "Омепразол", "A1\nA2", 15, 3500, "01.05.2026", 133]]
    assert updates["K2"][0] == [EXPIRY_TITLE]
    assert updates["K5"] == [["Аптека №1", "Омепразол", "A2", "01.09.2026", "просрочено", 5, 1500]]
    assert updates["S2"] == [["Остатки:", "11.09.2026 09:30 — остатки.xlsx"]]
    priority = sheets["Приоритет"].calls
    assert priority[0] == ("clear",)
    assert priority[1][2][0] == [PRIORITY_TITLE]
    assert priority[1][2][2] == ["Аптека №1", "Омепразол", "A2", "01.09.2026", "-10 дн.", 1500, 133]


def test_empty_blocks_write_only_headers() -> None:
    sheets = {"Отчёт аптек": FakeWorksheet(), "Приоритет": FakeWorksheet()}

    SheetsExporter(sheets.__getitem__, "Отчёт аптек", "Приоритет").export([], [], [], {})

    ranges = [call[1] for call in sheets["Отчёт аптек"].calls if call[0] == "update"]
    assert ranges == ["B2", "K2", "S2"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (2.0, 2),
        (2.5, 2.5),
        (float("nan"), ""),
        (date(2026, 9, 1), "01.09.2026"),
        (True, "True"),
        ("текст", "текст"),
    ],
)
def test_cell_values_for_sheets_api(value: object, expected: object) -> None:
    assert cell(value) == expected
