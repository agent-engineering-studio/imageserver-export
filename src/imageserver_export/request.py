"""``exportImage``: the query and the check of the answer, pure."""

from __future__ import annotations

from typing import Literal

from imageserver_export.errors import ImageServerError
from imageserver_export.grid import Bounds

#: ``RSP_NearestNeighbor`` is the only safe choice for thematic or
#: sentinel-carrying layers; ``RSP_BilinearInterpolation`` only for continuous ones.
Interpolation = Literal[
    "RSP_NearestNeighbor",
    "RSP_BilinearInterpolation",
    "RSP_CubicConvolution",
    "RSP_Majority",
]


def export_params(
    bounds: Bounds,
    *,
    width: int,
    height: int,
    crs: int,
    pixel_type: str = "U8",
    interpolation: Interpolation = "RSP_NearestNeighbor",
) -> dict[str, str]:
    """The query string of ``GET <service>/exportImage`` for one tile, as a GeoTIFF."""
    return {
        "bbox": ",".join(f"{v:.6f}" for v in bounds),
        "bboxSR": str(crs),
        "imageSR": str(crs),
        "size": f"{width},{height}",
        "format": "tiff",
        "pixelType": pixel_type,
        "interpolation": interpolation,
        "f": "image",
    }


def check_image(status_code: int, content_type: str, body: bytes) -> bytes:
    """The GeoTIFF bytes, or :class:`ImageServerError` with the service's message.

    An ImageServer answers a bad request with **HTTP 200 and a JSON error**:
    without this check the JSON would reach the TIFF reader as a corrupt
    file, hiding the message that says what was wrong.
    """
    if status_code != 200:
        raise ImageServerError(f"exportImage failed (HTTP {status_code}): {body[:200]!r}")
    if "json" in content_type or body[:1] == b"{":
        raise ImageServerError(
            f"exportImage refused the request: {body[:300].decode('utf-8', 'replace')}"
        )
    return body
