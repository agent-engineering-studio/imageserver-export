from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import rasterio
from conftest import tiff

from imageserver_export import Grid, ImageServer, ImageServerError, Mosaic, Tile, list_services

URL = "https://example.org/arcgis/rest/services/Layer/ImageServer"
GRID = Grid(crs=3035, left=0.0, top=1000.0, res=100.0, width=7, height=5)


async def _sleep(_s: float) -> None:
    return None


class _Server:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.failures: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            return httpx.Response(self.failures.pop(0))
        path = request.url.path
        if path.endswith("/exportImage"):
            w, h = (int(v) for v in request.url.params["size"].split(","))
            value = 7 if request.url.params["bbox"].startswith("0.0") else 250
            return httpx.Response(
                200, content=tiff(w, h, value), headers={"content-type": "image/tiff"}
            )
        if path.endswith("/rasterAttributeTable"):
            return httpx.Response(200, json={"features": [{"attributes": {"Value": 1}}]})
        if path.endswith("/ImageServer"):
            return httpx.Response(
                200,
                json={
                    "name": "Layer",
                    "pixelType": "U8",
                    "pixelSizeX": 10,
                    "pixelSizeY": 10,
                    "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                },
            )
        raise AssertionError(path)


def _server(handler: Any, **kw: Any) -> ImageServer:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ImageServer(URL, client, sleep=_sleep, **kw)


async def test_export_writes_a_whole_aligned_geotiff(tmp_path: Path) -> None:
    server = _Server()
    progress: list[tuple[int, int]] = []
    async with _server(server) as s:
        result = await s.export(
            GRID,
            tmp_path / "out.tif",
            tile_px=3,
            valid_range=(0, 100),
            on_tile=lambda d, t: progress.append((d, t)),
        )
    assert (result.tiles, result.width, result.height) == (6, 7, 5)
    assert progress[-1] == (6, 6)
    with rasterio.open(tmp_path / "out.tif") as r:
        band = r.read(1)
        assert r.crs.to_epsg() == 3035 and r.res == (100.0, 100.0)
        assert (r.transform.c, r.transform.f) == (0.0, 1000.0)
        assert r.nodata == 255
    # The tiles of the first column (bbox starting at x=0) are 7, the rest 250.
    assert (band[:, :3] == 7).all() and (band[:, 3:] == 250).all()
    assert result.valid_px == 15 and result.valid_fraction == pytest.approx(15 / 35)
    assert not list(tmp_path.glob("*.part"))


async def test_a_json_error_with_http_200_stops_and_leaves_nothing(tmp_path: Path) -> None:
    def refuses(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"message": "Invalid bbox"}},
            headers={"content-type": "application/json"},
        )

    async with _server(refuses) as s:
        with pytest.raises(ImageServerError, match="Invalid bbox"):
            await s.export(GRID, tmp_path / "out.tif")
    assert list(tmp_path.iterdir()) == []


async def test_a_tile_of_the_wrong_size_is_not_pasted(tmp_path: Path) -> None:
    def wrong_size(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=tiff(2, 2), headers={"content-type": "image/tiff"})

    async with _server(wrong_size) as s:
        with pytest.raises(ImageServerError, match="2x2 pixels"):
            await s.export(GRID, tmp_path / "out.tif")
    assert list(tmp_path.iterdir()) == []


async def test_busy_server_is_retried_then_raises(tmp_path: Path) -> None:
    server = _Server()
    server.failures = [503, 429]
    async with _server(server) as s:
        assert (await s.info()).name == "Layer"
    server.failures = [503, 503]
    async with _server(server, retries=1) as s:
        with pytest.raises(httpx.HTTPStatusError):
            await s.info()

    calls: list[int] = []

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("down", request=request)

    async with _server(down, retries=1) as s:
        with pytest.raises(httpx.ConnectError):
            await s.attribute_table()
    assert len(calls) == 2


async def test_send_hook_replaces_the_http_layer(tmp_path: Path) -> None:
    server = _Server()
    seen: list[str] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))

    async def send(method: str, url: str, **kw: Any) -> httpx.Response:
        seen.append(url.rsplit("/", 1)[1])
        return await client.request(method, url, **kw)

    s = ImageServer(URL, send=send)
    assert await s.attribute_table() == [{"Value": 1}]
    await s.export(GRID, tmp_path / "out.tif", tile_px=10)
    await s.aclose()
    assert seen == ["rasterAttributeTable", "exportImage"]


async def test_own_client_is_closed_and_listing() -> None:
    s = ImageServer(URL)
    await s.aclose()

    def folder(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"services": [{"name": "X", "type": "ImageServer"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(folder))
    assert await list_services("https://e/rest/services/F/", client) == ["X"]


async def test_list_services_with_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.AsyncClient

    def mocked(**kw: Any) -> httpx.AsyncClient:
        return real(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(200, content=json.dumps({"services": []}))
            ),
            **kw,
        )

    monkeypatch.setattr(httpx, "AsyncClient", mocked)
    assert await list_services("https://e/rest/services/F") == []


def test_mosaic_counts_without_a_range(tmp_path: Path) -> None:
    g = Grid(crs=3035, left=0.0, top=200.0, res=100.0, width=2, height=2)
    with Mosaic(tmp_path / "a.tif", g, nodata=0) as m:
        assert m.paste(Tile(0, 0, 2, 2), tiff(2, 2, 0)) == 0
    with Mosaic(tmp_path / "b.tif", g, nodata=None) as m:
        assert m.paste(Tile(0, 0, 2, 2), tiff(2, 2, 0)) == 4
    with rasterio.open(tmp_path / "b.tif") as r:
        assert np.array_equal(r.read(1), np.zeros((2, 2)))
