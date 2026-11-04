"""From a fine raster to coarse cells: the mean and the share of valid pixels.

A 100 m imperviousness raster feeds a model on 1 km cells: each cell wants
the mean of its 10x10 pixels, and how many of them were real data. Sentinel
values (254 "unclassifiable", 255 "no data") must stay out of the mean, or a
coastal cell half in the sea comes out 128 % sealed.

The raster is read one strip of cells at a time, so a whole country at 10 m
never has to fit in memory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from imageserver_export.grid import Grid
from imageserver_export.mosaic import valid_mask

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True, slots=True)
class Aggregate:
    """Coarse cells on ``grid``: ``mean`` (NaN with no valid pixel) and ``valid_fraction``.

    ``valid_fraction`` is over the whole cell: a cell that sticks out past the
    raster's edge counts the missing part as not valid.
    """

    grid: Grid
    mean: np.ndarray
    valid_fraction: np.ndarray


def aggregate(
    path: Path,
    factor: int,
    *,
    valid_range: tuple[float, float] | None = None,
) -> Aggregate:
    """Cells of ``factor`` x ``factor`` pixels of the GeoTIFF at ``path``.

    A pixel counts if it lies in ``valid_range``; without it, if it is not the
    file's nodata. The cell grid starts at the raster's top-left corner, so
    for cells to fall on an existing grid (the 1 km EEA reference grid, say)
    export on a grid snapped to it: ``Grid.snap(..., res=100)`` with bounds on
    whole kilometres.
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    if factor < 1:
        raise ValueError("factor must be ≥ 1")
    with rasterio.open(path) as src:
        a, _, left, _, e, top = tuple(src.transform)[:6]
        if a != -e or src.transform.b or src.transform.d:
            raise ValueError("aggregate needs square, north-up pixels")
        epsg = src.crs.to_epsg() if src.crs is not None else None
        if epsg is None:
            raise ValueError("aggregate needs a raster in an EPSG CRS")
        rows = math.ceil(src.height / factor)
        cols = math.ceil(src.width / factor)
        grid = Grid(crs=epsg, left=left, top=top, res=a * factor, width=cols, height=rows)
        sums = np.zeros((rows, cols), dtype=np.float64)
        counts = np.zeros((rows, cols), dtype=np.int64)
        for r in range(rows):
            row_off = r * factor
            height = min(factor, src.height - row_off)
            band: Any = src.read(1, window=Window(0, row_off, src.width, height))
            mask = valid_mask(band, valid_range=valid_range, nodata=src.nodata)
            values = np.where(mask, band, 0).astype(np.float64)
            padded = np.zeros((factor, cols * factor))
            padded_mask = np.zeros((factor, cols * factor), dtype=bool)
            padded[:height, : src.width] = values
            padded_mask[:height, : src.width] = mask
            sums[r] = padded.reshape(factor, cols, factor).sum(axis=(0, 2))
            counts[r] = padded_mask.reshape(factor, cols, factor).sum(axis=(0, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(counts > 0, sums / counts, np.nan).astype(np.float32)
    valid_fraction = (counts / (factor * factor)).astype(np.float32)
    return Aggregate(grid=grid, mean=mean, valid_fraction=valid_fraction)
