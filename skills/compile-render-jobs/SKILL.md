---
name: compile-render-jobs
description: Compile a locked assembly plan into deterministic Creo render jobs with fixed cameras, visibility, translations, and arrow audits.
---

# Compile render jobs

Invoke `compile-render-jobs` through `SkillRuntime` only after `PlanRevision` is locked. It emits `render-plan/v2` as the locked render-jobs artifact.

Use only the locked `PlanRevision`, `formal-render-plan/v1` and authoritative assembly artifact.
Do not accept caller-selected output paths.

Compile ready steps with exact moving, receiver, stage-scope and visible occurrence sets. Explosion
must be pure translation and preserve every occurrence rotation. Keep the proved receiver normal as
installation truth; a separately audited display vector may use a bounded root-axis lateral path only
for the overlap/shape cases defined in `rules/render-rules.md`.
Restrict cameras to the calibrated `fixed_123` and exact centre-opposite `fixed_456`, select exactly
one from absolute receiver-axis visibility and staged-context front overlap before rendering, and
emit exactly one formal variant. Render warnings never switch cameras or trigger a second variant.

For questioned geometry, compile an explicit regeneration placeholder; never promote a guessed
vector or camera. Structured input may confirm only the sign of a measured Creo axis. Same-direction grouping must retain complete quantity
and receiver coverage. Arrow jobs must use the same deterministic CAD anchor at exploded and
complete states. Return versioned jobs, input fingerprint and per-step recovery scope.
