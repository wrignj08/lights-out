# Heatmap: replacing the grid "hack" — investigation

Branch: `heatmap-blur`. Scratch notes; not sure we'll keep this.

## What's there today (main)
An adaptive **grid** heatmap. Outage polygons are binned into square lat/lng
cells whose size scales with zoom (~3 km when zoomed out → ~16 m deep in). Each
cell sums either outage **count** or **outage-hours** (window-overlap weighted),
log-normalised, then drawn as filled rectangles on a canvas overlay. A static
per-cell max (scanned over the whole dataset, capped at 2 km res and cached)
keeps colours from shifting as you pan. Hover/tap reads the exact cell value.

It works and is fast (~189–460 polygons), but reads as **blocky**: hard-edged
3–6 px cells, not the smooth glow people picture as a "heatmap".

## Options considered

**A. Blur the grid (implemented on this branch).** Keep all the existing
binning / static-max / tooltip logic untouched; add one pass that paints the
cells to an offscreen canvas and blurs it onto the layer. Blur radius tracks the
cell's on-screen size, so it smooths equally at every zoom.
- ➕ Tiny, reversible change. Reuses tested code. Tooltip stays exact.
- ➕ Self-contained — no new library (keeps the single-file app).
- ➖ Blurs *already-coloured* RGBA, so colours can mix slightly across the ramp
  at steep gradients (cosmetic). Relies on canvas `filter:blur()` (Safari 14+;
  falls back to crisp cells if unsupported).
- Tune/disable via `HEAT_BLUR_K` (0 = today's crisp grid).

**B. True KDE / coverage field (noted, not built).** Drop the grid. Rasterise
each weighted polygon additively into an accumulation buffer, blur the *scalar*
field, then colour-map per pixel. This is the "proper" heatmap.
- ➕ Smoothest, most correct (blur happens before colouring).
- ➖ More code; need a view-independent normalisation to avoid colours shifting
  on pan; tooltip needs a separate exact query. Bigger commitment for a viz
  we're unsure about.

**C. Point KDE via a lib (e.g. simpleheat / Leaflet.heat).** Rejected: it works
on points, so a statewide outage polygon (p95 bbox ≈ 26 km, max ≈ 343 km here)
collapses to one centroid and is badly mislocated unless we sample points across
each polygon — at which point we're reinventing B with a dependency.

## Recommendation
Try **A** first (this branch) — it's the cheapest way to see whether smoothing
alone is "good enough". If the colour-mixing bugs us or we want it crisper, graduate to **B**. Revert is just `git checkout main`.
