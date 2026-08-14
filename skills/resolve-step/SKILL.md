---
name: resolve-step
description: Resolve a questioned SOP step from a selected candidate or natural-language correction and compute the minimum invalidated dependency subgraph.
---

# Resolve step

Convert feedback into a versioned, bounded StepRevision. Validate every Qwen field. Rebuild only
the current image for presentation changes and dependency descendants for complete-state changes.
Preserve hashes for all unaffected images, then republish atomically.
