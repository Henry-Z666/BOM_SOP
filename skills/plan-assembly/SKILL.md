---
name: plan-assembly
description: Build a scalable assembly dependency graph with state deltas, complete-state hashes, main processes, and installation steps.
---

# Plan assembly

Accept only one run's `normalized-bom/v1`, `draft-plan/v1`, `bom-cad-map/v1` and
`creo-cad-graph/v3` artifact references. Return `formal-render-plan/v1`.

Use the first root-level BOM/CAD item as the total-assembly foundation only when its position is
consistent with Creo FIX evidence; otherwise create a confirmation item. Treat a nested occurrence
fixed to its containing ASM as that subassembly's local construction base, not as a moving step.

Plan bottom-up construction in the lowest common occurrence scope, then move the completed rigid
subassembly in its parent scope. Group repeated occurrences into one image only when their proven
receiver normals select the same fixed-camera face; retain every receiver occurrence in the
contract. Never derive direction from occurrence centres or the assembly centre.

Record `depends_on`, `state_delta`, `complete_state_hash`, `affected_descendants`, main process,
stage scope and exact forward visible set. Preserve missing geometry and whole-versus-expanded
subassembly choices as pre-generation questions. Resolve producers across the complete graph and
apply a stable topological order; do not assume BOM row order places every receiver before its
dependent part. Identical inputs must produce the same fingerprint.
