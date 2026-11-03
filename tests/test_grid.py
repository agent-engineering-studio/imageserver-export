from __future__ import annotations

import pytest

from imageserver_export import Grid, Tile, plan


def test_snap_grows_outward_to_whole_pixels() -> None:
    g = Grid.snap((4_000_050.0, 2_000_010.0, 4_000_950.0, 2_000_420.0), res=100, crs=3035)
    assert (g.left, g.top, g.width, g.height) == (4_000_000.0, 2_000_500.0, 10, 5)
    assert g.bounds == (4_000_000.0, 2_000_000.0, 4_001_000.0, 2_000_500.0)
    assert all(v % 100 == 0 for v in g.bounds)


def test_snap_refuses_bad_input() -> None:
    with pytest.raises(ValueError, match="bounds"):
        Grid.snap((1.0, 1.0, 0.0, 2.0), res=10, crs=3035)
    with pytest.raises(ValueError, match="res"):
        Grid.snap((0.0, 0.0, 1.0, 1.0), res=0, crs=3035)


def test_from_lonlat_covers_the_curved_southern_edge() -> None:
    # Italy: the corners alone cut 6.8 km off the south (Lampedusa outside).
    g = Grid.from_lonlat((6.6, 35.4, 18.6, 47.1), res=100)
    from rasterio.warp import transform

    xs, ys = transform(4326, 3035, [12.6], [35.5])  # Lampedusa
    x0, y0, x1, y1 = g.bounds
    assert x0 <= xs[0] <= x1 and y0 <= ys[0] <= y1
    assert g.crs == 3035 and all(v % 100 == 0 for v in g.bounds)


def test_transform_puts_the_origin_top_left() -> None:
    g = Grid(crs=3035, left=10.0, top=20.0, res=5.0, width=2, height=2)
    assert (g.transform.c, g.transform.f, g.transform.a, g.transform.e) == (10.0, 20.0, 5.0, -5.0)


def test_tiles_partition_the_grid_exactly() -> None:
    import numpy as np

    g = Grid(crs=3035, left=0.0, top=0.0, res=1.0, width=542, height=488)
    coverage = np.zeros((g.height, g.width), dtype=np.uint8)
    for t in plan(g, tile_px=256):
        coverage[t.row_off : t.row_off + t.height, t.col_off : t.col_off + t.width] += 1
    assert coverage.min() == 1 and coverage.max() == 1


def test_edge_tiles_are_clamped() -> None:
    g = Grid(crs=3035, left=0.0, top=0.0, res=1.0, width=300, height=100)
    assert [(t.width, t.height) for t in plan(g, tile_px=256)] == [(256, 100), (44, 100)]
    with pytest.raises(ValueError):
        plan(g, tile_px=0)


def test_tile_bounds_land_on_pixel_edges() -> None:
    g = Grid(crs=3035, left=4_000_000.0, top=2_500_000.0, res=100.0, width=1000, height=1000)
    x0, y0, x1, y1 = Tile(col_off=256, row_off=512, width=100, height=50).bounds(g)
    assert (x0, y1) == (4_025_600.0, 2_448_800.0)
    assert (x1 - x0, y1 - y0) == (10_000.0, 5_000.0)
