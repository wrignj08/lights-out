#!/usr/bin/env bash
# Scrape the live Western Power outage feed and commit a snapshot only if the
# map actually changed. Designed to be run on a schedule (launchd/cron/Actions).
# The git commit timestamp is the authoritative "observed at" time.
set -euo pipefail
cd "$(dirname "$0")"

python3 scrape.py

# Nothing changed since the last snapshot -> no commit, keep history clean.
if git diff --quiet -- data/current.geojson; then
  echo "no change in outage map; skipping commit"
  exit 0
fi

count=$(python3 -c "import json;print(len(json.load(open('data/current.geojson'))['features']))")
git add data/current.geojson
git commit -q -m "snapshot: ${count} outages @ $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "committed snapshot with ${count} outages"

# Refresh the derived event table so map.html always reflects the latest
# committed snapshots (events.geojson is git-ignored; rebuilt from history).
python3 build_events.py
