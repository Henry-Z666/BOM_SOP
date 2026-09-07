---
name: map-bom-cad
description: Map normalized BOM items and quantities to final-assembly Creo occurrence paths and classify evidence-backed non-modeled rows.
---

# Map BOM to CAD

Invoke `map-bom-cad` through `SkillRuntime`; consume only registered BOM, inventory and CAD-graph artifact references. It emits `bom-cad-map/v1` for one root or `bom-cad-forest/v1` for parallel roots.

Accept `normalized-bom/v1`, `model-inventory/v1`, `draft-plan/v1` and `creo-cad-graph/v3`
artifact references for one `run_id`. Match normalized drawing number first; only when it has no
exact hit, scan the exact model number, then the exact material code. Record which identifier tier
produced the evidence. Never combine conflicting tiers and never use substring similarity for an
automatic occurrence binding.

Constrain candidates first by BOM parent hierarchy and occurrence parent paths. For repeated items,
use only same-process uniquely mapped receivers plus native Creo constraint edges. Auto-resolve only
when the evidence selects the exact BOM quantity or has a strict score boundary. Otherwise preserve
all candidates in a confirmation item; never slice candidates by file or feature order.

Return `bom-cad-map/v1` with every BOM row, expected quantity, full occurrence paths, status and
plain-language evidence. Missing geometry remains missing and cannot be invented from text.

For a CAD forest, map only inside each top-level ASM's BOM scope and graph, then flatten all row statuses for coverage reporting. Product titles remain evidence-backed non-renderable rows. Unscoped renderable rows remain missing exceptions; never bind them across roots.
