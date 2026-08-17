---
name: normalize-bom
description: Detect BOM worksheets and headers, preserve hierarchy and quantities, and normalize Chinese or numeric process levels for Creo SOP planning.
---

# Normalize BOM

Invoke `normalize-bom` through `SkillRuntime` with the registered input manifest. It emits `normalized-bom/v1`.

Read the registered BOM artifact. Preserve drawing number, model number, name, quantity, process,
controls, and tools. Separate process-only or non-modeled rows only with evidence. Write a
versioned normalized-BOM artifact and deterministic input fingerprint.
