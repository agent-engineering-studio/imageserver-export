"""Writing tiles into one GeoTIFF that exists only when it is whole."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

from imageserver_export.errors import ImageServerError
from imageserver_export.grid import Grid
from imageserver_export.tiles import Tile


class Mosaic:
    """A tiled, compressed GeoTIFF on ``grid``, written to ``<path>.part``.

    Used as a context manager: on a clean exit the ``.part`` is renamed to
    ``path``; on an exception it is deleted. A half-written raster that a
    later reader takes for a whole one is worse than no raster.

    ``tags`` are written as GeoTIFF metadata when the mosaic is whole,
    together with ``valid_px``: where the pixels came from travels with them.
    """

    def __init__(
        self,
        path: Path,
        grid: Grid,
        *,
        dtype: str = "uint8",
        nodata: float | None = 255,
        valid_range: tuple[float, float] | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path
        self.partial = path.with_name(f"{path.name}.part")
        self.grid = grid
        self.dtype = dtype
        self.nodata = nodata
        self.valid_range = valid_range
        self.tags = dict(tags or {})
        self.valid_px = 0
        self._dst: Any = None

    def __enter__(self) -> Mosaic:
        import rasterio

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._dst = rasterio.open(
            self.partial,
            "w",
            driver="GTiff",
            width=self.grid.width,
            height=self.grid.height,
            count=1,
            dtype=self.dtype,
            crs=f"EPSG:{self.grid.crs}",
            transform=self.grid.transform,
            nodata=self.nodata,
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="deflate",
            BIGTIFF="IF_SAFER",
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._dst is not None:
            if exc_type is None:
                self._dst.update_tags(**self.tags, valid_px=str(self.valid_px))
            self._dst.close()
        if exc_type is None:
            self.partial.replace(self.path)
        else:
            self.partial.unlink(missing_ok=True)

    def paste(self, tile: Tile, image: bytes) -> int:
        """Write one tile into its window; the number of valid pixels in it.

        A tile whose size differs from the one asked for raises: pasting it
        would shift the data by however many pixels the service got wrong.
        """
        import numpy as np
        from rasterio.io import MemoryFile
        from rasterio.windows import Window

        with MemoryFile(image) as mem, mem.open() as src:
            band = src.read(1)
        if band.shape != (tile.height, tile.width):
            raise ImageServerError(
                f"the service returned {band.shape[1]}x{band.shape[0]} pixels "
                f"for a {tile.width}x{tile.height} tile"
            )
        band = band.astype(self.dtype, copy=False)
        self._dst.write(band, 1, window=Window(tile.col_off, tile.row_off, tile.width, tile.height))
        if self.valid_range is not None:
            low, high = self.valid_range
            valid = int(np.count_nonzero((band >= low) & (band <= high)))
        elif self.nodata is not None:
            valid = int(np.count_nonzero(band != self.nodata))
        else:
            valid = int(band.size)
        self.valid_px += valid
        return valid
