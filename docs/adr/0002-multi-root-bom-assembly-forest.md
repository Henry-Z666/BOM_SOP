# ADR 0002: Treat parallel top-level ASMs as an assembly forest

## Status

Accepted — 2026-08-25

## Context

Some production BOMs expand several independent top-level ASM hierarchies in one worksheet. Selecting the first matched ASM as one final assembly causes all later roots to fall outside the discovered Creo graph, so a 900-row BOM can produce only the steps under the first root.

A product title may precede those roots. Its row number is not stable and cannot be used as an exclusion rule.

## Decision

- An exact-matched ASM row with no exact-matched ASM ancestor is a top-level ASM batch.
- Every batch locks and discovers its own authoritative ASM, root coordinates, occurrence graph and fixed cameras.
- Nested ASMs remain inside their containing top-level batch.
- Mapping and formal planning retain per-batch contracts and expose a flattened all-BOM coverage view.
- Validated render tasks from all batches are concatenated into one SOP without cross-root dependencies.
- A product-title row is non-renderable only when title-level position, no exact ASM match, no descendants and no assembly instruction are all evidenced.
- Rows outside every batch remain explicit unscoped exceptions; the system does not guess a root.

## Consequences

The pipeline supports one-root inputs through the existing contracts and multi-root inputs through `assembly-lock/v2`, `creo-cad-forest/v1`, `bom-cad-forest/v1` and `formal-render-forest/v1`. Analysis takes one Creo discovery pass per top-level root, but it no longer silently drops later assembly hierarchies.
