from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import rasterio
from mcp.server.mcpserver.exceptions import ToolError

from imageserver_export import mcp_server
from imageserver_export.mcp_server import label_of, server, service_url, summarize

#: The values the fake service returns, cycling over the pixels of each tile.
PATTERN = (10, 30, 254, 255)


def _tiff(width: int, height: int) -> bytes:
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    band = np.resize(np.array(PATTERN, dtype="uint8"), width * height).reshape(height, width)
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="uint8",
            crs="EPSG:3035",
            transform=from_origin(0, 0, 10, 10),
        ) as dst:
            dst.write(band, 1)
        return bytes(mem.read())


def _api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/GioLandPublic"):
        services = [
            {"name": "GioLandPublic/HRL_ImperviousnessDensity_2018", "type": "ImageServer"},
            {"name": "GioLandPublic/Other", "type": "MapServer"},
        ]
        return httpx.Response(200, json={"services": services})
    if "Broken" in path:
        raise httpx.ConnectError("down", request=request)
    if path.endswith("/exportImage"):
        w, h = (int(v) for v in request.url.params["size"].split(","))
        return httpx.Response(200, content=_tiff(w, h), headers={"content-type": "image/tiff"})
    if path.endswith("/rasterAttributeTable"):
        rows = [
            {"Value": 10, "Class_name": "10% imperviousness value"},
            {"Value": 30, "Class_name": "30% imperviousness value"},
            {"Value": 254, "class_name": "unclassifiable"},
            {"Value": 255, "Class_name": ""},
        ]
        return httpx.Response(200, json={"features": [{"attributes": r} for r in rows]})
    if path.endswith("/ImageServer"):
        return httpx.Response(
            200,
            json={
                "name": "HRL_ImperviousnessDensity_2018",
                "pixelType": "U8",
                "pixelSizeX": 10,
                "pixelSizeY": 10,
                "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                "spatialReference": {"wkid": 3035},
                "hasRasterAttributeTable": "Plain" not in path,
            },
        )
    raise AssertionError(path)


def _server(tmp_path: Path | None = None, **kw: Any) -> Any:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_api))
    return server(client, output=tmp_path, **kw)


async def _call(mcp: Any, tool: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(tool, arguments)
    assert not result.is_error, result.content
    structured = result.structured_content
    if structured is None:  # pragma: no cover - every tool here is typed
        return json.loads(result.content[0].text)
    return structured.get("result", structured)


async def _refused(mcp: Any, tool: str, match: str, **arguments: Any) -> None:
    with pytest.raises(ToolError, match=match):
        await mcp.call_tool(tool, arguments)


BOX = [9.0, 45.4, 9.2, 45.5]


async def test_tools_are_listed_and_only_export_writes() -> None:
    tools = {t.name: t for t in await _server().list_tools()}
    assert set(tools) == {
        "list_layers",
        "describe_layer",
        "attribute_table",
        "sample_point",
        "area_summary",
        "export_area",
    }
    for name, tool in tools.items():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is (name != "export_area")


async def test_layers_description_and_table() -> None:
    mcp = _server()
    assert await _call(mcp, "list_layers") == ["HRL_ImperviousnessDensity_2018"]
    found = await _call(mcp, "describe_layer", service="HRL_ImperviousnessDensity_2018")
    assert found["pixel_type"] == "U8" and found["crs"] == 3035
    assert found["url"].endswith("/GioLandPublic/HRL_ImperviousnessDensity_2018/ImageServer")
    assert found["value_labels"] == {
        "10": "10% imperviousness value",
        "30": "30% imperviousness value",
        "254": "unclassifiable",
    }
    plain = await _call(
        mcp, "describe_layer", service="https://e.org/rest/services/Plain/ImageServer/"
    )
    assert plain["value_labels"] == {}
    assert len(await _call(mcp, "attribute_table", service="X", limit=2)) == 2
    await _refused(mcp, "attribute_table", "limit", service="X", limit=0)


async def test_sample_point_gives_the_value_and_its_label() -> None:
    mcp = _server()
    found = await _call(
        mcp, "sample_point", service="HRL_ImperviousnessDensity_2018", lon=9.19, lat=45.46
    )
    assert found == {"value": 10, "label": "10% imperviousness value", "res": 10, "crs": 3035}
    coarse = await _call(mcp, "sample_point", service="X", lon=9.19, lat=45.46, res=100)
    assert coarse["res"] == 100
    await _refused(mcp, "sample_point", "degrees", service="X", lon=200, lat=45)


async def test_area_summary_keeps_sentinels_out_of_the_mean() -> None:
    mcp = _server()
    found = await _call(mcp, "area_summary", service="X", bbox=BOX, res=100, valid_range=[0, 100])
    assert found["valid_fraction"] == pytest.approx(0.5, abs=0.01)
    assert found["mean"] == pytest.approx(20, abs=0.5) and (found["min"], found["max"]) == (10, 30)
    labels = {c["value"]: c["label"] for c in found["classes"]}
    assert labels[10] == "10% imperviousness value" and labels[255] is None
    assert found["crs"] == 3035 and found["width"] * found["height"] == found["pixels"]


async def test_areas_are_capped_and_arguments_checked(tmp_path: Path) -> None:
    mcp = _server(tmp_path, summary_max_px=100, export_max_px=100)
    await _refused(
        mcp,
        "area_summary",
        r"over the limit of 100: use a smaller box or res ≥",
        service="X",
        bbox=BOX,
    )
    await _refused(mcp, "export_area", "over the limit", service="X", bbox=BOX, res=10, name="a")
    await _refused(mcp, "area_summary", "min_lon", service="X", bbox=[1, 2, 3])
    await _refused(mcp, "area_summary", "degrees", service="X", bbox=[10, 45, 9, 46])
    await _refused(mcp, "area_summary", "valid_range", service="X", bbox=BOX, valid_range=[5, 1])
    await _refused(mcp, "describe_layer", "EEA layer name", service="  ")
    await _refused(mcp, "describe_layer", "could not be reached", service="Broken")


async def test_export_area_writes_only_into_the_output_folder(tmp_path: Path) -> None:
    mcp = _server(tmp_path)
    found = await _call(
        mcp, "export_area", service="X", bbox=BOX, res=200, name="milan.tif", valid_range=[0, 100]
    )
    path = Path(found["path"])
    assert path == (tmp_path / "milan.tif").resolve() and path.exists()
    assert found["valid_fraction"] == pytest.approx(0.5, abs=0.01) and found["skipped"] == 0
    with rasterio.open(path) as r:
        assert r.crs.to_epsg() == 3035 and r.tags()["valid_range"] == "0,100"
    for bad in ("../escape", "", ".hidden", "a/b"):
        await _refused(
            mcp, "export_area", "plain file name", service="X", bbox=BOX, res=200, name=bad
        )


def test_pure_helpers() -> None:
    assert service_url("https://e.org/x/ImageServer/") == "https://e.org/x/ImageServer"
    assert label_of({"ClassName": "a"}) == "a" and label_of({"Value": 1}) is None
    floats = summarize(np.array([[0.5, np.nan]]), valid_range=None, nodata=None)
    assert "classes" not in floats and floats["valid_pixels"] == 2
    empty = summarize(np.full((2, 2), 255, dtype="uint8"), valid_range=(0, 100), nodata=255)
    assert empty["valid_pixels"] == 0 and "mean" not in empty
    assert (
        summarize(np.zeros((0, 0), dtype="uint8"), valid_range=None, nodata=None)["valid_fraction"]
        == 0.0
    )


async def test_own_client_is_closed_at_shutdown() -> None:
    client = httpx.AsyncClient()
    async with mcp_server._closing(client, own=False):
        pass
    assert not client.is_closed
    async with mcp_server._closing(client, own=True):
        pass
    assert client.is_closed


def test_options_and_http() -> None:
    default = mcp_server.options([], environ={})
    assert (default.transport, default.host, default.port, default.allowed_host) == (
        "stdio",
        "127.0.0.1",
        8768,
        [],
    )
    assert default.output == Path("imageserver-exports")
    env = {
        "IMAGESERVER_MCP_TRANSPORT": "streamable-http",
        "IMAGESERVER_MCP_HOST": "0.0.0.0",
        "IMAGESERVER_MCP_PORT": "9000",
        "IMAGESERVER_MCP_ALLOWED_HOSTS": "imageserver-mcp:*",
        "IMAGESERVER_MCP_OUTPUT": "/data",
    }
    from_env = mcp_server.options([], environ=env)
    assert (from_env.transport, from_env.port, from_env.output) == (
        "streamable-http",
        9000,
        Path("/data"),
    )
    assert mcp_server.http_options(from_env)["transport_security"].allowed_hosts == [
        "imageserver-mcp:*"
    ]
    assert mcp_server.http_options(default) == {
        "host": "127.0.0.1",
        "port": 8768,
        "streamable_http_path": "/mcp",
    }
    with pytest.raises(SystemExit):
        mcp_server.options([], environ={"IMAGESERVER_MCP_TRANSPORT": "sse"})


def test_health_and_mcp_over_http() -> None:
    from starlette.testclient import TestClient

    with TestClient(server().streamable_http_app()) as http:
        health = http.get("/health")
        assert health.status_code == 200 and health.json()["status"] == "ok"
        assert 400 <= http.get("/mcp").status_code < 500  # no session: refused, but alive
