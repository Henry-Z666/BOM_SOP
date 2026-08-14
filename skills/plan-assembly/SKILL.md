---
name: plan-assembly
description: Build a scalable assembly dependency graph with state deltas, complete-state hashes, main processes, and installation steps.
---

# Plan assembly

Plan bottom-up subassemblies and forward installation visibility. Keep completed subassemblies
rigid. Record `depends_on`, `state_delta`, `complete_state_hash`, affected descendants, and main
process ID for every step. Keep the result deterministic for identical inputs.
