"""Демо-выгрузки: остатки и сроки годности пяти аптек, как их отдаёт учётная система.

    python -m pharmacy_bot.demo examples

Аптеки и адреса вымышленные, препараты — по международным непатентованным наименованиям.
Генератор детерминированный (seed), поэтому тесты и README опираются на одни и те же цифры.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

TODAY = date(2026, 9, 11)

PHARMACIES = [
    "Аптека №1, ул. Ленина, 10",
    "Аптека №2, пр. Мира, 45",
    "Аптека №3, ул. Садовая, 7",
    "Аптека №4, ТЦ «Галерея»",
    "Аптека №5, ул. Гагарина, 21",
]

PRODUCTS = [
    ("Парацетамол таб. 500 мг №20", 38),
    ("Ибупрофен таб. п/о 200 мг №50", 96),
    ("Лоратадин таб. 10 мг №10", 54),
    ("Амброксол сироп 15 мг/5 мл 100 мл", 118),
    ("Омепразол капс. 20 мг №30", 142),
    ("Цетиризин таб. 10 мг №20", 131),
    ("Панкреатин таб. 25 ЕД №60", 89),
    ("Лоперамид капс. 2 мг №20", 64),
    ("Метформин таб. 500 мг №60", 157),
    ("Аторвастатин таб. 20 мг №30", 318),
    ("Эналаприл таб. 10 мг №20", 72),
    ("Амлодипин таб. 5 мг №30", 115),
    ("Азитромицин капс. 250 мг №6", 246),
    ("Амоксициллин капс. 500 мг №16", 198),
    ("Флуконазол капс. 150 мг №1", 61),
    ("Ксилометазолин спрей наз. 0,1% 10 мл", 94),
    ("Хлоргексидин р-р 0,05% 100 мл", 29),
    ("Ацетилсалициловая кислота таб. 100 мг №30", 88),
    ("Дротаверин таб. 40 мг №20", 57),
    ("Натрия хлорид р-р 0,9% 200 мл", 45),
    ("Ретинол капс. 33000 МЕ №30", 136),
    ("Колекальциферол капли 15000 МЕ/мл 10 мл", 262),
    ("Мометазон спрей наз. 50 мкг/доза 120 доз", 574),
    ("Бисопролол таб. 5 мг №30", 128),
]


def _series(rng: random.Random) -> str:
    return f"{rng.choice('ABCDKMPT')}{rng.randint(10000, 99999)}"


def build_frames(seed: int = 7, today: date = TODAY) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = random.Random(seed)  # noqa: S311 — детерминированные демо-данные, не криптография
    stock_rows, expiry_rows = [], []
    for pharmacy in PHARMACIES:
        for product, price in rng.sample(PRODUCTS, 18):
            for _ in range(rng.choice((1, 1, 2))):
                series = _series(rng)
                quantity = rng.randint(3, 120)
                amount = round(quantity * price * rng.uniform(0.95, 1.05), 2)
                roll = rng.random()
                if roll < 0.07:
                    last_sale = None  # ни разу не продавался
                elif roll < 0.3:
                    last_sale = today - timedelta(days=rng.randint(91, 400))
                else:
                    last_sale = today - timedelta(days=rng.randint(0, 60))
                # Залежавшийся товар чаще доживает до конца срока — ради таких серий и считается «Приоритет».
                horizon = 240 if roll < 0.3 else 720
                expires = today + timedelta(days=rng.randint(-20, horizon))
                stock_rows.append((pharmacy, product, series, quantity, amount, last_sale))
                expiry_rows.append((pharmacy, product, series, expires, quantity, amount))
    stock = pd.DataFrame(
        stock_rows, columns=["Аптека", "Товар", "Серия", "Остаток", "Сумма, руб", "Дата последней продажи"]
    )
    expiry = pd.DataFrame(
        expiry_rows, columns=["Аптека", "Наименование", "Серия", "Годен до", "Кол-во", "Сумма"]
    )
    return stock, expiry


def build_demo(folder: Path, seed: int = 7, today: date = TODAY) -> tuple[Path, Path]:
    """Пишет две выгрузки и возвращает (остатки, сроки). Названия столбцов намеренно разные — как в жизни."""
    folder.mkdir(parents=True, exist_ok=True)
    stock, expiry = build_frames(seed, today)
    stock_path, expiry_path = folder / "остатки.xlsx", folder / "сроки годности.xlsx"
    stock.to_excel(stock_path, index=False)
    expiry.to_excel(expiry_path, index=False)
    return stock_path, expiry_path


if __name__ == "__main__":
    paths = build_demo(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples"))
    print("Созданы:", *paths, sep="\n  ")
