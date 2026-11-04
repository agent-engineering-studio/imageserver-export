from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import rasterio
from conftest import tiff

from imageserver_export import (
    Grid,
    ImageServer,
    ImageServerError,
    Mosaic,
    Tile,
    list_services,
    occupied,
)

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


async def test_export_records_where_its_pixels_came_from(tmp_path: Path) -> None:
    async with _server(_Server()) as s:
        await s.export(
            GRID, tmp_path / "out.tif", tile_px=3, valid_range=(0, 100), tags={"layer": "IMD"}
        )
    with rasterio.open(tmp_path / "out.tif") as r:
        tags = r.tags()
    assert tags["source"] == URL and tags["layer"] == "IMD"
    assert tags["interpolation"] == "RSP_NearestNeighbor" and tags["pixel_type"] == "U8"
    assert tags["valid_range"] == "0,100" and tags["valid_px"] == "15"
    assert tags["software"].startswith("imageserver-export ")
    assert tags["created"].endswith("+00:00")


async def test_export_without_a_range_and_overridden_tags(tmp_path: Path) -> None:
    async with _server(_Server()) as s:
        await s.export(GRID, tmp_path / "out.tif", tags={"source": "mirror"})
    with rasterio.open(tmp_path / "out.tif") as r:
        tags = r.tags()
    assert tags["source"] == "mirror" and "valid_range" not in tags


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


async def test_skip_empty_sends_no_request_for_tiles_without_data(tmp_path: Path) -> None:
    # Data (7) only in the tiles whose bbox starts at x=0; 250 elsewhere is
    # outside the valid range. The middle column touches the data and stays.
    server = _Server()
    progress: list[tuple[int, int]] = []
    async with _server(server) as s:
        result = await s.export(
            GRID,
            tmp_path / "out.tif",
            tile_px=3,
            valid_range=(0, 100),
            skip_empty=True,
            on_tile=lambda d, t: progress.append((d, t)),
        )
    assert (result.tiles, result.skipped) == (6, 2)
    assert progress == [(i, 6) for i in range(1, 7)]
    # 6 overview tiles (factor 1 at this tile size) + 4 real ones.
    assert len(server.requests) == 10
    with rasterio.open(tmp_path / "out.tif") as r:
        band = r.read(1)
    assert (band[:, :3] == 7).all() and (band[:, 3:6] == 250).all() and (band[:, 6] == 255).all()


async def test_skip_empty_needs_nodata_and_a_whole_overview(tmp_path: Path) -> None:
    async with _server(_Server()) as s:
        with pytest.raises(ValueError, match="nodata"):
            await s.export(GRID, tmp_path / "out.tif", nodata=None, skip_empty=True)

    def wrong_size(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=tiff(2, 2), headers={"content-type": "image/tiff"})

    async with _server(wrong_size) as s:
        with pytest.raises(ImageServerError, match="overview tile"):
            await s.export(GRID, tmp_path / "out.tif", skip_empty=True)
    assert list(tmp_path.iterdir()) == []


async def test_resume_reuses_the_tiles_of_a_failed_run(tmp_path: Path) -> None:
    server = _Server()
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 4:
            return httpx.Response(
                200,
                json={"error": {"message": "busy"}},
                headers={"content-type": "application/json"},
            )
        return server(request)

    dest = tmp_path / "out.tif"
    cache = tmp_path / "out.tif.tiles"
    async with _server(flaky) as s:
        with pytest.raises(ImageServerError, match="busy"):
            await s.export(GRID, dest, tile_px=3, concurrency=1, resume=True)
    assert not dest.exists() and cache.is_dir()
    kept = len(list(cache.glob("*.tif")))
    assert kept >= 1

    server.requests.clear()
    async with _server(server) as s:
        result = await s.export(GRID, dest, tile_px=3, concurrency=1, resume=True)
    assert result.reused == kept and len(server.requests) == 6 - kept
    assert dest.exists() and not cache.exists()
    with rasterio.open(dest) as r:
        band = r.read(1)
    assert (band[:, :3] == 7).all() and (band[:, 3:] == 250).all()


def test_occupied_looks_one_overview_pixel_past_the_tile() -> None:
    valid = np.zeros((4, 4), dtype=bool)
    valid[0, 2] = True
    assert occupied(Tile(0, 0, 4, 4), valid, factor=2)  # overview cols 0-1, grown to 2
    assert not occupied(Tile(0, 0, 2, 2), valid, factor=2)  # overview col 0, grown to 1
    assert occupied(Tile(4, 0, 4, 4), valid, factor=2)


def test_mosaic_fill_needs_nodata(tmp_path: Path) -> None:
    g = Grid(crs=3035, left=0.0, top=200.0, res=100.0, width=2, height=2)
    with pytest.raises(ValueError, match="nodata"), Mosaic(tmp_path / "a.tif", g, nodata=None) as m:
        m.fill(Tile(0, 0, 2, 2))
    with Mosaic(tmp_path / "b.tif", g) as m:
        m.fill(Tile(0, 0, 2, 2))
    with rasterio.open(tmp_path / "b.tif") as r:
        assert (r.read(1) == 255).all() and r.tags()["valid_px"] == "0"
