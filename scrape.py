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
import urllib.error
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
USER_AGENT = "lights-out outage archiver (github.com/wrignj08/lights-out)"


class ServiceError(RuntimeError):
    """An ArcGIS error envelope returned under an HTTP 200."""

    def __init__(self, err: dict):
        self.code = err.get("code")
        super().__init__(f"service error: {err}")


def _retryable(e: Exception) -> bool:
    """Only wait-and-try-again for failures a later attempt could fix.

    A 4xx (bad query, dead layer) fails identically every time, so retrying it
    just burns 30s before reporting the same thing.
    """
    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500 or e.code == 429
    if isinstance(e, ServiceError):
        # ArcGIS mirrors HTTP status codes into the envelope.
        return isinstance(e.code, int) and (e.code >= 500 or e.code == 429)
    # URLError (DNS/connection reset), socket timeouts, and truncated bodies
    # that fail to parse are all transient by nature.
    return isinstance(e, (urllib.error.URLError, OSError, ValueError))


def _get(url: str, params: dict) -> dict:
    """GET a JSON URL, retrying transient failures, returning the parsed body."""
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    last_err = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            # ArcGIS reports failures as HTTP 200 with an error envelope. Without
            # this check a service-side error looks like "zero outages" and we'd
            # archive an empty map as if it were real.
            if isinstance(body, dict) and "error" in body:
                raise ServiceError(body["error"])
            return body
        except Exception as e:  # noqa: BLE001 - classified by _retryable
            last_err = e
            if not _retryable(e):
                raise RuntimeError(f"failed to fetch {full}: {e}") from e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {full} after 5 attempts: {last_err}")


def fetch_all() -> list:
    """Page through the feature service and return all outage features."""
    features = []
    offset = 0
    # ~40k outages would be far beyond anything real; a page counter stops a
    # service that ignores resultOffset from looping until the job times out.
    for _ in range(20):
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
        return features
    raise RuntimeError("pagination did not terminate; refusing partial snapshot")


# Coordinate precision. The service returns ~12 decimal places (sub-micron);
# 5dp is ~1.1m at this latitude — ample for outage-area polygons, and ~35%
# smaller files (so smaller commits and a smaller published events.geojson).
COORD_DP = 5


def round_coords(obj):
    """Recursively round every float in a GeoJSON coordinate structure."""
    if isinstance(obj, float):
        return round(obj, COORD_DP)
    if isinstance(obj, list):
        return [round_coords(x) for x in obj]
    return obj


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
        geom = ft.get("geometry")
        if geom and "coordinates" in geom:
            geom = {**geom, "coordinates": round_coords(geom["coordinates"])}
        cleaned.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": geom,
            }
        )

    def sort_key(ft):
        p = ft["properties"]
        # INCIDENTREF is the stable id; fall back to area name if ever absent.
        return (p.get("INCIDENTREF") or "", p.get("AFFECTED_AREA") or "")

    cleaned.sort(key=sort_key)
    return {"type": "FeatureCollection", "features": cleaned}


def previous_count() -> int:
    """How many outages the last snapshot held (0 if there isn't one)."""
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return len(json.load(f).get("features") or [])
    except (OSError, ValueError):
        return 0


def main() -> int:
    features = fetch_all()
    fc = normalise(features)

    # A statewide zero-outage moment is not a thing in practice, so an empty
    # result almost certainly means the feed is broken in a way that still
    # returned 200. Bail rather than wipe the live map (override to archive a
    # genuine empty: ALLOW_EMPTY=1).
    if not fc["features"] and previous_count() and not os.environ.get("ALLOW_EMPTY"):
        print(
            f"refusing to overwrite {previous_count()} outages with an empty feed",
            file=sys.stderr,
        )
        return 1

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    # Sorted keys + trailing newline => minimal, stable git diffs.
    text = json.dumps(fc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {len(fc['features'])} outages to {OUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
