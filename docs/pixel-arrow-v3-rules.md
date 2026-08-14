# Pixel Arrow V3 — legacy compatibility rules

V3 separates geometric truth from delivered arrow appearance. It is retained
only for legacy-batch diagnosis and explicit compatibility experiments. New
formal Agent batches use Creo/J-Link `DisplayList3D` through
`creo_display_list/v1`; legacy `run_stage_batch.ps1`,
`run_bounded_stage_batch.ps1`, `run_render.ps1`, V2 runners and image-overlay
scripts are not V3 dependencies.

## Two-pass contract

For every legacy V3 stage, the compatibility J-Link process exports two disposable rasters
from the same locked final ASM, fixed camera, stage occurrence set and pure
translation:

1. **Calibration raster** — native temporary green arrows, used only to read
   final screen endpoints.
2. **Base raster** — no temporary graphics. This is the sole geometry layer of
   the delivered image.

The calibration raster is never published. The final compositor crops both
rasters with the fixed rectangle `(100, 400, 1600, 1600)` and draws the
delivered arrows on the base raster.

## Geometric gates before calibration

- An anchor is a deterministic point on a real model surface. Bounding-box
  centres are forbidden.
- `anchor_complete_root` and `anchor_exploded_root` are obtained from the same
  local point transformed through the actual before/after Creo occurrence
  transforms. Their delta must equal the contract root translation.
- A moving rigid subassembly has one parent movement but may use a physical
  descendant solid as the anchor; both paths are recorded in the audit.
- Failure to resolve a physical anchor blocks the stage. No visual fallback is
  allowed.

## Pixel gates after calibration

- The calibration raster must contain exactly one green connected component per
  unmerged arrow. A mismatch blocks publication.
- Each detected arrow must be 24–420 pixels long after the fixed final crop.
- The installed endpoint is identified from the native arrow head and the
  published arrow always points exploded → installed.
- Final arrows are RGB `(0,176,80)`, 3 px wide, and use a 13 px head. These
  are publication units, independent of CAD dimensions and Creo zoom.
- A future implementation may replace calibration-raster endpoint extraction
  with direct viewport projection only if it retains equivalent pixel tests.

## Migration acceptance

For a new product, lock its final ASM manifest and fixed cameras first. A V3
trial must pass one multi-fastener stage, one rigid-subassembly stage and one
single fitting stage before batch expansion. The test package must verify
fixed-frame output, exact component count, readable arrow length and absence
of legacy runner dependencies.
