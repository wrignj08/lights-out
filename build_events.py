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
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKED = "data/current.geojson"
CSV_PATH = os.path.join(HERE, "data", "events.csv")
GEOJSON_PATH = os.path.join(HERE, "data", "events.geojson")

# The feed reports times in local WA time (AWST, no DST) as DD/MM/YYYY hh:mm AM/PM.
AWST = timezone(timedelta(hours=8))


def awst_to_utc_iso(s):
    """Parse a feed timestamp (local AWST) -> UTC ISO string, or None."""
    if not s or not s.strip():
        return None
    try:
        dt = datetime.strptime(s.strip(), "%d/%m/%Y %I:%M %p")
    except ValueError:
        return None
    return dt.replace(tzinfo=AWST).astimezone(timezone.utc).isoformat()


# The feed's INCIDENTREF carries a trailing revision/sub-area counter like
# "(1)", "(2)": the same real incident is re-issued under a new suffix when it's
# revised, and is also split across suffixes when it spans separate areas at
# once. Keying on the full ref would fragment one outage into several phantom
# events, so we aggregate on the base ref (suffix stripped).
_SUFFIX_RE = re.compile(r"\(\d+\)$")


def base_ref(ref: str) -> str:
    return _SUFFIX_RE.sub("", ref)


def combine_geometry(geoms):
    """Merge one incident's coexisting sub-area polygons into a single geometry
    for rendering. Not a topological union - overlapping rings render fine."""
    geoms = [g for g in geoms if g]
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    polys = []
    for g in geoms:
        t, coords = g.get("type"), g.get("coordinates")
        if t == "Polygon":
            polys.append(coords)
        elif t == "MultiPolygon":
            polys.extend(coords)
    if not polys:
        return geoms[0]
    return {"type": "MultiPolygon", "coordinates": polys}


CSV_FIELDS = [
    "incident_ref",
    "outage_type",
    "planned",
    "first_seen_utc",
    "last_seen_utc",
    "observed_duration_hours",
    "reported_start",
    "estimated_restoration",
    "reported_start_utc",
    "estimated_restoration_utc",
    "active_start_utc",
    "active_end_utc",
    "likely_stuck",
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
    events = {}  # base incident_ref -> aggregated record
    n_commits = 0
    for sha, ts in commits_for_file():
        try:
            fc = load_snapshot(sha)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue  # file may not exist / be valid in very first commits
        n_commits += 1

        # Collapse this snapshot's features by base incident ref first. The feed
        # splits one incident across "(n)" suffixes, and its customer counts are
        # per-piece, so sum them within the snapshot and pick the largest piece
        # as the metadata representative.
        snap = {}  # base ref -> {cust, geoms, props}
        for ft in fc.get("features", []):
            p = ft.get("properties") or {}
            ref = p.get("INCIDENTREF")
            if not ref:
                continue
            cust = p.get("NOCUSTOMERSIMPACTED") or 0
            g = snap.get(base_ref(ref))
            if g is None:
                snap[base_ref(ref)] = {
                    "cust": cust, "geoms": [ft.get("geometry")],
                    "props": p, "_peak": cust,
                }
            else:
                g["cust"] += cust
                g["geoms"].append(ft.get("geometry"))
                if cust > g["_peak"]:
                    g["_peak"], g["props"] = cust, p

        for ref, g in snap.items():
            p, cust = g["props"], g["cust"]
            geom = combine_geometry(g["geoms"])
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
                    "first_estimated_restoration": p.get("ESTIMATEDRESTORATIONTIME"),
                    "max_customers": cust,
                    "affected_area": p.get("AFFECTED_AREA"),
                    "snapshots": 1,
                    "geometry": geom,
                }
            else:
                ev["last_seen"] = ts
                ev["snapshots"] += 1
                ev["max_customers"] = max(ev["max_customers"], cust)
                # Keep the latest known restoration estimate + geometry.
                ev["estimated_restoration"] = p.get("ESTIMATEDRESTORATIONTIME")
                if geom:
                    ev["geometry"] = geom

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
        first_dt = datetime.fromtimestamp(ev["first_seen"], tz=timezone.utc)
        last_dt = datetime.fromtimestamp(ev["last_seen"], tz=timezone.utc)
        rs_utc = awst_to_utc_iso(ev["reported_start"])
        eta_utc = awst_to_utc_iso(ev["estimated_restoration"])

        # Active window = when the power is/was actually out. Computed
        # differently by type:
        #   start = reported start (fall back to when we first saw it)
        #   end (UNPLANNED, type U) = when the record LEFT the feed (last_seen).
        #     The feed only lists active outages, so its disappearance is the
        #     true restoration signal, and it's more accurate than WP's padded
        #     ETA. We cap at ETA+grace only to catch "stuck" records that
        #     linger long past a frozen estimate (verified bimodal: normal
        #     clear <=2h past ETA, stuck ones >10 days).
        #   end (PLANNED/SCHEDULED, F/P) = the FIRST-published restoration time.
        #     These are pre-published works, so their stated start->ETA window is
        #     when the power is scheduled down. We use the initial ETA, not the
        #     latest: a lingering record rolls its ETA forward for days/weeks
        #     (verified bimodal: real planned jobs <=~4d, rolled ones 13-25d),
        #     which would otherwise be counted as continuous downtime.
        STUCK_GRACE_H = 24
        first_eta_utc = awst_to_utc_iso(ev["first_estimated_restoration"])
        active_start_dt = datetime.fromisoformat(rs_utc) if rs_utc else first_dt
        likely_stuck = False
        if ev["outage_type"] == "U":
            # The feed only lists ACTIVE unplanned outages, so a reported start
            # more than the grace window before we first saw the record is a
            # garbled/rolled feed time - fall back to when we observed it out.
            if active_start_dt < first_dt - timedelta(hours=STUCK_GRACE_H):
                active_start_dt = first_dt
            if eta_utc:
                cap = datetime.fromisoformat(eta_utc) + timedelta(hours=STUCK_GRACE_H)
                active_end_dt = min(last_dt, cap)
                likely_stuck = last_dt > cap
            else:
                active_end_dt = last_dt
        else:
            active_end_dt = (datetime.fromisoformat(first_eta_utc)
                             if first_eta_utc else last_dt)
            # Flag records the feed kept serving well past their scheduled end:
            # the ETA was later rolled forward, marking a stuck planned record.
            if first_eta_utc:
                likely_stuck = last_dt > active_end_dt + timedelta(hours=STUCK_GRACE_H)
        if active_end_dt < active_start_dt:   # guard against bad/garbled times
            active_end_dt = active_start_dt

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
                "reported_start_utc": rs_utc,
                "estimated_restoration_utc": eta_utc,
                "active_start_utc": active_start_dt.isoformat(),
                "active_end_utc": active_end_dt.isoformat(),
                "likely_stuck": likely_stuck,
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
        json.dump({"type": "FeatureCollection",
                   # build time ~= last scrape (the workflow scrapes then rebuilds)
                   "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "features": features}, f,
                  ensure_ascii=False, indent=1)

    print(f"reconstructed {len(rows)} outage events from {n_commits} snapshots",
          file=sys.stderr)
    print(f"  -> {CSV_PATH}", file=sys.stderr)
    print(f"  -> {GEOJSON_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
