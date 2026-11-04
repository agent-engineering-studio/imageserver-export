# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-04

### Added

- Provenance in every export: the GeoTIFF records `source` (the service URL),
  `interpolation`, `pixel_type`, `valid_range`, `created` (UTC), `software`
  and `valid_px` as metadata tags. `export(..., tags={...})` adds to them;
  `Mosaic(..., tags=...)` writes them when the mosaic is whole.
- `export(..., skip_empty=True)`: one coarse overview first, then no request
  for the tiles with no data on or next to them, filled with `nodata`.
  Off by default: a feature smaller than an overview pixel can be missed.
- `export(..., resume=True)`: checked tiles are kept in `<dest>.tiles/` and
  reused by the next run after a failure; deleted once the file is whole.
- `Export.skipped` and `Export.reused`.
- `aggregate(path, factor, valid_range=...)`: the mean and the valid share
  of each `factor` x `factor` cell, read one strip at a time.
- `Mosaic.fill(tile)`, `occupied()`, `valid_mask()`, `read_band()`.
- `imageserver-mcp`, an MCP server for AI agents (`pip install
  "imageserver-export[mcp]"`): `list_layers`, `describe_layer`,
  `attribute_table`, `sample_point`, `area_summary`, `export_area`. Every
  number is computed by the library; areas are capped in pixels; only
  `export_area` writes, into the output folder. stdio or streamable HTTP
  (`/mcp`, `/health`).

## [0.1.0] - 2026-11-03

### Added

- `ImageServer.export(grid, dest)`: an area of any ArcGIS ImageServer as one
  tiled, compressed GeoTIFF, written to `.part` and renamed only when whole.
- `Grid.from_lonlat` / `Grid.snap`: a target grid on whole pixels, reprojected
  with densified edges; `plan()` cuts it into tiles the server accepts.
- `export_params` / `check_image`: one `exportImage` request, and the JSON
  error an ImageServer sends with HTTP 200.
- `ImageServer.info()`, `attribute_table()`, `list_services()`, and `eea(name)`
  for the European Environment Agency's open Copernicus Land layers.
- `send=`: route every request through your own HTTP layer.
