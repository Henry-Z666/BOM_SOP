---
name: validate-repair
description: Apply deterministic geometry and arrow hard gates, optional Qwen visual review, bounded repair, and comparable candidate generation.
---

# Validate and repair

Invoke `validate-repair` through `SkillRuntime` after rendering. It emits `validation-result/v1` and `candidate-set/v1`.

Run deterministic gates before Qwen review. Never let Qwen waive assembly, occurrence, visibility,
camera, transform, arrow, or image-size failures. Search only approved camera, pan, zoom, distance,
and arrow changes. Produce two to four single-factor candidates or an explicit placeholder.
