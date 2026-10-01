"""Те же отчёты без Telegram — для проверки на своих выгрузках и для запуска из планировщика.

python -m pharmacy_bot.report_cli --stock examples/остатки.xlsx --expiry "examples/сроки годности.xlsx"
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, datetime
from pathlib import Path

from pharmacy_bot.config import Settings
from pharmacy_bot.service import ReportService
from pharmacy_bot.storage import SnapshotStorage


def _plain(html: str) -> str:
    return re.sub(r"</?b>", "", html)


async def _run(args: argparse.Namespace) -> None:
    settings = Settings(_env_file=None, data_dir=args.out)  # type: ignore[call-arg]  # локальный .env не читаем
    today = args.today
    now = (lambda: datetime.combine(today, datetime.min.time())) if today else None
    service = ReportService(settings, SnapshotStorage(args.out / "snapshots"), now=now)
    for path in (args.stock, args.expiry):
        if path is None:
            continue
        outcome = await service.process_file(path, path.name)
        print(_plain(outcome.text), end="\n\n")
        print(f"Excel: {outcome.report_path}", end="\n\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Отчёты аптечной сети без Telegram")
    parser.add_argument("--stock", type=Path, help="выгрузка остатков")
    parser.add_argument("--expiry", type=Path, help="выгрузка сроков годности")
    parser.add_argument("--out", type=Path, default=Path("output"))
    parser.add_argument(
        "--today", type=date.fromisoformat, help="дата отчёта ГГГГ-ММ-ДД (по умолчанию — сегодня)"
    )
    asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
