"""Клиент Яндекс Диска против локального эмулятора API на aiohttp.web: настоящий HTTP и потоковый приём.

Подменяются только адрес API и проверка ссылки на скачивание (эмулятор живёт на http://127.0.0.1).
Боевая проверка домена покрыта отдельно: test_download_host_check и test_foreign_download_link_is_refused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import test_utils, web

from pharmacy_bot.yadisk import DiskFile, YandexDisk, YandexDiskError, is_allowed_download, normalize_folder

TOKEN = "token"
FILE = DiskFile("остатки.xlsx", "disk:/Отчёты/остатки.xlsx", "2026-09-11T06:00:00+00:00", 11)


class DiskEmulator:
    """Три ручки, которые нужны боту: листинг папки, ссылка на скачивание и сервер скачивания."""

    def __init__(self) -> None:
        self.base = ""
        self.items: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.fail_with: int | None = None
        self.href: str | None = None  # подменённая ссылка — как если бы ответ API подделали
        self.queries: list[dict[str, str]] = []
        self.download_auth: list[str | None] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/disk/resources", self.listing)
        app.router.add_get("/v1/disk/resources/download", self.link)
        app.router.add_get("/files/{name}", self.file)
        return app

    def _refusal(self, request: web.Request) -> web.Response | None:
        self.queries.append(dict(request.query))
        if request.headers.get("Authorization") != f"OAuth {TOKEN}":
            return web.json_response({"error": "UnauthorizedError"}, status=401)
        if self.fail_with is not None:
            return web.json_response({"error": "emulated"}, status=self.fail_with)
        return None

    async def listing(self, request: web.Request) -> web.Response:
        refusal = self._refusal(request)
        if refusal is not None:
            return refusal
        return web.json_response({"_embedded": {"items": self.items}})

    async def link(self, request: web.Request) -> web.Response:
        refusal = self._refusal(request)
        if refusal is not None:
            return refusal
        name = request.query["path"].rsplit("/", 1)[-1]
        return web.json_response({"href": self.href or f"{self.base}/files/{name}"})

    async def file(self, request: web.Request) -> web.StreamResponse:
        self.download_auth.append(request.headers.get("Authorization"))
        body = self.files[request.match_info["name"]]
        response = web.StreamResponse()
        await response.prepare(request)
        for start in range(0, len(body), 64 * 1024):
            await response.write(body[start : start + 64 * 1024])
        await response.write_eof()
        return response


@pytest.fixture
async def emulator() -> AsyncIterator[DiskEmulator]:
    fake = DiskEmulator()
    server = test_utils.TestServer(fake.app())
    await server.start_server()
    fake.base = f"http://{server.host}:{server.port}"
    yield fake
    await server.close()


def client(fake: DiskEmulator, token: str = TOKEN, max_bytes: int = 10**6) -> YandexDisk:
    return YandexDisk(
        token,
        max_bytes,
        api_url=f"{fake.base}/v1/disk/resources",
        link_check=lambda href: href.startswith(f"{fake.base}/files/"),
    )


def item(name: str, kind: str = "file") -> dict[str, Any]:
    return {"name": name, "path": f"disk:/Отчёты/{name}", "type": kind, "size": 11, "modified": FILE.modified}


async def test_latest_file_skips_folders_and_other_types(emulator: DiskEmulator) -> None:
    emulator.items = [item("архив", kind="dir"), item("readme.txt"), item(FILE.name)]

    found = await client(emulator).latest_file("Отчёты", (".xlsx", ".zip"))

    assert found == FILE
    [query] = emulator.queries
    assert (query["path"], query["sort"], query["limit"]) == ("disk:/Отчёты/", "-modified", "100")


async def test_empty_folder_returns_none(emulator: DiskEmulator) -> None:
    assert await client(emulator).latest_file("Отчёты", (".xlsx",)) is None


async def test_download_streams_file_without_sending_token(emulator: DiskEmulator, tmp_path: Path) -> None:
    body = bytes(range(256)) * 3000  # 750 КБ — несколько кусков по 256 КБ
    emulator.files[FILE.name] = body

    target = await client(emulator).download(FILE, tmp_path / "file.xlsx")

    assert target.read_bytes() == body
    assert emulator.download_auth == [None]  # OAuth-токен ушёл только в API, не на сервер скачивания


async def test_foreign_download_link_is_refused(emulator: DiskEmulator, tmp_path: Path) -> None:
    emulator.href = "https://evil.example.com/steal"
    disk = YandexDisk(TOKEN, 1000, api_url=f"{emulator.base}/v1/disk/resources")  # боевая проверка ссылки

    with pytest.raises(YandexDiskError, match="посторонний адрес"):
        await disk.download(FILE, tmp_path / "file.xlsx")

    assert not (tmp_path / "file.xlsx").exists()


@pytest.mark.parametrize(
    ("token", "status", "message"),
    [
        pytest.param("bad", None, "не принял токен", id="401"),
        pytest.param(TOKEN, 403, "Нет доступа", id="403"),
        pytest.param(TOKEN, 404, "не найдены", id="404"),
        pytest.param(TOKEN, 503, "ответил 503", id="503"),
    ],
)
async def test_api_errors_are_explained(
    emulator: DiskEmulator, token: str, status: int | None, message: str
) -> None:
    emulator.fail_with = status

    with pytest.raises(YandexDiskError, match=message):
        await client(emulator, token=token).latest_file("Отчёты", (".xlsx",))


async def test_size_limits(emulator: DiskEmulator, tmp_path: Path) -> None:
    disk = client(emulator, max_bytes=5)
    with pytest.raises(YandexDiskError, match="больше допустимых"):
        await disk.download(FILE, tmp_path / "big.xlsx")  # размер из метаданных — до любых запросов
    assert emulator.queries == []

    emulator.files["x.xlsx"] = b"0123456789"
    small_but_lying = DiskFile("x.xlsx", "disk:/x.xlsx", "", 1)
    with pytest.raises(YandexDiskError, match="больше лимита"):
        await disk.download(small_but_lying, tmp_path / "lie.xlsx")
    assert not (tmp_path / "lie.xlsx").exists()  # недокачанный файл не остаётся на диске


@pytest.mark.parametrize(
    ("href", "allowed"),
    [
        ("https://downloader.disk.yandex.ru/x", True),
        ("https://s123.storage.yandex.net/x", True),
        ("http://downloader.disk.yandex.ru/x", False),
        ("https://yandex.ru.evil.com/x", False),
        ("https://localhost/x", False),
    ],
)
def test_download_host_check(href: str, allowed: bool) -> None:
    assert is_allowed_download(href) is allowed


def test_folder_normalization() -> None:
    assert normalize_folder("/Отчёты/остатки") == "disk:/Отчёты/остатки/"
    assert normalize_folder("disk:/Отчёты/") == "disk:/Отчёты/"
