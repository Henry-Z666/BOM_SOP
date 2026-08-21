---
name: render-batch
description: Execute bounded, checkpointed Creo render jobs while isolating failures and preserving the source CAD model set.
---

# Render batch

Invoke `render-batch` through `SkillRuntime` with locked render jobs. It emits
`render-batch-result/v2` and internal render files.

Use one worker by default, one disposable model copy per worker session, and a checkpoint every
20 tasks. Retry external failures up to three times. Continue unrelated steps when one image
fails; wait only descendants of a structural failure.

Send only `formal` tasks to Creo. A step with unresolved occurrences, receiver geometry, or
installation direction remains a placeholder and must not be rendered from inferred geometry.
For a targeted rerender, success requires a new real image and removal of the correction's
unresolved required field. Never report success while retaining only the old placeholder.

The fixed-camera lossless-label audit is temporarily frozen. A contract with `status=frozen` proceeds
directly to the one formal raster using the deterministically selected fixed camera. Do not produce
audit label rasters, retry a missing audit, or emit camera-audit resolution choices while frozen.

Classify every deterministic failure through the shared four-class gate policy:

- `hard_block`: assembly, camera compatibility, transform, and arrow truth failures reject the image.
- `auto_repair`: camera-visibility repair options are frozen; the production worker does not expose
  them or launch free camera, PAN, Zoom, or explosion searches from this category.
- `human_review`: keep a real image with presentation-only warnings as `QUESTIONED`.
- `system_retry`: roll back a failed local attempt and retain the previous valid image as history.

The production framing contract is `native_zoom_to_selected/v1`: one locked fixed camera, one
Creo selected-object fit command, `Zoom=1`, `PAN=(0,0)`, and one formal raster. Camera
compatibility or weak-direction findings return to planning/clarification instead of being
converted into extra render candidates.

Every failed or questioned step carries `primary_code`, the structured `failures` list,
`category`, `expected`, `actual`, `attempted_actions`, `suggested_actions`, and `retained_image`.
An explicit axis-direction clarification may re-rank `fixed_123` and `fixed_456` before Creo runs;
only the deterministically compatible camera is compiled into the formal task.
