#!/usr/bin/env python3
"""
Mirror Western Power's live outage feed to a stable GeoJSON snapshot.

The live map at https://www.westernpower.com.au/outages/ is backed by a public
ArcGIS Feature Service. We page through every current outage and write it to
data/current.geojson in a deterministic order/format, so that committing the
file on a schedule turns git history into a time machine of the outage map.

No third-party dependencies (stdlib only). Python 3.8+.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

# Backing service for the Western Power outage map (found in the page source of
# https://www.westernpower.com.au/outages/ -> layer "Outage_Areas").
SERVICE = (
    "https://services2.arcgis.com/tBLxde4cxSlNUxsM/ArcGIS/rest/services/"
    "WP_Outage_Prod/FeatureServer/0"
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "data", "current.geojson")

PAGE_SIZE = 2000  # = the service maxRecordCount
USER_AGENT = "wa-outage-history (github.com/your-handle/power-history)"


def _get(url: str, params: dict) -> dict:
    """GET a JSON URL with basic retry, returning the parsed body."""
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    last_err = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - network flakiness, retry
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {full}: {last_err}")


def fetch_all() -> list:
    """Page through the feature service and return all outage features."""
    features = []
    offset = 0
    while True:
        body = _get(
            f"{SERVICE}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "outSR": "4326",          # WGS84 lon/lat
                "f": "geojson",
                "resultRecordCount": PAGE_SIZE,
                "resultOffset": offset,
                "orderByFields": "INCIDENTREF",
            },
        )
        batch = body.get("features", [])
        features.extend(batch)
        # ArcGIS sets this flag when more records remain beyond this page.
        if body.get("exceededTransferLimit") and batch:
            offset += len(batch)
            continue
        if len(batch) == PAGE_SIZE:  # defensive: page was full but no flag
            offset += len(batch)
            continue
        break
    return features


def normalise(features: list) -> dict:
    """Return a deterministic FeatureCollection.

    We deliberately drop the volatile OBJECTID (it is reassigned by the service
    and would create spurious diffs) and sort by the stable INCIDENTREF so that
    an unchanged outage map produces an unchanged file -> no empty commits.
    """
    cleaned = []
    for ft in features:
        props = dict(ft.get("properties") or {})
        props.pop("OBJECTID", None)
        cleaned.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": ft.get("geometry"),
            }
        )

    def sort_key(ft):
        p = ft["properties"]
        # INCIDENTREF is the stable id; fall back to area name if ever absent.
        return (p.get("INCIDENTREF") or "", p.get("AFFECTED_AREA") or "")

    cleaned.sort(key=sort_key)
    return {"type": "FeatureCollection", "features": cleaned}


def main() -> int:
    features = fetch_all()
    fc = normalise(features)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    # Sorted keys + trailing newline => minimal, stable git diffs.
    text = json.dumps(fc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {len(fc['features'])} outages to {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
