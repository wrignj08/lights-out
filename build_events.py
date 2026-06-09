#!/usr/bin/env python3
"""
Reconstruct a per-outage event table from the git history of current.geojson.

Each scrape commit is a snapshot of the live map. By walking every commit and
tracking each INCIDENTREF, we recover when an outage first appeared and when it
last appeared (i.e. its observed duration) without storing anything extra at
scrape time. Commit timestamps are the authoritative "observed at" clock.

Outputs:
  data/events.csv      - one row per outage (analysis-friendly, no geometry)
  data/events.geojson  - one polygon per outage with first/last-seen metadata,
                         suitable for rendering a historical outage map

Stdlib only. Python 3.8+. Run from inside the repo.
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKED = "data/current.geojson"
CSV_PATH = os.path.join(HERE, "data", "events.csv")
GEOJSON_PATH = os.path.join(HERE, "data", "events.geojson")

CSV_FIELDS = [
    "incident_ref",
    "outage_type",
    "planned",
    "first_seen_utc",
    "last_seen_utc",
    "observed_duration_hours",
    "reported_start",
    "estimated_restoration",
    "max_customers_impacted",
    "affected_area",
    "snapshots_seen",
]


def git(*args) -> str:
    return subprocess.run(
        ["git", *args], cwd=HERE, capture_output=True, text=True, check=True
    ).stdout


def commits_for_file():
    """Yield (sha, unix_ts) oldest-first for commits that touched the file."""
    out = git("log", "--reverse", "--format=%H %ct", "--", TRACKED)
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, ts = line.split()
        yield sha, int(ts)


def load_snapshot(sha: str):
    raw = git("show", f"{sha}:{TRACKED}")
    return json.loads(raw)


def main() -> int:
    events = {}  # incident_ref -> aggregated record
    n_commits = 0
    for sha, ts in commits_for_file():
        try:
            fc = load_snapshot(sha)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue  # file may not exist / be valid in very first commits
        n_commits += 1
        for ft in fc.get("features", []):
            p = ft.get("properties") or {}
            ref = p.get("INCIDENTREF")
            if not ref:
                continue
            cust = p.get("NOCUSTOMERSIMPACTED") or 0
            ev = events.get(ref)
            if ev is None:
                events[ref] = {
                    "incident_ref": ref,
                    "outage_type": p.get("OUTAGETYPE"),
                    "planned": p.get("PLANNEDOUTAGE"),
                    "first_seen": ts,
                    "last_seen": ts,
                    "reported_start": p.get("OUTAGESTARTTIME"),
                    "estimated_restoration": p.get("ESTIMATEDRESTORATIONTIME"),
                    "max_customers": cust,
                    "affected_area": p.get("AFFECTED_AREA"),
                    "snapshots": 1,
                    "geometry": ft.get("geometry"),
                }
            else:
                ev["last_seen"] = ts
                ev["snapshots"] += 1
                ev["max_customers"] = max(ev["max_customers"], cust)
                # Keep the latest known restoration estimate + geometry.
                ev["estimated_restoration"] = p.get("ESTIMATEDRESTORATIONTIME")
                if ft.get("geometry"):
                    ev["geometry"] = ft["geometry"]

    if not events:
        print("no events found - has the scraper committed any snapshots yet?",
              file=sys.stderr)
        return 1

    def iso(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    rows = []
    features = []
    for ev in sorted(events.values(), key=lambda e: e["first_seen"]):
        dur_h = round((ev["last_seen"] - ev["first_seen"]) / 3600, 2)
        rows.append(
            {
                "incident_ref": ev["incident_ref"],
                "outage_type": ev["outage_type"],
                "planned": ev["planned"],
                "first_seen_utc": iso(ev["first_seen"]),
                "last_seen_utc": iso(ev["last_seen"]),
                "observed_duration_hours": dur_h,
                "reported_start": ev["reported_start"],
                "estimated_restoration": ev["estimated_restoration"],
                "max_customers_impacted": ev["max_customers"],
                "affected_area": ev["affected_area"],
                "snapshots_seen": ev["snapshots"],
            }
        )
        features.append(
            {
                "type": "Feature",
                "properties": rows[-1],
                "geometry": ev["geometry"],
            }
        )

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f,
                  ensure_ascii=False, indent=1)

    print(f"reconstructed {len(rows)} outage events from {n_commits} snapshots",
          file=sys.stderr)
    print(f"  -> {CSV_PATH}", file=sys.stderr)
    print(f"  -> {GEOJSON_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
