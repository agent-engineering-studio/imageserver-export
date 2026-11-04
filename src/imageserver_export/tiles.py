"""Cutting a grid into requests the service accepts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from imageserver_export.grid import Bounds, Grid

if TYPE_CHECKING:
    import numpy as np

#: Default tile side: well under the 4,100 px most services allow, so one
#: failed request costs a few MB, not the whole export.
DEFAULT_TILE_PX = 2048


@dataclass(frozen=True, slots=True)
class Tile:
    """A window of the target grid, in pixels."""

    col_off: int
    row_off: int
    width: int
    height: int

    def bounds(self, grid: Grid) -> Bounds:
        """The tile's box in the grid's CRS: whole pixels by construction."""
        x0 = grid.left + self.col_off * grid.res
        y1 = grid.top - self.row_off * grid.res
        return (x0, y1 - self.height * grid.res, x0 + self.width * grid.res, y1)


def plan(grid: Grid, *, tile_px: int = DEFAULT_TILE_PX) -> list[Tile]:
    """Tiles that cover the grid exactly once, edge tiles clamped to the grid."""
    if tile_px < 1:
        raise ValueError("tile_px must be ≥ 1")
    return [
        Tile(
            col_off=col,
            row_off=row,
            width=min(tile_px, grid.width - col),
            height=min(tile_px, grid.height - row),
        )
        for row in range(0, grid.height, tile_px)
        for col in range(0, grid.width, tile_px)
    ]


def occupied(tile: Tile, valid: np.ndarray, factor: int) -> bool:
    """Whether a tile may hold data, judged on an overview ``factor`` times coarser.

    ``valid`` marks the overview pixels that hold data. The tile's footprint
    on the overview is grown by one overview pixel on every side, so data
    sampled just across a tile edge still keeps the tile.
    """
    r0 = max(tile.row_off // factor - 1, 0)
    c0 = max(tile.col_off // factor - 1, 0)
    r1 = (tile.row_off + tile.height - 1) // factor + 2
    c1 = (tile.col_off + tile.width - 1) // factor + 2
    return bool(valid[r0:r1, c0:c1].any())
