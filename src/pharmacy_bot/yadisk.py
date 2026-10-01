"""Клиент REST API Яндекс Диска на aiohttp: последний файл из папки и его скачивание по OAuth.

Как в референсе: ссылку на скачивание API отдаёт отдельным запросом, и перед тем как по ней пойти,
проверяется, что это HTTPS на домене Яндекса, — защита от подмены адреса (SSRF). OAuth-токен на сервер
скачивания не отправляется, размер ограничен, файл пишется потоком, недокачанный — удаляется.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

API_URL = "https://cloud-api.yandex.net/v1/disk/resources"
ALLOWED_DOWNLOAD_HOSTS = ("yandex.net", "yandex.ru")
API_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=1800, connect=30, sock_read=300)
CHUNK = 256 * 1024


class YandexDiskError(Exception):
    pass


@dataclass(frozen=True)
class DiskFile:
    name: str
    path: str
    modified: str
    size: int


def normalize_folder(path: str) -> str:
    folder = path.strip()
    if not folder.startswith("disk:"):
        folder = "disk:/" + folder.lstrip("/")
    return folder if folder.endswith("/") else folder + "/"


def is_allowed_download(href: str) -> bool:
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == base or host.endswith("." + base) for base in ALLOWED_DOWNLOAD_HOSTS
    )


async def _check(response: aiohttp.ClientResponse) -> None:
    if response.status < 400:
        return
    if response.status == 401:
        raise YandexDiskError("Яндекс Диск не принял токен (401): обновите YANDEX_OAUTH_TOKEN")
    if response.status == 403:
        raise YandexDiskError("Нет доступа к папке на Яндекс Диске (403)")
    if response.status == 404:
        raise YandexDiskError("Папка или файл на Яндекс Диске не найдены (404)")
    raise YandexDiskError(f"Яндекс Диск ответил {response.status}: {(await response.text())[:200]}")


class YandexDisk:
    def __init__(
        self,
        token: str,
        max_bytes: int,
        *,
        api_url: str = API_URL,
        link_check: Callable[[str], bool] = is_allowed_download,
    ) -> None:
        """`api_url` и `link_check` меняются только в тестах: там API эмулирует локальный сервер."""
        self._headers = {"Authorization": f"OAuth {token}"}
        self._max_bytes = max_bytes
        self._api_url = api_url
        self._link_check = link_check

    async def latest_file(self, folder: str, suffixes: tuple[str, ...]) -> DiskFile | None:
        params = {
            "path": normalize_folder(folder),
            "limit": "100",
            "sort": "-modified",
            "fields": "_embedded.items.name,_embedded.items.path,_embedded.items.modified,"
            "_embedded.items.size,_embedded.items.type",
        }
        try:
            data = await self._get_json(self._api_url, params)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise YandexDiskError(
                "Не удалось связаться с Яндекс Диском — проверьте сеть и повторите"
            ) from exc
        for item in data.get("_embedded", {}).get("items", []):
            if item.get("type") == "file" and Path(item["name"]).suffix.lower() in suffixes:
                return DiskFile(item["name"], item["path"], item["modified"], int(item.get("size", 0)))
        return None

    async def download(self, file: DiskFile, target: Path) -> Path:
        """Скачивает файл потоком в `target`; при любой ошибке недокачанный файл удаляется."""
        if file.size > self._max_bytes:
            raise YandexDiskError(f"Файл «{file.name}» больше допустимых {self._max_bytes // 2**20} МБ")
        try:
            link = await self._get_json(f"{self._api_url}/download", {"path": file.path})
            href = str(link.get("href", ""))
            if not self._link_check(href):
                raise YandexDiskError("Яндекс Диск вернул ссылку на посторонний адрес — скачивание отменено")
            await self._save(href, target, file.name)
        except (aiohttp.ClientError, TimeoutError) as exc:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise YandexDiskError("Скачивание с Яндекс Диска прервалось — повторите позже") from exc
        except YandexDiskError:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        return target

    async def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        async with (
            aiohttp.ClientSession(headers=self._headers, timeout=API_TIMEOUT) as session,
            session.get(url, params=params) as response,
        ):
            await _check(response)
            data: dict[str, Any] = await response.json()
        return data

    async def _save(self, href: str, target: Path, name: str) -> None:
        # Отдельная сессия без Authorization: токен не должен уходить на сервер скачивания.
        written = 0
        async with (
            aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session,
            session.get(href) as response,
        ):
            await _check(response)
            with target.open("wb") as output:
                async for chunk in response.content.iter_chunked(CHUNK):
                    written += len(chunk)
                    if written > self._max_bytes:
                        raise YandexDiskError(f"Файл «{name}» оказался больше лимита")
                    output.write(chunk)
