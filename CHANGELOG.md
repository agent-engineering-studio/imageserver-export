# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Provenance in every export: the GeoTIFF records `source` (the service URL),
  `interpolation`, `pixel_type`, `valid_range`, `created` (UTC), `software`
  and `valid_px` as metadata tags. `export(..., tags={...})` adds to them;
  `Mosaic(..., tags=...)` writes them when the mosaic is whole.

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
