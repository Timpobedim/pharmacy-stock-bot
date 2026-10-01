from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(folder: Path) -> None:
    """Лог в файл с ротацией (5 МБ × 3, как в референсе) и в консоль."""
    folder.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        folder / "bot.log", maxBytes=5 * 2**20, backupCount=3, encoding="utf-8"
    )
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console], force=True)
    # httpx пишет в INFO полные URL запросов к Bot API, а в них токен бота — в лог ему не место.
    logging.getLogger("httpx").setLevel(logging.WARNING)
