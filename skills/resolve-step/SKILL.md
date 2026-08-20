---
name: resolve-step
description: Resolve a questioned SOP step from a validated candidate, explicit human override, or structured axis-direction correction and compute the minimum invalidated dependency subgraph.
---

# Resolve step

Invoke `resolve-step` through `SkillRuntime` only in `NEEDS_REVIEW`. It emits
`step-revision/v1` and `invalidation-set/v1`; an informed override additionally emits
`human-review-decision/v1`. The Agent orchestrator performs any rerender.

Convert feedback into a versioned, bounded `StepRevision`. Rebuild only the current image for
presentation changes and dependency descendants for deterministic complete-state changes.
Preserve hashes for all unaffected images, then republish atomically.

A real Creo image may be normally accepted without rerendering when validation explicitly sets
`manual_acceptance_allowed`. A candidate requires `selection_allowed=true`. A machine-failed
real image may be accepted only through an explicit acknowledged human override that records the
original image path and SHA-256, machine failures, time and reason. A placeholder, missing image,
legacy path or arbitrary file can never be adopted.

The production framing path is native selected fit only. User PAN/Zoom, explosion-distance,
arrow-layout, or camera text cannot enable `manual_refit`, switch the locked camera, or create
extra framing renders. An installation-direction clarification is accepted only when a
structured field or explicit sentence names one positive or negative X/Y/Z axis. Resolution
cannot revise dependencies, complete-state facts, occurrence IDs, moving parts, or receiver parts.

For unresolved moving or receiver occurrences, keep the step blocked and return to deterministic
BOM/CAD remapping with an actionable human-facing explanation. Never require a Creo occurrence ID
from the user and never infer one from unrestricted text.

After a successful resolution, persist the selected image and effective `PASSED` state in the
publication artifact. For a human override, preserve the original machine status separately and
publish the byte-identical source image with no watermark or transformation. Later resolutions
must preserve that exact image unless the new invalidation set names the step. The resolved step
immediately leaves the review queue; the desktop Agent stays on the review page and advances to
the next unresolved step.

If an instruction is not one unambiguous axis direction, preserve the pending step and show a
concrete Chinese example such as “沿设备 Z 轴正方向装入”。
