# lights-out — WA power outage history

Western Power publishes a **live** outage map at
<https://www.westernpower.com.au/outages/> but no historical data. This project
rebuilds that history by **git-scraping**: it mirrors the map's backing feed on
a schedule and commits each snapshot, so git history becomes a time machine of
every outage on the SWIS (South West Interconnected System) network. It also
ships a self-contained map viewer ([`map.html`](map.html)) with a time slider
and an on-the-fly heatmap.

### 🔦 Live map → <https://wrignj08.github.io/lights-out/>

> **Unofficial / non-commercial.** This is a personal, public-interest project.
> It is **not affiliated with, endorsed by, or connected to Western Power**.
> Outage data © Western Power, mirrored here for non-commercial transparency
> and research — see [Data & licence](#data--licence).

## The data source

The live map is backed by a public, unauthenticated ArcGIS Feature Service:

```
https://services2.arcgis.com/tBLxde4cxSlNUxsM/ArcGIS/rest/services/WP_Outage_Prod/FeatureServer/0
```

Each outage ("Outage_Area") carries these fields:

| Field | Meaning |
|---|---|
| `INCIDENTREF` | Stable incident id (e.g. `INCD-2010301-U`) — tracks one outage over time |
| `OUTAGETYPE` | Internal type code: **`U` = unplanned (active), `F` = future scheduled work, `P` = planned** |
| `PLANNEDOUTAGE` | `Planned` / `Unplanned` — **unreliable; ignore it** (it labels every `F` as "Unplanned") |
| `OUTAGESTARTTIME` | Reported start (local, `DD/MM/YYYY hh:mm AM/PM`) |
| `ESTIMATEDRESTORATIONTIME` | Current ETA for restoration |
| `NOCUSTOMERSIMPACTED` | Total customers off supply |
| `AFFECTED_AREA` | Comma-separated suburbs affected |
| `AFFECTED_AREA_NOCUSTOMERS` | Per-suburb customer counts (parallel to `AFFECTED_AREA`) |
| geometry | Polygon of the affected area (WGS84) |

> Note: the feed only shows outages **currently active**. An outage's full life
> is reconstructed from the sequence of snapshots in which it appears — which is
> exactly what the git history gives us.

## How it works

```
scrape.py        Fetch every current outage -> data/current.geojson
                 (sorted by INCIDENTREF, OBJECTID stripped, stable formatting
                  so an unchanged map = unchanged file = no commit)

run.sh           scrape.py, then commit ONLY if current.geojson changed.
                 The commit timestamp is the authoritative "observed at" time.

build_events.py  Walk the git history of current.geojson and emit:
                   data/events.csv      one row per outage
                   data/events.geojson  one polygon per outage + metadata
                 first_seen .. last_seen = the outage's observed duration.
```

`data/current.geojson` is the live mirror (versioned). `data/events.*` are
derived artefacts — not committed (see `.gitignore`); rebuild any time with
`python3 build_events.py`.

## Running it

### Option A — GitHub Actions (recommended: cloud, set-and-forget)

Push this repo to GitHub. `.github/workflows/scrape.yml` runs every 10 minutes,
commits changed snapshots, and pushes them back. No machine of your own needs to
stay on. (GitHub may delay scheduled runs under load; 10 min is a safe cadence.)

### Option B — run it yourself on a schedule

Any host that can run `run.sh` on a timer works — e.g. Linux cron
(`crontab -e`):

```cron
*/10 * * * * cd /path/to/lights-out && ./run.sh >> scraper.log 2>&1
```

## Viewing the map

The viewer is published with **GitHub Pages**:

**<https://wrignj08.github.io/lights-out/map.html>**

`build_events.py` reconstructs `data/events.geojson` from the git history of
`current.geojson` (each incident's first-seen → last-seen, with the type-aware
active window above). The deploy keeps this up to date so the published map
tracks new snapshots.

To run the viewer locally instead:

```bash
python3 build_events.py          # rebuild data/events.geojson from git history
python3 -m http.server 8000      # then open http://localhost:8000/map.html
```

You can also drop `data/events.geojson` into QGIS or onto <https://geojson.io>.

## How an outage's active window is derived

`build_events.py` works out *when the power was actually out* (not just when the
record sat in the feed). The rule is type-aware — see `active_start_utc` /
`active_end_utc` / `likely_stuck` in the output:

- **Start** = `OUTAGESTARTTIME` (reported start), falling back to first-seen.
- **End (unplanned, `U`)** = when the record **left the feed** (`last_seen`).
  The live feed only lists *active* outages, so its disappearance is the true
  restoration signal — more reliable than Western Power's padded ETA. The end is
  capped at `ETA + 24h` only to catch **stuck records** that linger in the feed
  long past a frozen estimate (the data is cleanly bimodal: normal outages clear
  within ~2h of their ETA, stuck ones persist for 10+ days). Stuck records are
  flagged `likely_stuck` and excluded from the viewer.
- **End (scheduled, `F`/`P`)** = the scheduled `ESTIMATEDRESTORATIONTIME` —
  these are pre-published works, so their stated start→ETA window is when the
  power is scheduled down (feed presence says nothing; they sit there for days).

## The viewer (`map.html`)

A single self-contained HTML file (Leaflet + CDN libs). Serve it locally —
browsers block `file://` from reading the data:

```bash
python3 -m http.server 8000   # then open http://localhost:8000/map.html
```

Two mutually-exclusive modes (tabs, top-left):

- **Time** — every outage as a polygon (red = unplanned, blue = scheduled), a
  time slider + ▶ play to scrub history, and a sparkline of customers affected
  over time. Planned works only appear during their scheduled window.
- **Heatmap** — an on-the-fly grid aggregating a user-chosen date range by
  **total outage-hours** or **outage count**. The grid resolution scales with
  zoom (10km → 50m), rendered fill-only on a canvas, re-binned only for the
  visible area so it stays smooth. Basemaps: Carto light / Esri satellite, with
  an optional Overture buildings overlay.

## Limitations

- Captures only outages the public feed exposes (active outages ≥ the map's
  reporting threshold); very brief or sub-threshold outages may never appear.
- Duration resolution = scrape interval (10 min). An outage shorter than one
  interval may be caught in a single snapshot (duration shown as 0).
- `OUTAGESTARTTIME` / `ESTIMATEDRESTORATIONTIME` are Western Power's own values;
  `first_seen`/`last_seen` are when *we* observed it.
- Cause/reason is not in the live feed.

## Data & licence

The **code** in this repository is MIT-licensed — see [`LICENSE`](LICENSE).

The **outage data** under `data/` originates from and remains the property of
**Western Power**. It is mirrored here for non-commercial, public-interest
transparency and research. This project is unofficial and not affiliated with,
endorsed by, or connected to Western Power. If you reuse the data, respect
[Western Power's terms](https://www.westernpower.com.au/terms--conditions/) and
attribute the source.
