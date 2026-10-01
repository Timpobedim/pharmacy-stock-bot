"""Загрузка выгрузок и правила отчётов."""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from conftest import CANONICAL_EXPIRY, CANONICAL_STOCK, EXPIRY_COLUMNS, STOCK_COLUMNS, TODAY, write_xlsx

from pharmacy_bot.reports.loader import TableError, load_report, prepare
from pharmacy_bot.reports.models import IlliquidRow, ReportKind
from pharmacy_bot.reports.rules import build_expiry, build_illiquid, build_priority


def stock(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    return prepare(pd.DataFrame(rows, columns=CANONICAL_STOCK), ReportKind.STOCK)


def expiry(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    return prepare(pd.DataFrame(rows, columns=CANONICAL_EXPIRY), ReportKind.EXPIRY)


def test_illiquid_groups_series_and_applies_thresholds() -> None:
    frame = stock(
        [
            ("Аптека №1", "Омепразол", "A1", 10, 2000.0, date(2026, 5, 1)),  # 133 дня без продаж
            ("Аптека №1", "Омепразол", "A2", 5, 1500.0, None),  # ни разу не продавался
            ("Аптека №1", "Ибупрофен", "B1", 3, 900.0, date(2026, 1, 1)),  # давно, но дешевле порога
            ("Аптека №2", "Омепразол", "A3", 30, 6000.0, date(2026, 9, 1)),  # продаётся
            ("Аптека №2", "Лоратадин", "C1", 40, 5000.0, "13.06.2026"),  # ровно 90 дней
        ]
    )

    rows = build_illiquid(frame, TODAY, days=90, min_sum=3000)

    assert [(row.pharmacy, row.product, row.series, row.amount) for row in rows] == [
        ("Аптека №1", "Омепразол", ("A1", "A2"), 3500.0),
        ("Аптека №2", "Лоратадин", ("C1",), 5000.0),
    ]
    assert (rows[0].quantity, rows[0].last_sale, rows[0].days_without_sales) == (15, date(2026, 5, 1), 133)
    assert rows[1].days_without_sales == 90  # дата текстом «13.06.2026» разобрана, граница включается


def test_never_sold_product_has_no_sale_date() -> None:
    frame = stock([("Аптека №1", "Мометазон", "M1", 8, 4600.0, None)])

    [row] = build_illiquid(frame, TODAY, days=90, min_sum=3000)

    assert (row.last_sale, row.days_without_sales) == (None, None)


def test_expiry_includes_expired_and_soon_sorted_by_date() -> None:
    frame = expiry(
        [
            ("Аптека №2", "Парацетамол", "S2", date(2026, 10, 11), 20, 760.0),  # через 30 дней
            ("Аптека №2", "Ибупрофен", "S3", date(2026, 10, 12), 8, 770.0),  # через 31 день — не входит
            ("Аптека №1", "Амоксициллин", "S1", "01.09.2026", 5, 900.0),  # просрочено
            ("Аптека №1", "Лоратадин", "S4", date(2026, 9, 11), 1, 54.0),  # сегодня последний день
        ]
    )

    rows = build_expiry(frame, TODAY, days=30)

    assert [(row.series, row.days_left, row.status, row.expired) for row in rows] == [
        ("S1", -10, "просрочено", True),
        ("S4", 0, "последний день", False),
        ("S2", 30, "30 дн.", False),
    ]


def test_priority_matches_pharmacy_product_and_series() -> None:
    stale = [IlliquidRow("Аптека №1", "Омепразол", ("A1", "A2"), 15, 3500.0, date(2026, 5, 1), 133)]
    soon = build_expiry(
        expiry(
            [
                ("Аптека №1", "Омепразол", "A2", date(2026, 9, 20), 5, 1500.0),
                ("Аптека №1", "Омепразол", "A9", date(2026, 9, 20), 5, 1500.0),  # другая серия
                ("Аптека №2", "Омепразол", "A1", date(2026, 9, 20), 5, 1500.0),  # другая аптека
            ]
        ),
        TODAY,
        days=30,
    )

    [row] = build_priority(stale, soon)

    assert (row.pharmacy, row.series, row.days_left, row.days_without_sales) == ("Аптека №1", "A2", 9, 133)


def test_load_report_maps_aliases_and_detects_kind(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "остатки.xlsx",
        [("  Аптека №1 ", "Омепразол", "A1", "10", "1 250,50", "01.05.2026")],
        STOCK_COLUMNS,
    )

    kind, frame = load_report(path, tmp_path)

    assert kind is ReportKind.STOCK
    record = frame.to_dict("records")[0]
    assert (record["аптека"], record["остаток"], record["сумма"]) == ("Аптека №1", 10, 1250.5)
    assert record["последняя продажа"] == pd.Timestamp(2026, 5, 1)


def test_expiry_file_with_other_column_names_is_detected(tmp_path: Path) -> None:
    path = write_xlsx(
        tmp_path / "сроки.xlsx", [("Аптека №1", "Омепразол", "A1", "20.09.2026", 5, 900)], EXPIRY_COLUMNS
    )

    kind, frame = load_report(path, tmp_path)

    assert kind is ReportKind.EXPIRY
    assert frame["срок годности"].tolist() == [pd.Timestamp(2026, 9, 20)]


def test_zip_is_unpacked_by_file_name_only(tmp_path: Path) -> None:
    inner = write_xlsx(
        tmp_path / "inner.xlsx", [("Аптека №1", "Омепразол", "A1", 1, 10, None)], STOCK_COLUMNS
    )
    archive = tmp_path / "выгрузка.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(inner, "../../выгрузка/остатки.xlsx")  # путь с «..» не должен выйти из рабочей папки
    unpack = tmp_path / "unpack"
    unpack.mkdir()

    kind, _ = load_report(archive, unpack)

    assert kind is ReportKind.STOCK
    assert (unpack / "остатки.xlsx").exists()


@pytest.mark.parametrize(
    ("name", "rows", "columns", "expected", "message"),
    [
        pytest.param("x.csv", [], STOCK_COLUMNS, None, "Нужен файл Excel", id="wrong-suffix"),
        pytest.param(
            "x.xlsx",
            [("Аптека", "Товар", "S", 1, 1)],
            STOCK_COLUMNS[:5],
            ReportKind.STOCK,
            "«последняя продажа»",
            id="missing-column",
        ),
        pytest.param(
            "x.xlsx", [("Аптека", "Товар", 1)], ["Аптека", "Товар", "Остаток"], None, "Не понял", id="unknown"
        ),
        pytest.param(
            "x.xlsx",
            [("Аптека", "Товар", "S", "31.02.2026", 1, 1)],
            EXPIRY_COLUMNS,
            None,
            "Не разобрал даты в столбце «срок годности»: «31.02.2026»",
            id="impossible-date",
        ),
    ],
)
def test_unsuitable_files_are_explained(
    tmp_path: Path,
    name: str,
    rows: list[tuple[object, ...]],
    columns: list[str],
    expected: ReportKind | None,
    message: str,
) -> None:
    path = tmp_path / name
    if name.endswith(".xlsx"):
        write_xlsx(path, rows, columns)
    else:
        path.write_text("a;b", encoding="utf-8")

    with pytest.raises(TableError, match=message):
        load_report(path, tmp_path, expected)


def test_empty_zip_is_explained(tmp_path: Path) -> None:
    archive = tmp_path / "пусто.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "нет таблиц")

    with pytest.raises(TableError, match="нет таблиц Excel"):
        load_report(archive, tmp_path)
