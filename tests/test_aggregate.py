from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

from imageserver_export import Grid, aggregate

BAND = np.array(
    [
        [10, 20, 255, 255],
        [30, 40, 255, 50],
        [0, 100, 254, 254],
    ],
    dtype="uint8",
)


def _write(path: Path, band: np.ndarray, transform: Affine, crs: str | None = "EPSG:3035") -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=band.shape[1],
        height=band.shape[0],
        count=1,
        dtype=band.dtype,
        crs=crs,
        transform=transform,
        nodata=255,
    ) as dst:
        dst.write(band, 1)
    return path


def test_cells_average_only_valid_pixels(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.tif", BAND, from_origin(1000, 2000, 100, 100))
    cells = aggregate(path, 2, valid_range=(0, 100))
    assert cells.grid == Grid(crs=3035, left=1000, top=2000, res=200, width=2, height=2)
    np.testing.assert_allclose(cells.mean, np.array([[25.0, 50.0], [50.0, np.nan]]))
    # The bottom row of cells sticks out past the raster: its missing half is not valid.
    np.testing.assert_allclose(cells.valid_fraction, [[1, 0.25], [0.5, 0]])


def test_without_a_range_only_nodata_is_left_out(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.tif", BAND, from_origin(0, 300, 100, 100))
    cells = aggregate(path, 2)
    assert cells.mean[1, 1] == 254 and cells.valid_fraction[1, 1] == 0.5
    whole = aggregate(path, 1)
    assert whole.mean.shape == (3, 4) and np.isnan(whole.mean[0, 2])


def test_refuses_what_it_cannot_cut_into_cells(tmp_path: Path) -> None:
    square = _write(tmp_path / "a.tif", BAND, from_origin(0, 300, 100, 100))
    with pytest.raises(ValueError, match="factor"):
        aggregate(square, 0)
    tall = _write(tmp_path / "b.tif", BAND, from_origin(0, 300, 100, 50))
    with pytest.raises(ValueError, match="square"):
        aggregate(tall, 2)
    no_crs = _write(tmp_path / "c.tif", BAND, from_origin(0, 300, 100, 100), crs=None)
    with pytest.raises(ValueError, match="EPSG"):
        aggregate(no_crs, 2)
