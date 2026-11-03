from __future__ import annotations

import numpy as np


def tiff(width: int, height: int, value: int = 42, dtype: str = "uint8") -> bytes:
    """A real GeoTIFF of ``width`` x ``height`` filled with ``value``."""
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype=dtype,
            crs="EPSG:3035",
            transform=from_origin(0, 0, 100, 100),
        ) as dst:
            dst.write(np.full((height, width), value, dtype=dtype), 1)
        return bytes(mem.read())
