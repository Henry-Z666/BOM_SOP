---
name: map-bom-cad
description: Map normalized BOM items and quantities to final-assembly Creo occurrence paths and classify evidence-backed non-modeled rows.
---

# Map BOM to CAD

Accept `normalized-bom/v1`, `model-inventory/v1`, `draft-plan/v1` and `creo-cad-graph/v3`
artifact references for one `run_id`. Match normalized drawing number, model number or material
code exactly; never use substring similarity.

Constrain candidates first by BOM parent hierarchy and occurrence parent paths. For repeated items,
use only same-process uniquely mapped receivers plus native Creo constraint edges. Auto-resolve only
when the evidence selects the exact BOM quantity or has a strict score boundary. Otherwise preserve
all candidates in a confirmation item; never slice candidates by file or feature order.

Return `bom-cad-map/v1` with every BOM row, expected quantity, full occurrence paths, status and
plain-language evidence. Missing geometry remains missing and cannot be invented by Qwen.
