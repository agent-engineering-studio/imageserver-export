"""Cutting a grid into requests the service accepts."""

from __future__ import annotations

from dataclasses import dataclass

from imageserver_export.grid import Bounds, Grid

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
