# WA Power Outage History

Western Power publishes a **live** outage map at
<https://www.westernpower.com.au/outages/> but no historical data. This project
rebuilds that history by **git-scraping**: it mirrors the map's backing feed on
a schedule and commits each snapshot, so git history becomes a time machine of
every outage on the SWIS (South West Interconnected System) network.

Inspired by Simon Willison's [git scraping](https://simonwillison.net/2020/Oct/9/git-scraping/)
technique and the [energyq-outages](https://github.com/joelkoen/energyq-outages)
project for Queensland.

## The data source

The live map is backed by a public, unauthenticated ArcGIS Feature Service:

```
https://services2.arcgis.com/tBLxde4cxSlNUxsM/ArcGIS/rest/services/WP_Outage_Prod/FeatureServer/0
```

Each outage ("Outage_Area") carries these fields:

| Field | Meaning |
|---|---|
| `INCIDENTREF` | Stable incident id (e.g. `INCD-2010301-U`) — tracks one outage over time |
| `OUTAGETYPE` | Internal type code (e.g. `F`, `U`) |
| `PLANNEDOUTAGE` | `Planned` / `Unplanned` |
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

### Option B — locally on this Mac (launchd)

Runs `run.sh` on an interval while your Mac is awake. Install with:

```bash
cp launchd/au.wa.outage-scraper.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/au.wa.outage-scraper.plist
```

(See `launchd/` for the plist; edit the interval/paths first.)

## Reconstructing the historical map

After snapshots have accumulated:

```bash
python3 build_events.py
```

Then drop `data/events.geojson` onto <https://geojson.io>, into QGIS, or a
Leaflet/Mapbox map to render every past outage. Filter the CSV by
`first_seen_utc` to draw the map "as it was" on any date.

## Limitations

- Captures only outages the public feed exposes (active outages ≥ the map's
  reporting threshold); very brief or sub-threshold outages may never appear.
- Duration resolution = scrape interval (10 min). An outage shorter than one
  interval may be caught in a single snapshot (duration shown as 0).
- `OUTAGESTARTTIME` / `ESTIMATEDRESTORATIONTIME` are Western Power's own values;
  `first_seen`/`last_seen` are when *we* observed it.
- Cause/reason is not in the live feed (only via the per-property request form
