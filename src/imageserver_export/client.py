"""An async client: describe a service, and export an area of it to a GeoTIFF.

Give it your ``httpx.AsyncClient`` and timeouts, proxies and pooling stay
yours; give it none and it opens (and closes) its own. Network errors and
429/5xx are retried ``retries`` times with exponential backoff.

To route every request through your own HTTP layer (a shared retry policy,
a cache, a rate limiter), pass ``send``: an ``async (method, url, **kwargs)
-> httpx.Response``. The client then uses it for every call and does no
retrying of its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from imageserver_export.grid import Grid
from imageserver_export.mosaic import Mosaic
from imageserver_export.request import Interpolation, check_image, export_params
from imageserver_export.service import (
    ServiceInfo,
    parse_attribute_table,
    parse_info,
    parse_services,
)
from imageserver_export.tiles import DEFAULT_TILE_PX, Tile, plan

Send = Callable[..., Awaitable[httpx.Response]]
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class Export:
    """What an export wrote."""

    path: Path
    tiles: int
    width: int
    height: int
    valid_px: int

    @property
    def valid_fraction(self) -> float:
        return self.valid_px / (self.width * self.height) if self.width and self.height else 0.0


class ImageServer:
    """One ImageServer (``https://…/arcgis/rest/services/<name>/ImageServer``)."""

    def __init__(
        self,
        url: str,
        client: httpx.AsyncClient | None = None,
        *,
        send: Send | None = None,
        retries: int = 3,
        backoff_s: float = 2.0,
        timeout_s: float = 180.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.url = url.rstrip("/")
        self._own = client is None and send is None
        self._client = client or (
            None if send is not None else httpx.AsyncClient(follow_redirects=True)
        )
        self._send_hook = send
        self._retries = retries
        self._backoff_s = backoff_s
        self._timeout_s = timeout_s
        self._sleep = sleep

    async def __aenter__(self) -> ImageServer:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this instance opened it."""
        if self._own and self._client is not None:
            await self._client.aclose()

    async def info(self) -> ServiceInfo:
        """The service description: pixel type, size, extent, largest image."""
        response = await self._send("GET", self.url, params={"f": "json"})
        return parse_info(response.content)

    async def attribute_table(self) -> list[dict[str, Any]]:
        """The raster attribute table: what each pixel value means."""
        response = await self._send("GET", f"{self.url}/rasterAttributeTable", params={"f": "json"})
        return parse_attribute_table(response.content)

    async def export(
        self,
        grid: Grid,
        dest: Path,
        *,
        pixel_type: str = "U8",
        interpolation: Interpolation = "RSP_NearestNeighbor",
        dtype: str = "uint8",
        nodata: float | None = 255,
        valid_range: tuple[float, float] | None = None,
        tile_px: int = DEFAULT_TILE_PX,
        concurrency: int = 3,
        on_tile: Callable[[int, int], None] | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> Export:
        """``grid`` of this service as a GeoTIFF at ``dest``, written atomically.

        Tiles are requested ``concurrency`` at a time and pasted as they
        arrive; ``on_tile(done, total)`` reports progress. ``valid_range``
        counts the valid pixels (e.g. ``(0, 100)`` for a percentage layer
        that uses 254 and 255 as sentinels); without it, every pixel that is
        not ``nodata`` counts.

        The file records where it came from as GeoTIFF metadata: ``source``
        (the service URL), ``interpolation``, ``pixel_type``, ``valid_range``,
        ``created`` (UTC), ``software`` and ``valid_px``. ``tags`` adds to
        them, or overrides them.
        """
        tiles = plan(grid, tile_px=tile_px)
        provenance = {
            "source": self.url,
            "interpolation": interpolation,
            "pixel_type": pixel_type,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "software": f"imageserver-export {version('imageserver-export')}",
        }
        if valid_range is not None:
            provenance["valid_range"] = f"{valid_range[0]:g},{valid_range[1]:g}"
        provenance.update(tags or {})
        gate = asyncio.Semaphore(concurrency)

        async def one(tile: Tile) -> tuple[Tile, bytes]:
            params = export_params(
                tile.bounds(grid),
                width=tile.width,
                height=tile.height,
                crs=grid.crs,
                pixel_type=pixel_type,
                interpolation=interpolation,
            )
            async with gate:
                response = await self._send(
                    "GET", f"{self.url}/exportImage", params=params, timeout=self._timeout_s
                )
            ctype = response.headers.get("content-type", "")
            return tile, check_image(response.status_code, ctype, response.content)

        with Mosaic(
            dest, grid, dtype=dtype, nodata=nodata, valid_range=valid_range, tags=provenance
        ) as mosaic:
            pending = [asyncio.ensure_future(one(t)) for t in tiles]
            try:
                for done, future in enumerate(asyncio.as_completed(pending), 1):
                    tile, image = await future
                    await asyncio.to_thread(mosaic.paste, tile, image)
                    if on_tile is not None:
                        on_tile(done, len(tiles))
            except BaseException:
                for p in pending:
                    p.cancel()
                raise
        return Export(
            path=dest,
            tiles=len(tiles),
            width=grid.width,
            height=grid.height,
            valid_px=mosaic.valid_px,
        )

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._send_hook is not None:
            return await self._send_hook(method, url, **kwargs)
        assert self._client is not None
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
            else:
                if response.status_code not in _RETRY_STATUS:
                    return response
                if attempt == self._retries:
                    response.raise_for_status()
            await self._sleep(self._backoff_s * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


async def list_services(folder_url: str, client: httpx.AsyncClient | None = None) -> list[str]:
    """The ImageServer names in a folder (``…/rest/services/<folder>``)."""
    own = client is None
    http = client or httpx.AsyncClient(follow_redirects=True)
    try:
        response = await http.get(folder_url.rstrip("/"), params={"f": "json"})
        response.raise_for_status()
        return parse_services(response.content)
    finally:
        if own:
            await http.aclose()
