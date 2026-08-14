---
name: compile-render-jobs
description: Compile a locked assembly plan into deterministic Creo render jobs with fixed cameras, visibility, translations, and arrow audits.
---

# Compile render jobs

Use only the locked `PlanRevision`, `formal-render-plan/v1` and authoritative assembly artifact.
Do not accept caller-selected output paths.

Compile ready steps with exact moving, receiver, stage-scope and visible occurrence sets. Explosion
must be pure translation along a proved receiver normal; preserve every occurrence rotation.
Restrict cameras to the calibrated `fixed_123` and exact centre-opposite `fixed_456`. Keep both as
the only repair choices and require preview hard gates before publication.

For questioned geometry, compile bounded direction/camera candidates or an explicit regeneration
placeholder; never promote a guessed vector. Same-direction grouping must retain complete quantity
and receiver coverage. Arrow jobs must use the same deterministic CAD anchor at exploded and
complete states. Return versioned jobs, input fingerprint and per-step recovery scope.
