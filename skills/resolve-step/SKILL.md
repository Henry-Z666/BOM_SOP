---
name: resolve-step
description: Resolve a questioned SOP step from a selected candidate or natural-language correction and compute the minimum invalidated dependency subgraph.
---

# Resolve step

Invoke `resolve-step` through `SkillRuntime` only in `NEEDS_REVIEW`. It emits `step-revision/v1` and `invalidation-set/v1`; the Agent orchestrator performs any rerender.

Convert feedback into a versioned, bounded StepRevision. Validate every Qwen field. Rebuild only
the current image for presentation changes and dependency descendants for complete-state changes.
Preserve hashes for all unaffected images, then republish atomically.

A real Creo image that passed structural and geometric gates may be explicitly accepted as the
current image without rerendering. A placeholder or structural failure can never be accepted.
Explicit user PAN/Zoom feedback uses one bounded `manual_refit/v1` render and never re-enables
the frozen automatic probe loop.

An installation-direction or camera revision must re-evaluate both locked fixed cameras before
rendering. Keep the user's camera as a preference only when it passes receiver-facing and visible
explosion projection checks; otherwise run the compatible camera first and report that automatic
action. If a local revision produces a blank frame or process failure, retain the previous valid
image, mark the revision for system retry, and surface the failed attempt instead of silently
showing stale validation.

After a successful resolution, persist the selected image and `PASSED` state in the publication
artifact. Later resolutions must preserve that exact image unless the new invalidation set names
the step. The resolved step immediately leaves the pending-review queue; the desktop Agent keeps
the review page open and advances to the next unresolved step instead of returning to the parent
progress page.

Pass the current validation error code and image kind to Qwen as minimized context. A
presentation-only revision cannot claim to repair a placeholder caused by a structural geometry
gate; reject it with the unresolved gate and request installation direction/object/receiver facts.
Every rerender uses a revision-qualified image path so the GUI can distinguish new evidence from
the previous image.

## Qwen handoff contract

The request must include the human-facing step number, title, source BOM rows, current error code,
image kind, current/allowed fixed cameras, `required_correction_fields`, and a machine-readable
`correction_contract`. Never rely on prior conversation history to supply these facts.

Qwen returns exactly `{"kind": "...", "changes": {...}}`. `changes` must be non-empty and use only
the field names and value types declared by `correction_contract`; vectors are JSON arrays, never
scalars or prose. For `DIRECTION_SIGN_WEAK` and `RECEIVER_NORMAL_NOT_AXIS_ALIGNED`, require a
non-zero three-number `direction` vector. A request to change the view does not satisfy a missing
installation direction. For unresolved moving or receiver occurrences, do not invent occurrence
IDs: return actionable guidance asking the user to identify the installed object and receiver.
The adapter may canonicalize only unambiguous shape aliases such as `pan_x` plus `pan_y` into
`pan: [x, y]`; all values still pass the same bounded validator before execution.

## Human reply contract

The review UI binds the selected step before submitting an instruction, so the user does not need
to repeat the step number. Accept component references in this order:

1. component name;
2. component name plus drawing number or material code when names are duplicated or ambiguous;
3. BOM row only as a diagnostic cross-reference.

Never require a Creo occurrence ID from the user. The handoff context must include the selected
step's known moving/receiver occurrence IDs and its source BOM item's name, drawing number,
material code, and BOM row. Copy those locked IDs when a geometry revision requires them. A step
number identifies an SOP step; it does not identify a component inside that step.

On schema failure, retry at most three times and include the exact failed validation rule plus the
required fields in the correction prompt. If the response remains unusable, preserve the pending
step and show a Chinese question with a concrete answer example; do not expose a raw Python type
error or claim that regeneration started.
