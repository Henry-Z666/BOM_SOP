---
name: validate-repair
description: Apply deterministic geometry and arrow hard gates, optional Qwen visual review, bounded repair, and comparable candidate generation.
---

# Validate and repair

Invoke `validate-repair` through `SkillRuntime` after rendering. It emits `validation-result/v2` and `candidate-set/v1`.

Run deterministic gates before Qwen review. Never let Qwen waive assembly, occurrence, visibility,
camera, transform, arrow, or image-size failures. Search only approved camera, pan, zoom, distance,
and arrow changes. Produce two to four single-factor candidates or an explicit placeholder.

Carry the render error code and user-facing error message into the validation item. When a local
rerender creates real evidence, publish its revision-qualified path and SHA-256 so the desktop
review list immediately displays the new image rather than an overwritten or cached path.

Separate structural/geometry validity from presentation quality. A real image with only
`SUBJECT_TOO_SMALL`, centering, clipping, or arrow-readability warnings must be retained for human
review and may be explicitly adopted. A renderable planning ambiguity such as
`DIRECTION_SIGN_WEAK` must likewise retain the inferred-direction image as `QUESTIONED`; only
missing occurrences, missing receiver geometry, invalid transforms/audits, or a missing image use
a placeholder. Neither Qwen nor a presentation-only correction may silently waive an unresolved
installation-direction diagnostic.

Use the shared gate category instead of treating all deterministic findings as equivalent.
Presentation warnings and exhausted bounded camera repairs remain real, manually reviewable
images. System failures remain retryable and may point to a retained previous image, but that
retained image is evidence of rollback rather than proof that the failed revision succeeded.
For an exhausted fixed-camera flip, publish both the original and centre-opposite fixed-camera
renders in `candidate-set/v1`; human review must be able to inspect and select either image.
Copy the complete structured diagnostics into validation so desktop review can show the Chinese
meaning, expected and actual measurements, actions already tried, and concrete next actions.
