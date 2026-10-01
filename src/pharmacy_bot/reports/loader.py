"""Чтение выгрузок учётной системы: .xlsx, .xls или zip с таблицей внутри.

Столбцы в выгрузках разных аптек называются по-разному («Сумма, руб», «Сумма закупки»), поэтому
заголовки приводятся к каноническим именам по словарю синонимов.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from pharmacy_bot.reports.models import ReportKind

TABLE_SUFFIXES = (".xlsx", ".xls")

ALIASES: dict[str, set[str]] = {
    "аптека": {"аптека", "подразделение", "точка продаж"},
    "товар": {"товар", "наименование", "номенклатура"},
    "серия": {"серия", "партия"},
    "остаток": {"остаток", "остаток, шт", "количество", "кол-во"},
    "сумма": {"сумма", "сумма, руб", "сумма, ₽", "сумма закупки"},
    "последняя продажа": {"последняя продажа", "дата последней продажи"},
    "срок годности": {"срок годности", "годен до"},
}

REQUIRED: dict[ReportKind, list[str]] = {
    ReportKind.STOCK: ["аптека", "товар", "серия", "остаток", "сумма", "последняя продажа"],
    ReportKind.EXPIRY: ["аптека", "товар", "серия", "срок годности", "остаток", "сумма"],
}


class TableError(Exception):
    """Файл не подходит: не та выгрузка, нет нужных столбцов, пустой архив."""


def _normalize(text: object) -> str:
    return " ".join(str(text).replace("ё", "е").split()).casefold()


def extract_table(path: Path, workdir: Path) -> Path:
    """Из zip берётся первая таблица. Имя файла внутри архива не используется как путь — только basename."""
    if path.suffix.lower() != ".zip":
        return path
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if Path(name).suffix.lower() in TABLE_SUFFIXES]
        if not members:
            raise TableError("В архиве нет таблиц Excel (.xlsx или .xls)")
        target = workdir / Path(members[0]).name
        target.write_bytes(archive.read(members[0]))
    return target


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() not in TABLE_SUFFIXES:
        raise TableError(f"Нужен файл Excel (.xlsx, .xls) или zip, а пришёл «{path.name}»")
    try:
        frame = pd.read_excel(path, dtype=object)
    except ValueError as exc:
        raise TableError(f"Не удалось прочитать «{path.name}»: {exc}") from exc
    lookup = {alias: canonical for canonical, aliases in ALIASES.items() for alias in aliases}
    renamed = {column: lookup.get(_normalize(column), _normalize(column)) for column in frame.columns}
    return frame.rename(columns=renamed).dropna(how="all")


def detect_kind(frame: pd.DataFrame) -> ReportKind | None:
    if "срок годности" in frame.columns:
        return ReportKind.EXPIRY
    if "последняя продажа" in frame.columns:
        return ReportKind.STOCK
    return None


def prepare(frame: pd.DataFrame, kind: ReportKind) -> pd.DataFrame:
    """Проверяет столбцы и приводит типы: числа с запятой, даты «дд.мм.гггг», пустые строки отбрасываются."""
    missing = [column for column in REQUIRED[kind] if column not in frame.columns]
    if missing:
        raise TableError(
            f"Это не выгрузка «{kind.label}»: нет столбцов " + ", ".join(f"«{name}»" for name in missing)
        )
    result = frame[REQUIRED[kind]].copy()
    for column in ("аптека", "товар", "серия"):
        result[column] = result[column].astype("string").str.strip()
    result = result.dropna(subset=["аптека", "товар"])
    for column in ("остаток", "сумма"):
        text = (
            result[column]
            .astype("string")
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        result[column] = pd.to_numeric(text, errors="coerce").fillna(0)
    date_column = "последняя продажа" if kind is ReportKind.STOCK else "срок годности"
    # В одном столбце бывают и ячейки-даты Excel, и текст «дд.мм.гггг» — каждая разбирается отдельно.
    parsed = pd.to_datetime(result[date_column], errors="coerce", dayfirst=True, format="mixed")
    text = result[date_column].astype("string").str.strip().fillna("")
    unreadable = text[parsed.isna() & (text != "")]
    if not unreadable.empty:
        # Молча превратить такую дату в пустую нельзя: товар попадёт в «ни разу не продавался»,
        # а серия со сроком годности выпадет из отчёта.
        examples = ", ".join(f"«{value}»" for value in unreadable.head(3))
        raise TableError(f"Не разобрал даты в столбце «{date_column}»: {examples} — нужен формат дд.мм.гггг")
    result[date_column] = parsed.dt.normalize()
    if kind is ReportKind.EXPIRY:
        result = result.dropna(subset=["срок годности"])
    return result


def load_report(
    path: Path, workdir: Path, expected: ReportKind | None = None
) -> tuple[ReportKind, pd.DataFrame]:
    """Если тип не задан кнопкой, он определяется по столбцам файла."""
    frame = read_table(extract_table(path, workdir))
    kind = expected or detect_kind(frame)
    if kind is None:
        raise TableError(
            "Не понял, что это за выгрузка: нужен столбец «Срок годности» или «Последняя продажа»"
        )
    return kind, prepare(frame, kind)
