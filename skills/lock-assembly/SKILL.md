---
name: lock-assembly
description: Classify product titles and lock every exact-matched top-level Creo ASM batch.
---

# Lock assembly

Invoke `lock-assembly` through `SkillRuntime` with `normalized-bom/v1`. It emits `model-inventory/v1` and either the compatible single-root `assembly-lock/v1` or multi-root `assembly-lock/v2`.

Treat every exact-matched ASM row with no exact-matched ASM ancestor as a parallel top-level batch. Record its BOM root, row scope, nested ASM rows, exact filename, Creo version, SHA-256, and root-coordinate contract. Multiple parallel roots are work batches, not candidates for one final assembly.

Classify a product-title row only when title-level position, no exact ASM by drawing/model/material, no hierarchy descendants, and no assembly instruction are all evidenced. Never use a row number as a classification rule. Keep every row outside all top-level ASM scopes in `unscoped_bom_rows`; never guess its owner.
