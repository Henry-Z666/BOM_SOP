---
name: plan-assembly
description: Build a scalable assembly dependency graph with state deltas, complete-state hashes, main processes, and installation steps.
---

# Plan assembly

Invoke `plan-assembly` through `SkillRuntime`. It emits `draft-plan/v1` and the repository's latest `formal-render-plan` schema.

Accept only one run's registered normalized BOM, draft, BOM/CAD mapping and CAD graph artifacts. Return `formal-render-plan/v2` for one root or `formal-render-forest/v1` for parallel roots.

For parallel roots, compile each top-level ASM against only its own authoritative graph, root coordinates and two fixed cameras. Treat that root as the batch foundation, keep nested ASM construction inside the batch, and concatenate batch tasks into one SOP without creating cross-root dependencies.

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

When explicit “第N步” text is absent, derive process scopes from the BOM hierarchy: exclude only
evidence-backed product-title rows, treat every exact-matched ASM row as a process scope, and
assign each remaining renderable row to its nearest ASM ancestor. Use one product-level fallback
scope only for rows with no ASM ancestor. Before returning, account for every BOM row as planned,
assembly context/foundation, unresolved mapping, or evidence-backed non-renderable; fail closed if
an already-mapped row falls through all categories.
