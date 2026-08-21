---
name: validate-repair
description: Apply deterministic geometry, camera, arrow, and file-integrity hard gates with bounded non-camera repair.
---

# Validate and repair

Invoke `validate-repair` through `SkillRuntime` after rendering. It emits
`validation-result/v2` and `candidate-set/v1`.

Run structural deterministic gates only. Never waive assembly, occurrence, visibility, camera identity,
transform, arrow, or file-integrity failures. Image composition and readability are decided by a
person viewing the real raster, not by automatic approval. Do not activate unrequested extra renders.
Produce two to four single-factor arrow-layout candidates only when the render
artifact explicitly proves that all structural and geometric gates passed. When a real Creo
image exists but fails a machine gate, retain the byte-identical image for informed human
review; produce a placeholder only when no real image exists.

Every structurally valid real image must be emitted as `QUESTIONED` with
`manual_acceptance_allowed=true` and `image_review_mode=human_only/v1`, even when legacy image
measurements reported no warnings. Do not allow it into publication until the person explicitly
adopts it. Weak direction, non-axis-aligned receiver normals,
camera/receiver incompatibility, missing occurrences, invalid transforms/audits, and missing
images remain machine hard blocks. A renderable hard block may expose its original image for an
explicit, separately audited human override; it never becomes machine-passed.

Every candidate group must carry `selection_allowed=true` from validation. Directory contents,
legacy paths, and the existence of a JPEG never imply eligibility. System failures remain
retryable and may point to a retained previous image, but that image records rollback rather than
proof that the failed revision succeeded.

Copy structured diagnostics into validation so the desktop review page can show the Chinese
meaning, expected and actual measurements, actions already tried, and concrete next actions.
Expose guided fields for missing key facts. For a structurally valid image, expose only registered
rerender options whose stable IDs map to executable render-task changes. Free-form notes are audit-only.
