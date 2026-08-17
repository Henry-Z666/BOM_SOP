---
name: lock-assembly
description: Select and hash the highest valid final Creo assembly version when one or more final ASM candidates exist.
---

# Lock assembly

Invoke `lock-assembly` through `SkillRuntime` with `normalized-bom/v1`. It emits `model-inventory/v1` and `assembly-lock/v1`.

Choose only from discovered final-assembly candidates. Record exact filename, Creo version,
SHA-256, and root coordinate contract. Emit a confirmation question when multiple candidates
change assembly meaning. Never silently substitute an intermediate assembly.
