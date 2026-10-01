"""Снимки последних отчётов на диске.

Как в референсе: неликвид и сроки годности приходят разными файлами в разное время, а блок
«Приоритет» — их пересечение. Поэтому результат каждого отчёта сохраняется, и новый файл одного
типа сразу пересчитывает приоритет с последним файлом другого. Запись атомарная: сначала во
временный файл, потом замена — оборванная запись не испортит снимок.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pharmacy_bot.reports.models import ExpiryRow, IlliquidRow, ReportKind


@dataclass(frozen=True)
class SnapshotMeta:
    source: str
    processed_at: datetime
    rows: int


def _encode(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _decode_date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


class SnapshotStorage:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: ReportKind) -> Path:
        return self.folder / f"last_{kind.value}.json"

    def _write(self, kind: ReportKind, rows: list[Any], meta: SnapshotMeta) -> None:
        payload = {
            "meta": {key: _encode(value) for key, value in asdict(meta).items()},
            "rows": [{key: _encode(value) for key, value in asdict(row).items()} for row in rows],
        }
        target = self._path(kind)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, target)

    def _read(self, kind: ReportKind) -> tuple[list[dict[str, Any]], SnapshotMeta | None]:
        path = self._path(kind)
        if not path.exists():
            return [], None
        payload = json.loads(path.read_text(encoding="utf-8"))
        meta = payload["meta"]
        return payload["rows"], SnapshotMeta(
            source=meta["source"],
            processed_at=datetime.fromisoformat(meta["processed_at"]),
            rows=meta["rows"],
        )

    def save_illiquid(self, rows: list[IlliquidRow], source: str, processed_at: datetime) -> None:
        self._write(ReportKind.STOCK, rows, SnapshotMeta(source, processed_at, len(rows)))

    def save_expiry(self, rows: list[ExpiryRow], source: str, processed_at: datetime) -> None:
        self._write(ReportKind.EXPIRY, rows, SnapshotMeta(source, processed_at, len(rows)))

    def load_illiquid(self) -> tuple[list[IlliquidRow], SnapshotMeta | None]:
        raw, meta = self._read(ReportKind.STOCK)
        rows = [
            IlliquidRow(
                pharmacy=item["pharmacy"],
                product=item["product"],
                series=tuple(item["series"]),
                quantity=item["quantity"],
                amount=item["amount"],
                last_sale=_decode_date(item["last_sale"]),
                days_without_sales=item["days_without_sales"],
            )
            for item in raw
        ]
        return rows, meta

    def load_expiry(self) -> tuple[list[ExpiryRow], SnapshotMeta | None]:
        raw, meta = self._read(ReportKind.EXPIRY)
        rows = [
            ExpiryRow(
                pharmacy=item["pharmacy"],
                product=item["product"],
                series=item["series"],
                expires=date.fromisoformat(item["expires"]),
                days_left=item["days_left"],
                quantity=item["quantity"],
                amount=item["amount"],
            )
            for item in raw
        ]
        return rows, meta
