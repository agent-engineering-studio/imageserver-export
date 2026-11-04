"""An MCP server over ArcGIS ImageServers, for AI agents: ``pip install "imageserver-export[mcp]"``.

    imageserver-mcp                                     # stdio: Claude Desktop, IDEs
    imageserver-mcp --transport streamable-http --host 0.0.0.0 --port 8768

Over HTTP the MCP endpoint is ``/mcp`` and ``GET /health`` answers
``{"status": "ok"}`` for container healthchecks. Every option also reads an
environment variable (``IMAGESERVER_MCP_TRANSPORT``, ``IMAGESERVER_MCP_HOST``,
``IMAGESERVER_MCP_PORT``, ``IMAGESERVER_MCP_ALLOWED_HOSTS``,
``IMAGESERVER_MCP_OUTPUT``), so a compose file needs no command line.

A ``service`` is either the name of one of the European Environment Agency's
open Copernicus Land layers (``HRL_ImperviousnessDensity_2018``) or the full
URL of any ImageServer. Every number an agent gets back (a value, a mean, a
share, a class count) is computed here, by the library, never left to the
model: the agent finds the data and explains it. Only ``export_area`` writes,
and only into the output folder; areas are capped in pixels, so one call can
never ask a server for a continent at 10 m.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any, cast

import httpx

from imageserver_export.client import ImageServer, list_services
from imageserver_export.errors import ImageServerError
from imageserver_export.grid import Bounds, Grid
from imageserver_export.mosaic import valid_mask
from imageserver_export.service import EEA_FOLDER, EEA_URL, eea

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        'imageserver_export.mcp_server needs the mcp extra: pip install "imageserver-export[mcp]"'
    ) from exc

INSTRUCTIONS = """\
Rasters from ArcGIS ImageServers. By default, the European Environment
Agency's open Copernicus Land Monitoring Service layers of Europe at 10 m:
sealed soil (HRL_ImperviousnessDensity_2018), tree cover
(HRL_TreeCoverDensity_2018), forest type (HRL_DominantLeafType2018),
grassland, water and wetness, small woody features. A `service` is one of
those names (list_layers) or the full URL of any ImageServer.

Start with describe_layer: it says the pixel type, the native resolution and
what each value means. Percentage layers use 0-100 and keep sentinels in the
same band (254 unclassifiable, 255 no data): pass valid_range [0, 100] so they
stay out of every mean. Boxes are [min_lon, min_lat, max_lon, max_lat].
sample_point gives the value at a place; area_summary the mean, the valid
share and the class counts of a box; export_area writes a GeoTIFF.

Report the numbers the tools return; do not compute your own. Credit the EEA
layers as "© European Union, Copernicus Land Monitoring Service, European
Environment Agency (EEA)".
"""

#: Pixel types of ``pixelType`` and the numpy dtype that holds them.
DTYPES = {
    "U1": "uint8",
    "U2": "uint8",
    "U4": "uint8",
    "U8": "uint8",
    "S8": "int8",
    "U16": "uint16",
    "S16": "int16",
    "U32": "uint32",
    "S32": "int32",
    "F32": "float32",
    "F64": "float64",
}
#: The most pixels area_summary reads, and export_area writes, by default.
SUMMARY_MAX_PX = 4_000_000
EXPORT_MAX_PX = 25_000_000
#: The most classes area_summary lists, largest first.
_TOP_CLASSES = 30

_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


def service_url(service: str) -> str:
    """The URL of ``service``: an EEA layer name, or already a URL."""
    service = service.strip()
    if not service:
        raise ValueError("service is an EEA layer name or an ImageServer URL")
    if service.startswith(("http://", "https://")):
        return service.rstrip("/")
    return eea(service)


def label_of(row: Mapping[str, Any]) -> str | None:
    """The class label of an attribute-table row (``Class_name`` or ``class_name``)."""
    return next(
        (str(v) for k, v in row.items() if k.lower() in ("class_name", "classname") and v), None
    )


def _bbox(bbox: Sequence[float]) -> Bounds:
    if len(bbox) != 4:
        raise ValueError("bbox is [min_lon, min_lat, max_lon, max_lat]")
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if not (-180 <= x0 < x1 <= 180 and -90 <= y0 < y1 <= 90):
        raise ValueError(f"bbox is [min_lon, min_lat, max_lon, max_lat] in degrees: {list(bbox)}")
    return (x0, y0, x1, y1)


def _range(valid_range: Sequence[float] | None) -> tuple[float, float] | None:
    if valid_range is None:
        return None
    if len(valid_range) != 2 or valid_range[0] > valid_range[1]:
        raise ValueError("valid_range is [low, high]")
    return (float(valid_range[0]), float(valid_range[1]))


def _capped(grid: Grid, max_px: int) -> Grid:
    if grid.width * grid.height > max_px:
        coarser = grid.res * math.ceil(math.sqrt(grid.width * grid.height / max_px))
        raise ValueError(
            f"{grid.width}x{grid.height} pixels at {grid.res:g} m is over the limit of "
            f"{max_px:,}: use a smaller box or res ≥ {coarser:g}"
        )
    return grid


def summarize(
    band: Any, *, valid_range: tuple[float, float] | None, nodata: float | None
) -> dict[str, Any]:
    """Valid share, mean, min, max and class counts of a band: the numbers an agent reports."""
    import numpy as np

    mask = valid_mask(band, valid_range=valid_range, nodata=nodata)
    values = band[mask]
    out: dict[str, Any] = {
        "pixels": int(band.size),
        "valid_pixels": int(values.size),
        "valid_fraction": round(float(values.size) / band.size, 4) if band.size else 0.0,
    }
    if values.size:
        out |= {
            "mean": round(float(values.mean()), 3),
            "min": float(values.min()),
            "max": float(values.max()),
        }
    if band.dtype.kind in "iu":
        classes, counts = np.unique(band, return_counts=True)
        order = np.argsort(counts)[::-1][:_TOP_CLASSES]
        out["classes"] = [
            {
                "value": int(classes[i]),
                "pixels": int(counts[i]),
                "share": round(float(counts[i]) / band.size, 4),
            }
            for i in order
        ]
    return out


@asynccontextmanager
async def _closing(client: httpx.AsyncClient, *, own: bool) -> AsyncIterator[None]:
    """The server's lifespan: close the HTTP client at shutdown if it is ours."""
    try:
        yield
    finally:
        if own:
            await client.aclose()


def _tool_errors[**P, T](call: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Service refusals, HTTP failures and bad arguments as a ToolError the agent can read."""

    @wraps(call)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await call(*args, **kwargs)
        except (ImageServerError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"the service could not be reached: {exc}") from exc

    return wrapper


def server(
    client: httpx.AsyncClient | None = None,
    *,
    output: Path | None = None,
    summary_max_px: int = SUMMARY_MAX_PX,
    export_max_px: int = EXPORT_MAX_PX,
) -> MCPServer[Any]:
    """The MCP server; ``client`` defaults to one it opens and closes itself.

    ``export_area`` writes into ``output`` (default ``$IMAGESERVER_MCP_OUTPUT``,
    else ``./imageserver-exports``).
    """
    http = client or httpx.AsyncClient(follow_redirects=True)
    folder = output or Path(os.environ.get("IMAGESERVER_MCP_OUTPUT", "imageserver-exports"))
    labels: dict[str, dict[int, str]] = {}

    def open_service(service: str) -> ImageServer:
        return ImageServer(service_url(service), http)

    async def labels_of(server: ImageServer) -> dict[int, str]:
        if server.url not in labels:
            info = await server.info()
            rows = await server.attribute_table() if info.has_attribute_table else []
            labels[server.url] = {
                int(r["Value"]): label
                for r in rows
                if "Value" in r and (label := label_of(r)) is not None
            }
        return labels[server.url]

    async def read_area(
        server: ImageServer,
        grid: Grid,
        *,
        valid_range: tuple[float, float] | None,
        nodata: float | None,
    ) -> Any:
        """The band of ``grid``, through a temporary GeoTIFF."""
        import rasterio

        info = await server.info()
        dtype = DTYPES.get(info.pixel_type, "float64")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "area.tif"
            await server.export(
                grid,
                path,
                pixel_type=info.pixel_type,
                dtype=dtype,
                nodata=nodata,
                valid_range=valid_range,
            )
            with rasterio.open(path) as src:
                return src.read(1)

    mcp: MCPServer[Any] = MCPServer(
        "imageserver-export",
        instructions=INSTRUCTIONS,
        lifespan=lambda _server: _closing(http, own=client is None),
    )

    async def health(_request: Request) -> Response:
        """``GET /health`` over HTTP, for container healthchecks."""
        return JSONResponse({"status": "ok", "server": "imageserver-export"})

    # custom_route is untyped in the SDK: register the typed handler through a cast.
    route = cast(Callable[[Any], Any], mcp.custom_route("/health", methods=["GET"]))
    route(health)

    @mcp.tool(annotations=_READ)
    @_tool_errors
    async def list_layers(folder_url: str = f"{EEA_URL}/{EEA_FOLDER}") -> list[str]:
        """The ImageServer layers of a folder; by default the EEA's Copernicus Land layers."""
        names = await list_services(folder_url, http)
        return [n.rsplit("/", 1)[-1] for n in names]

    @mcp.tool(annotations=_READ)
    @_tool_errors
    async def describe_layer(service: str) -> dict[str, Any]:
        """What a layer is: pixel type, native resolution, CRS, extent, and its value labels."""
        server = open_service(service)
        info = await server.info()
        found = asdict(info) | {"url": server.url}
        found["value_labels"] = {str(k): v for k, v in (await labels_of(server)).items()}
        return found

    @mcp.tool(annotations=_READ)
    @_tool_errors
    async def attribute_table(service: str, limit: int = 200) -> list[dict[str, Any]]:
        """The raster attribute table of a thematic layer: every row, up to ``limit``."""
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        return (await open_service(service).attribute_table())[:limit]

    @mcp.tool(annotations=_READ)
    @_tool_errors
    async def sample_point(
        service: str, lon: float, lat: float, res: float | None = None
    ) -> dict[str, Any]:
        """The pixel value at a place, with its label.

        ``res`` is the pixel side in metres, in LAEA Europe (default: the
        service's pixel size, 10 m for the EEA layers).
        """
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("lon and lat are in degrees")
        server = open_service(service)
        info = await server.info()
        from rasterio.warp import transform

        size = res or info.pixel_size[0]
        xs, ys = transform(4326, 3035, [lon], [lat])
        # The one pixel of a grid of ``size`` (on whole multiples) that holds the point.
        grid = Grid(
            crs=3035,
            left=math.floor(xs[0] / size) * size,
            top=(math.floor(ys[0] / size) + 1) * size,
            res=size,
            width=1,
            height=1,
        )
        band = await read_area(server, grid, valid_range=None, nodata=None)
        value = band[0, 0].item()
        label = (await labels_of(server)).get(int(value)) if band.dtype.kind in "iu" else None
        return {"value": value, "label": label, "res": size, "crs": grid.crs}

    @mcp.tool(annotations=_READ)
    @_tool_errors
    async def area_summary(
        service: str,
        bbox: list[float],
        res: float = 100,
        valid_range: list[float] | None = None,
        nodata: float | None = 255,
    ) -> dict[str, Any]:
        """Mean, min, max, valid share and class counts of a box, at ``res`` metres.

        Pass ``valid_range`` [0, 100] for percentage layers, so sentinels stay
        out of the mean. Class counts come with their labels.
        """
        server = open_service(service)
        valid = _range(valid_range)
        grid = _capped(Grid.from_lonlat(_bbox(bbox), res=res), summary_max_px)
        found = summarize(
            await read_area(server, grid, valid_range=valid, nodata=nodata),
            valid_range=valid,
            nodata=nodata,
        )
        names = await labels_of(server)
        for row in found.get("classes", []):
            row["label"] = names.get(row["value"])
        return found | {"width": grid.width, "height": grid.height, "res": res, "crs": grid.crs}

    @mcp.tool(annotations=_WRITE)
    @_tool_errors
    async def export_area(
        service: str,
        bbox: list[float],
        res: float,
        name: str,
        valid_range: list[float] | None = None,
        skip_empty: bool = False,
    ) -> dict[str, Any]:
        """Write a box of a layer as a GeoTIFF ``<name>.tif`` in the output folder.

        LAEA Europe (EPSG:3035), nearest neighbour, written whole or not at
        all. Returns the path, size and valid share.
        """
        if not name or Path(name).name != name or name.startswith("."):
            raise ValueError("name is a plain file name, without folders")
        server = open_service(service)
        valid = _range(valid_range)
        grid = _capped(Grid.from_lonlat(_bbox(bbox), res=res), export_max_px)
        info = await server.info()
        dest = folder / f"{name.removesuffix('.tif')}.tif"
        result = await server.export(
            grid,
            dest,
            pixel_type=info.pixel_type,
            dtype=DTYPES.get(info.pixel_type, "float64"),
            valid_range=valid,
            skip_empty=skip_empty,
        )
        return {
            "path": str(result.path.resolve()),
            "width": result.width,
            "height": result.height,
            "tiles": result.tiles,
            "skipped": result.skipped,
            "valid_fraction": round(result.valid_fraction, 4),
            "crs": grid.crs,
        }

    return mcp


def options(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ
) -> argparse.Namespace:
    """The command line of ``imageserver-mcp``, each default from its environment variable."""
    top = argparse.ArgumentParser(
        prog="imageserver-mcp", description="ArcGIS ImageServers as an MCP server."
    )
    top.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=environ.get("IMAGESERVER_MCP_TRANSPORT", "stdio"),
        help="stdio (default) or streamable-http, served at /mcp",
    )
    top.add_argument(
        "--host",
        default=environ.get("IMAGESERVER_MCP_HOST", "127.0.0.1"),
        help="HTTP address (default 127.0.0.1; 0.0.0.0 in a container)",
    )
    top.add_argument("--port", type=int, default=int(environ.get("IMAGESERVER_MCP_PORT", "8768")))
    top.add_argument(
        "--allowed-host",
        action="append",
        default=[
            h for h in environ.get("IMAGESERVER_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
        ],
        metavar="HOST[:PORT]",
        help="accept only these Host headers (DNS rebinding protection); repeatable",
    )
    top.add_argument(
        "--output",
        type=Path,
        default=Path(environ.get("IMAGESERVER_MCP_OUTPUT", "imageserver-exports")),
        help="the folder export_area writes into",
    )
    args = top.parse_args(argv)
    if args.transport not in ("stdio", "streamable-http"):
        top.error(f"unknown transport {args.transport!r}")
    return args


def http_options(args: argparse.Namespace) -> dict[str, Any]:
    """The keyword arguments of ``MCPServer.run`` for the HTTP transport."""
    found: dict[str, Any] = {"host": args.host, "port": args.port, "streamable_http_path": "/mcp"}
    if args.allowed_host:
        found["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[h.strip() for h in args.allowed_host],
        )
    return found


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - runs a server
    """Entry point of ``imageserver-mcp``."""
    logging.getLogger("httpx").setLevel(logging.WARNING)  # not one line per request
    args = options(argv)
    if args.transport == "stdio":
        server(output=args.output).run("stdio")
    else:
        server(output=args.output).run("streamable-http", **http_options(args))
