from __future__ import annotations

import json

import pytest

from imageserver_export import (
    ImageServerError,
    check_image,
    eea,
    export_params,
    parse_attribute_table,
    parse_info,
    parse_services,
)

# Reduced from the real description of HRL_ImperviousnessDensity_2018 (EEA).
_INFO = {
    "name": "GioLandPublic/HRL_ImperviousnessDensity_2018",
    "pixelType": "U8",
    "bandCount": 1,
    "pixelSizeX": 10,
    "pixelSizeY": 10,
    "maxImageHeight": 4100,
    "maxImageWidth": 15000,
    "spatialReference": {"wkid": 102100, "latestWkid": 3857},
    "extent": {"xmin": -3551137.0, "ymin": 3128826.0, "xmax": 5142393.0, "ymax": 11831356.0},
    "hasRasterAttributeTable": True,
}


def test_eea_url() -> None:
    assert eea("HRL_ImperviousnessDensity_2018") == (
        "https://image.discomap.eea.europa.eu/arcgis/rest/services/"
        "GioLandPublic/HRL_ImperviousnessDensity_2018/ImageServer"
    )


def test_export_params() -> None:
    p = export_params((1.0, 2.0, 3.0, 4.0), width=10, height=20, crs=3035)
    assert p["bbox"] == "1.000000,2.000000,3.000000,4.000000"
    assert (p["bboxSR"], p["imageSR"], p["size"]) == ("3035", "3035", "10,20")
    assert (p["format"], p["pixelType"], p["f"]) == ("tiff", "U8", "image")
    assert p["interpolation"] == "RSP_NearestNeighbor"


def test_check_image() -> None:
    assert check_image(200, "image/tiff", b"II*\x00") == b"II*\x00"
    # The ImageServer way to say no: HTTP 200 and a JSON body.
    with pytest.raises(ImageServerError, match="Invalid bbox"):
        check_image(200, "application/json", b'{"error":{"message":"Invalid bbox"}}')
    with pytest.raises(ImageServerError, match="refused"):
        check_image(200, "", b'{"error":{}}')
    with pytest.raises(ImageServerError, match="HTTP 404"):
        check_image(404, "text/html", b"not found")


def test_parse_info() -> None:
    i = parse_info(json.dumps(_INFO))
    assert (i.pixel_type, i.band_count, i.pixel_size) == ("U8", 1, (10.0, 10.0))
    assert (i.max_width, i.max_height, i.crs) == (15000, 4100, 3857)
    assert i.has_attribute_table and i.extent[0] == -3551137.0
    with pytest.raises(ImageServerError, match="missing"):
        parse_info(json.dumps({"name": "x"}))
    with pytest.raises(ImageServerError, match="not return JSON"):
        parse_info(b"<html>")
    with pytest.raises(ImageServerError, match="Token Required"):
        parse_info(json.dumps({"error": {"code": 499, "message": "Token Required"}}))
    with pytest.raises(ImageServerError, match="boom"):
        parse_info(json.dumps({"error": "boom"}))


def test_parse_attribute_table_and_services() -> None:
    rat = {"features": [{"attributes": {"Value": 0, "Class": "non sealed"}}, {}]}
    assert parse_attribute_table(json.dumps(rat)) == [{"Value": 0, "Class": "non sealed"}, {}]
    with pytest.raises(ImageServerError):
        parse_attribute_table(b"{}")
    folder = {
        "services": [{"name": "A", "type": "ImageServer"}, {"name": "B", "type": "MapServer"}]
    }
    assert parse_services(json.dumps(folder)) == ["A"]
    with pytest.raises(ImageServerError):
        parse_services(b"[]")
