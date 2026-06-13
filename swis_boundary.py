#!/usr/bin/env python3
"""Derive an APPROXIMATE boundary polygon for Western Power's South West
Interconnected System (SWIS).

No authoritative SWIS boundary polygon is published by Western Power, AEMO,
Energy Policy WA or the WA Government, so this approximates one from the actual
network: transmission overhead powerlines (WP-032) and substations / terminals /
power stations (WP-046), buffered and dissolved.

THE OUTPUT IS A DERIVED APPROXIMATION, NOT AN OFFICIAL BOUNDARY. Cite it as
such ("approximate SWIS extent derived from Western Power network data").

Data sources (Western Power, via the WA SLIP platform; source CRS EPSG:28350):
  WP-032 Transmission Overhead Powerlines  (MapServer layer 11)
  WP-046 Substations/Terminals/Power Stations (MapServer layer 15)

Both the SLIP MapServer and the GeoPackage download pages require a SLIP /
Landgate SSO login. If the MapServer query is rejected (401), download the two
GeoPackages while logged in and point this script at them with --wp032 / --wp046
(or drop them at swis_src/WP-032.gpkg and swis_src/WP-046.gpkg).

Usage:
    python3 swis_boundary.py [--buffer-km 10] [--wp032 PATH] [--wp046 PATH]
                             [--out swis_boundary.gpkg] [--plot swis_boundary.png]
                             [--geojson data/swis_boundary.geojson]

Requires: geopandas, shapely, pyproj, requests (matplotlib optional, for --plot).
"""
from __future__ import annotations

import argparse
import io
import os
from typing import Optional

import geopandas as gpd
import requests
from shapely.geometry import shape
from shapely.ops import unary_union

SRC_CRS = "EPSG:28350"   # GDA94 / MGA zone 50 — projected metres, good for buffering
OUT_CRS = "EPSG:7844"    # GDA2020 geographic — for the GeoPackage outputs
WEB_CRS = "EPSG:4326"    # WGS84 — for the GeoJSON the web map consumes

SLIP_BASE = ("https://services.slip.wa.gov.au/arcgis/rest/services/"
             "WP_Public_Secure_Services/WP_Public_Secure_Services/MapServer")
WP032_LAYER = 11   # transmission overhead powerlines (polyline)
WP046_LAYER = 15   # substations / terminals / power stations (polygon)

# SWIS published descriptor: Kalbarri (N) to Albany (S) to Kalgoorlie (E),
# ~261,000 km^2. Flag results well outside a plausible band.
SWIS_AREA_KM2 = 261_000
AREA_MIN_KM2, AREA_MAX_KM2 = 150_000, 500_000


def fetch_arcgis_layer(base_url: str, layer: int, out_sr: int = 28350,
                       page: int = 1000) -> gpd.GeoDataFrame:
    """Pull every feature from an ArcGIS REST MapServer layer as a GeoDataFrame.

    Pages with resultOffset / resultRecordCount because the server caps records
    per request. Geometry is requested in ``out_sr`` (EPSG:28350 by default).
    Raises requests.HTTPError on a rejected request (e.g. 401 without a token).
    """
    query = f"{base_url}/{layer}/query"
    feats: list = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": out_sr,
            "f": "geojson",
            "resultRecordCount": page,
            "resultOffset": offset,
        }
        resp = requests.get(query, params=params, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("features", [])
        feats.extend(batch)
        if body.get("exceededTransferLimit") and batch:
            offset += len(batch)
            continue
        if len(batch) == page:
            offset += len(batch)
            continue
        break
    gdf = gpd.GeoDataFrame.from_features(feats, crs=f"EPSG:{out_sr}")
    return gdf


def load_layer(local_path: Optional[str], base_url: str, layer: int) -> gpd.GeoDataFrame:
    """Load a layer from a local GeoPackage if given/available, else the MapServer.

    Everything is reprojected to SRC_CRS (EPSG:28350) for metric buffering.
    """
    default_local = None
    if local_path is None:
        guess = os.path.join("swis_src", f"WP-{ '032' if layer==WP032_LAYER else '046' }.gpkg")
        if os.path.exists(guess):
            default_local = guess
    path = local_path or default_local
    if path:
        gdf = gpd.read_file(path)
    else:
        gdf = fetch_arcgis_layer(base_url, layer)
    if gdf.crs is None:
        gdf = gdf.set_crs(SRC_CRS)
    return gdf.to_crs(SRC_CRS)


def combined_geometry(*gdfs: gpd.GeoDataFrame):
    """Union all geometries from the given GeoDataFrames into one shapely geom."""
    geoms = []
    for gdf in gdfs:
        geoms.extend(g for g in gdf.geometry if g is not None and not g.is_empty)
    return unary_union(geoms)


def convex_hull_boundary(combined) -> "gpd.GeoSeries":
    """Convex hull of the combined network (loosest extent)."""
    return gpd.GeoSeries([combined.convex_hull], crs=SRC_CRS)


def buffered_boundary(combined, buffer_km: float = 10.0) -> "gpd.GeoSeries":
    """Dissolved buffer of the combined network — hugs corridors more tightly
    than a convex hull. buffer_km is exposed as a parameter."""
    poly = combined.buffer(buffer_km * 1000.0)  # metres in EPSG:28350
    return gpd.GeoSeries([poly], crs=SRC_CRS)


def area_km2(geoseries: "gpd.GeoSeries") -> float:
    """Area in km^2, computed in the metric source CRS."""
    return float(geoseries.to_crs(SRC_CRS).area.sum()) / 1e6


def main() -> int:
    ap = argparse.ArgumentParser(description="Derive an approximate SWIS boundary.")
    ap.add_argument("--buffer-km", type=float, default=10.0)
    ap.add_argument("--wp032", help="local WP-032 GeoPackage (lines)")
    ap.add_argument("--wp046", help="local WP-046 GeoPackage (substations)")
    ap.add_argument("--out", default="swis_boundary.gpkg")
    ap.add_argument("--plot", default="swis_boundary.png")
    ap.add_argument("--geojson", default="data/swis_boundary.geojson",
                    help="also write the buffered boundary as WGS84 GeoJSON for the web map")
    args = ap.parse_args()

    try:
        lines = load_layer(args.wp032, SLIP_BASE, WP032_LAYER)
        subs = load_layer(args.wp046, SLIP_BASE, WP046_LAYER)
    except requests.HTTPError as e:
        raise SystemExit(
            f"Could not fetch network data from SLIP ({e}). The MapServer needs a "
            "SLIP login. Download the WP-032 and WP-046 GeoPackages while logged in "
            "at https://data-downloads.slip.wa.gov.au/ and re-run with --wp032/--wp046 "
            "(or place them at swis_src/WP-032.gpkg and swis_src/WP-046.gpkg)."
        )

    print(f"WP-032 lines: {len(lines)} features | WP-046 substations: {len(subs)} features")
    combined = combined_geometry(lines, subs)

    hull = convex_hull_boundary(combined)
    buf = buffered_boundary(combined, args.buffer_km)

    buf_km2 = area_km2(buf)
    print(f"convex hull area:   {area_km2(hull):>10,.0f} km^2")
    print(f"buffered ({args.buffer_km:g} km) area: {buf_km2:>10,.0f} km^2 "
          f"(published SWIS ~{SWIS_AREA_KM2:,} km^2)")
    if not (AREA_MIN_KM2 <= buf_km2 <= AREA_MAX_KM2):
        print(f"WARNING: buffered area {buf_km2:,.0f} km^2 is outside "
              f"{AREA_MIN_KM2:,}-{AREA_MAX_KM2:,} km^2 — likely a data or buffer problem.")

    # write GeoPackage layers in GDA2020
    if os.path.exists(args.out):
        os.remove(args.out)
    hull.to_crs(OUT_CRS).to_file(args.out, layer="convex_hull", driver="GPKG")
    buf.to_crs(OUT_CRS).to_file(args.out, layer=f"buffered_{args.buffer_km:g}km", driver="GPKG")
    print(f"wrote {args.out} (layers: convex_hull, buffered_{args.buffer_km:g}km)")

    # web-map GeoJSON (WGS84) — buffered boundary, coords rounded to ~1 m
    if args.geojson:
        os.makedirs(os.path.dirname(args.geojson) or ".", exist_ok=True)
        buf.to_crs(WEB_CRS).to_file(args.geojson, driver="GeoJSON")
        print(f"wrote {args.geojson} (buffered boundary, WGS84)")

    # quick overlay plot
    if args.plot:
        try:
            import matplotlib.pyplot as plt
            ax = plt.subplots(figsize=(8, 9))[1]
            hull.boundary.plot(ax=ax, color="#999", linestyle="--", label="convex hull")
            buf.boundary.plot(ax=ax, color="#0f9d77", label=f"buffered {args.buffer_km:g} km")
            lines.plot(ax=ax, color="#3b6", linewidth=0.3)
            subs.plot(ax=ax, color="#e03a1f", markersize=2)
            ax.set_title("Approx. SWIS extent (derived from Western Power network)")
            ax.legend(loc="lower left", fontsize=8)
            plt.savefig(args.plot, dpi=120, bbox_inches="tight")
            print(f"wrote {args.plot}")
        except ImportError:
            print("matplotlib not installed — skipping plot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
