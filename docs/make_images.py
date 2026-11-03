"""Draw the README images in docs/images from a real export.

    uv run --group docs python docs/make_images.py [DATA_DIR] [OUT_DIR]

The imperviousness of the Milan area is exported with imageserver-export
from the EEA's open ImageServer into DATA_DIR (default: a temporary folder);
the banner and the map are drawn from it, the two diagrams by hand.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio

from imageserver_export import Grid, ImageServer, eea

DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "images"
OUT.mkdir(parents=True, exist_ok=True)

#: Milan and its belt, in longitude/latitude.
MILAN = (8.95, 45.33, 9.45, 45.62)

STYLE = """<style>
  .bg{fill:#fcfcfb} .t1{fill:#0b0b0b} .t2{fill:#52514e} .chip{fill:#f0efec}
  .ln{stroke:#7a7974} .box{fill:#f0efec;stroke:#b9b8b2}
  @media (prefers-color-scheme: dark){ .bg{fill:#1a1a19} .t1{fill:#f0efec} .t2{fill:#c3c2b7}
    .chip{fill:#2a2a28} .ln{stroke:#9a9993} .box{fill:#2a2a28;stroke:#55544f} }
  text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
</style>"""

#: Imperviousness, from bare soil to sealed: a sequential ramp.
RAMP = ["#f0efec", "#f6d9b8", "#efb27a", "#e38443", "#c95a1f", "#9b3a12", "#5e2208"]


def colour(value: float) -> str:
    return RAMP[min(len(RAMP) - 1, int(value / 100 * (len(RAMP) - 1) + 0.5))]


async def export() -> Path:
    path = DATA / "milan_imperviousness_100m.tif"
    if not path.exists():
        async with ImageServer(eea("HRL_ImperviousnessDensity_2018")) as server:
            grid = Grid.from_lonlat(MILAN, res=100)
            result = await server.export(grid, path, valid_range=(0, 100))
            print(f"exported {result.width}x{result.height} in {result.tiles} tiles")
    return path


def blocks(band: np.ndarray, cols: int, rows: int) -> np.ndarray:
    """Mean imperviousness of ``cols`` x ``rows`` blocks, sea and no-data left out."""
    h, w = band.shape
    out = np.zeros((rows, cols))
    for r in range(rows):
        for c in range(cols):
            cell = band[r * h // rows : (r + 1) * h // rows, c * w // cols : (c + 1) * w // cols]
            valid = cell[cell <= 100]
            out[r, c] = float(valid.mean()) if valid.size else 0.0
    return out


def banner(band: np.ndarray) -> None:
    cols, rows, size, gap = 18, 8, 20, 2
    mean = blocks(band, cols, rows)
    squares = "\n".join(
        f'<rect x="{560 + c * (size + gap)}" y="{26 + r * (size + gap)}" '
        f'width="{size}" height="{size}" rx="3" fill="{colour(mean[r, c])}"/>'
        for r in range(rows)
        for c in range(cols)
    )
    chips = [("ArcGIS", 84), ("GeoTIFF", 90), ("Copernicus", 112), ("async", 70)]
    x, chip_svg = 40, []
    for label, width in chips:
        chip_svg.append(
            f'<rect class="chip" x="{x}" y="172" width="{width}" height="28" rx="14"/>'
            f'<text class="t2" x="{x + width // 2}" y="191" text-anchor="middle">{label}</text>'
        )
        x += width + 8
    (
        OUT / "banner.svg"
    ).write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 230" role="img" aria-label="imageserver-export: open land-cover rasters of Europe, as clean GeoTIFFs, in a few lines of Python">
{STYLE}
<rect class="bg" width="1000" height="230" rx="14"/>
<text class="t1" x="40" y="88" font-size="40" font-weight="700">imageserver-export</text>
<text class="t2" x="40" y="124" font-size="18">Open land-cover maps of Europe, as clean</text>
<text class="t2" x="40" y="148" font-size="18">GeoTIFFs, in a few lines of Python.</text>
<g font-size="13">{"".join(chip_svg)}</g>
{squares}
<text class="t2" x="956" y="214" font-size="11" text-anchor="end">Milan, sealed soil (Copernicus HRL 2018)</text>
</svg>
""")


def milan_map(band: np.ndarray) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    data = np.where(band <= 100, band, np.nan).astype(float)
    cmap = LinearSegmentedColormap.from_list("sealed", RAMP)
    cmap.set_bad("#ffffff")
    fig, ax = plt.subplots(figsize=(9, 6.2), dpi=120)
    image = ax.imshow(data, cmap=cmap, vmin=0, vmax=100, interpolation="nearest")
    ax.set_axis_off()
    bar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.02)
    bar.set_label("sealed soil, % of each 100 m pixel")
    ax.set_title(
        "Milan and its belt: how much of the ground is sealed\n"
        "Copernicus HRL Imperviousness Density 2018, exported at 100 m with imageserver-export",
        fontsize=11,
        loc="left",
    )
    fig.text(
        0.01,
        0.01,
        "© European Union, Copernicus Land Monitoring Service, EEA",
        fontsize=8,
        color="#52514e",
    )
    fig.savefig(OUT / "map-milan.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def tiles_diagram() -> None:
    def label(
        x: int, y: int, text: str, cls: str = "t2", size: int = 13, anchor: str = "middle"
    ) -> str:
        return f'<text class="{cls}" x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}">{text}</text>'

    grid_cells = []
    for r in range(3):
        for c in range(4):
            w = 46 if c < 3 else 22
            grid_cells.append(
                f'<rect x="{372 + c * 50}" y="{58 + r * 42}" width="{w}" height="38" rx="3" '
                f'fill="{["#8db8ec", "#2a78d6", "#5f9ae0"][(r + c) % 3]}"/>'
            )
    (
        OUT / "tiles.svg"
    ).write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 260" role="img" aria-label="How an export works: an area becomes a grid on whole pixels, the grid becomes tiles, the tiles become one GeoTIFF written atomically">
{STYLE}
<rect class="bg" width="1000" height="260" rx="14"/>
<rect class="box" x="40" y="58" width="200" height="122" rx="10"/>
<path d="M70 150 C 90 90, 150 70, 210 100 L 200 160 Z" fill="#efb27a" opacity=".8"/>
{label(140, 210, "1. an area", "t1", 15)}
{label(140, 232, "lon/lat, any shape of box")}
<line class="ln" x1="252" y1="119" x2="352" y2="119" stroke-width="2" marker-end="url(#a)"/>
{label(302, 108, "snap", "t2", 12)}
{"".join(grid_cells)}
{label(470, 210, "2. a grid of whole pixels, cut in tiles", "t1", 15)}
{label(470, 232, "every edge a multiple of the resolution")}
<line class="ln" x1="586" y1="119" x2="676" y2="119" stroke-width="2" marker-end="url(#a)"/>
{label(631, 108, "exportImage", "t2", 12)}
<rect class="box" x="690" y="58" width="122" height="122" rx="10"/>
<rect x="702" y="70" width="98" height="98" rx="4" fill="#2a78d6" opacity=".85"/>
{label(751, 124, "GeoTIFF", "t1", 13)}
<rect class="chip" x="830" y="70" width="130" height="30" rx="15"/>{label(895, 90, "out.tif.part", "t2", 12)}
<line class="ln" x1="895" y1="104" x2="895" y2="132" stroke-width="2" marker-end="url(#a)"/>
<rect class="chip" x="830" y="136" width="130" height="30" rx="15"/>{label(895, 156, "out.tif", "t1", 12)}
{label(825, 210, "3. one file, renamed only when whole", "t1", 15)}
{label(825, 232, "a failed tile leaves nothing behind")}
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#7a7974"/></marker></defs>
</svg>
""")


def sentinel_diagram() -> None:
    def cell(x: int, y: int, fill: str, text: str, text_cls: str = "t1") -> str:
        return (
            f'<rect x="{x}" y="{y}" width="92" height="92" rx="6" fill="{fill}"/>'
            f'<text class="{text_cls}" x="{x + 46}" y="{y + 54}" font-size="20" '
            f'font-weight="600" text-anchor="middle">{text}</text>'
        )

    (
        OUT / "sentinel.svg"
    ).write_text(f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 256" role="img" aria-label="Why nearest neighbour: between a 0 percent pixel and a 255 no-data pixel, bilinear resampling invents a plausible 64 percent; nearest neighbour keeps the two real values">
{STYLE}
<rect class="bg" width="1000" height="256" rx="14"/>
<text class="t1" x="40" y="44" font-size="17" font-weight="700">The source: two real pixels</text>
{cell(40, 66, "#f0efec", "0 %")}{cell(136, 66, "#3b3a37", "255", "chip")}
<text class="t2" x="40" y="190" font-size="13">0 % sealed, and 255 = "no data"</text>
<text class="t2" x="40" y="210" font-size="13">(the sea, or outside Europe)</text>
<text class="t1" x="360" y="44" font-size="17" font-weight="700">Bilinear: a value nobody measured</text>
{cell(360, 66, "#f0efec", "0 %")}{cell(456, 66, "#e38443", "64 %")}{cell(552, 66, "#3b3a37", "255", "chip")}
<text class="t2" x="360" y="190" font-size="13">A quarter of the way from 0 to 255 is 64:</text>
<text class="t2" x="360" y="210" font-size="13">a believable "64 % sealed",</text>
<text class="t2" x="360" y="230" font-size="13">exactly where there is no data.</text>
<text class="t1" x="720" y="44" font-size="17" font-weight="700">Nearest neighbour: kept</text>
{cell(720, 66, "#f0efec", "0 %")}{cell(816, 66, "#3b3a37", "255", "chip")}
<text class="t2" x="720" y="190" font-size="13">Every output pixel is a real one.</text>
<text class="t2" x="720" y="210" font-size="13">The default of this library.</text>
</svg>
""")


def main() -> None:
    path = asyncio.run(export())
    with rasterio.open(path) as src:
        band = src.read(1)
    banner(band)
    milan_map(band)
    tiles_diagram()
    sentinel_diagram()
    print("written:", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
