---
name: compile-render-jobs
description: Compile a locked assembly plan into deterministic Creo render jobs with fixed cameras, visibility, translations, and arrow audits.
---

# Compile render jobs

Use only the locked plan and authoritative assembly artifact. Restrict cameras to `fixed_123` and
`fixed_456`, explosion to translation, and arrows to the same CAD anchor. Do not accept caller-
selected output paths. Return a versioned render plan and fingerprint.
