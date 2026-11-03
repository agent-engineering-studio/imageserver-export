"""The target raster: a grid whose edges sit on whole pixels.

Every tile of an export is a window of this grid, so its bounds are exact
multiples of the resolution and the service never has to resample to fit
the size asked for. Resampling a band that carries sentinels (255 "no data"
next to 0-100 percentages) can invent valid values exactly where data is
missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from affine import Affine

Bounds = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Grid:
    """``width`` x ``height`` pixels of ``res`` units, top-left at ``(left, top)``."""

    crs: int
    left: float
    top: float
    res: float
    width: int
    height: int

    @property
    def bounds(self) -> Bounds:
        return (
            self.left,
            self.top - self.height * self.res,
            self.left + self.width * self.res,
            self.top,
        )

    @property
    def transform(self) -> Affine:
        from rasterio.transform import from_origin

        transform: Affine = from_origin(self.left, self.top, self.res, self.res)
        return transform

    @classmethod
    def snap(cls, bounds: Bounds, *, res: float, crs: int) -> Grid:
        """The smallest grid of ``res`` that contains ``bounds`` (in ``crs``).

        Edges grow outward to the next multiple of ``res``: rounding would
        cut a strip off the area asked for.
        """
        x0, y0, x1, y1 = bounds
        if not (x0 < x1 and y0 < y1):
            raise ValueError(f"bounds are (xmin, ymin, xmax, ymax): {bounds}")
        if res <= 0:
            raise ValueError("res must be positive")
        left = math.floor(x0 / res) * res
        bottom = math.floor(y0 / res) * res
        right = math.ceil(x1 / res) * res
        top = math.ceil(y1 / res) * res
        return cls(
            crs=crs,
            left=left,
            top=top,
            res=res,
            width=round((right - left) / res),
            height=round((top - bottom) / res),
        )

    @classmethod
    def from_lonlat(cls, bbox: Bounds, *, res: float, crs: int = 3035) -> Grid:
        """The grid covering a longitude/latitude box, in ``crs`` (LAEA Europe by default).

        The box is reprojected with densified edges, not by its four corners:
        in an equal-area projection the sides of a geographic box curve, and
        the southernmost point is not a corner. Measured on Italy, the corners
        alone cut 6.8 km off the southern edge (Lampedusa outside the raster).
        """
        from rasterio.warp import transform_bounds

        return cls.snap(transform_bounds(4326, crs, *bbox, densify_pts=101), res=res, crs=crs)
