---
name: render-batch
description: Execute bounded, checkpointed Creo render jobs while isolating failures and preserving the source CAD model set.
---

# Render batch

Invoke `render-batch` through `SkillRuntime` with locked render jobs. It emits `render-batch-result/v2` and internal render files.

Use one worker by default, one disposable model copy per worker session, and a checkpoint every
20 tasks. Retry external failures up to three times. Continue unrelated steps when one image
fails; wait only descendants of a structural failure.

Execute `candidate_search` tasks when they contain a complete renderable geometry contract. Keep
the inferred-direction output as a real `QUESTIONED` image with its planning diagnostic; never
promote it to `PASSED` automatically. This gives the user evidence they can adopt or correct.

For a targeted rerender, success requires a new real image and removal of the correction's
unresolved required field. If the revision leaves the target in placeholder mode, return
`TARGET_RERENDER_PRODUCED_NO_IMAGE` with the remaining hard-gate codes; never return a successful
resolution containing only the old placeholder.

Classify every deterministic failure through the shared four-class gate policy:

- `hard_block`: assembly truth failures stop the affected step and discard misleading evidence.
- `auto_repair`: retry bounded fixed-camera or explosion variants, then retain the best real image.
- `human_review`: keep the real image and mark it `QUESTIONED`; never replace it with a placeholder.
- `system_retry`: roll back a failed local presentation revision and retain the previous valid image.

When the first check specifically recommends a fixed-camera flip
(`CAMERA_RECEIVER_WRONG_HALF_SPACE`, `CAMERA_RECEIVER_SILHOUETTE`, or a
`DIRECTION_SIGN_WEAK` planning diagnostic), render the centre-opposite
fixed camera exactly once and validate it again. For camera-geometry failures,
continue automatically when the flipped image passes. For `DIRECTION_SIGN_WEAK`,
keep both the original and flipped images as a two-image candidate group for
human review even if both rasters pass. Do not loop between cameras.

`candidate_search` tasks must be accepted by the Creo native batch script; rejecting
them as non-formal produces a placeholder instead of reviewable evidence.

Every failed or questioned step carries `primary_code`, the complete structured `failures` list,
`category`, `expected`, `actual`, `attempted_actions`, `suggested_actions`, and `retained_image`.
Direction changes must re-rank both `fixed_123` and `fixed_456` before Creo runs. An incompatible
user-selected camera is not executed blindly: the compatible fixed camera runs first and the other
remains a bounded fallback.
