"""What an ImageServer says about itself: pure parsers of its JSON.

``GET <service>?f=json`` describes the raster (pixel type, bands, pixel
size, extent, the largest image one request may ask for);
``GET <service>/rasterAttributeTable?f=json`` maps the pixel values of a
thematic layer to their meaning; ``GET <folder>?f=json`` lists the services.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from imageserver_export.errors import ImageServerError

#: The European Environment Agency's open ImageServer: the Copernicus Land
#: Monitoring Service layers (imperviousness, tree cover, grassland, water
#: and wetness, small woody features...), with no account.
EEA_URL = "https://image.discomap.eea.europa.eu/arcgis/rest/services"
EEA_FOLDER = "GioLandPublic"


def eea(name: str) -> str:
    """The URL of one EEA service: ``eea("HRL_ImperviousnessDensity_2018")``."""
    return f"{EEA_URL}/{EEA_FOLDER}/{name}/ImageServer"


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """The parts of a service description an export needs."""

    name: str
    pixel_type: str
    band_count: int
    pixel_size: tuple[float, float]
    #: The largest image one ``exportImage`` may return, in pixels.
    max_width: int
    max_height: int
    #: EPSG code of the service's own CRS.
    crs: int | None
    extent: tuple[float, float, float, float]
    has_attribute_table: bool
    copyright: str


def _json(body: bytes | str) -> Any:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ImageServerError(f"the service did not return JSON: {str(body)[:200]!r}") from exc
    if isinstance(data, dict) and "error" in data:
        error = data["error"]
        message = error.get("message") if isinstance(error, dict) else error
        raise ImageServerError(f"the service refused: {message}")
    return data


def parse_info(body: bytes | str) -> ServiceInfo:
    """A :class:`ServiceInfo` from ``<service>?f=json``."""
    data = _json(body)
    try:
        sr = data.get("spatialReference") or {}
        extent = data["extent"]
        return ServiceInfo(
            name=str(data.get("name") or ""),
            pixel_type=str(data["pixelType"]),
            band_count=int(data.get("bandCount") or 1),
            pixel_size=(float(data["pixelSizeX"]), float(data["pixelSizeY"])),
            max_width=int(data.get("maxImageWidth") or 4000),
            max_height=int(data.get("maxImageHeight") or 4000),
            crs=sr.get("latestWkid") or sr.get("wkid"),
            extent=(
                float(extent["xmin"]),
                float(extent["ymin"]),
                float(extent["xmax"]),
                float(extent["ymax"]),
            ),
            has_attribute_table=bool(data.get("hasRasterAttributeTable")),
            copyright=str(data.get("copyrightText") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ImageServerError(f"not an ImageServer description: missing {exc}") from exc


def parse_attribute_table(body: bytes | str) -> list[dict[str, Any]]:
    """The rows of ``<service>/rasterAttributeTable?f=json``, as plain dicts."""
    data = _json(body)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise ImageServerError("not a raster attribute table")
    return [dict(f.get("attributes") or {}) for f in features]


def parse_services(body: bytes | str) -> list[str]:
    """The ImageServer names of a folder listing (``<folder>?f=json``)."""
    data = _json(body)
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, list):
        raise ImageServerError("not a folder listing")
    return [str(s["name"]) for s in services if s.get("type") == "ImageServer"]
