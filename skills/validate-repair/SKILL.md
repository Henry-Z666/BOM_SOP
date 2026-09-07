---
name: validate-repair
description: Apply deterministic geometry, camera, arrow, and file-integrity hard gates with bounded non-camera repair.
---

# Validate and repair

Invoke `validate-repair` through `SkillRuntime` after rendering. It emits
`validation-result/v2` and `candidate-set/v1`.

Run structural deterministic gates before AI image review. Never waive assembly, occurrence,
visibility, camera identity, transform, arrow, or file-integrity failures. AI reviews only image
composition and readability, and it cannot activate unrequested extra renders.
Produce two to four single-factor arrow-layout candidates only when the render
artifact explicitly proves that all structural and geometric gates passed. When a real Creo
image exists but fails a machine gate, retain the byte-identical image for informed human
review; produce a placeholder only when no real image exists.

Every structurally valid real image enters `ai_then_human/v1`. Emit `PASSED` with
`approval_source=ai_review` when AI returns `passed=true`; otherwise emit `QUESTIONED` with
`manual_acceptance_allowed=true`. Missing credentials, service errors, timeouts, and invalid AI
schemas fall back to human review and never fail open. Weak direction, non-axis-aligned receiver normals,
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
