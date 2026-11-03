"""Export ArcGIS ImageServer rasters to aligned, atomic GeoTIFF mosaics.

Pure pieces (:mod:`~imageserver_export.service`, :mod:`~imageserver_export.grid`,
:mod:`~imageserver_export.tiles`, :mod:`~imageserver_export.request`), the
mosaic writer (:mod:`~imageserver_export.mosaic`) and an async client over
them (:class:`ImageServer`).
"""

from importlib.metadata import version

from imageserver_export.client import Export, ImageServer, Send, list_services
from imageserver_export.errors import ImageServerError
from imageserver_export.grid import Bounds, Grid
from imageserver_export.mosaic import Mosaic
from imageserver_export.request import Interpolation, check_image, export_params
from imageserver_export.service import (
    EEA_FOLDER,
    EEA_URL,
    ServiceInfo,
    eea,
    parse_attribute_table,
    parse_info,
    parse_services,
)
from imageserver_export.tiles import DEFAULT_TILE_PX, Tile, plan

__version__ = version("imageserver-export")

__all__ = [
    "DEFAULT_TILE_PX",
    "EEA_FOLDER",
    "EEA_URL",
    "Bounds",
    "Export",
    "Grid",
    "ImageServer",
    "ImageServerError",
    "Interpolation",
    "Mosaic",
    "Send",
    "ServiceInfo",
    "Tile",
    "__version__",
    "check_image",
    "eea",
    "export_params",
    "list_services",
    "parse_attribute_table",
    "parse_info",
    "parse_services",
    "plan",
]
