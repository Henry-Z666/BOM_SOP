---
name: discover-cad
description: Inventory Creo ASM and PRT versions and extract occurrence or constraint evidence without modifying or uploading source CAD.
---

# Discover CAD

Invoke `discover-cad` through `SkillRuntime` with the locked assembly artifact. It emits `creo-cad-graph/v3`.

Accept only `run_id` plus the locked CAD-directory and final-assembly artifact references. Run
official asynchronous J-Link on a fresh disposable model copy. Return `creo-cad-graph/v3` with:

- the actual assembly filename/version, root coordinate system, SHA-256 manifest and default view;
- every recursive full root occurrence path and complete transform;
- stable constraint type codes and both assembly/component references;
- referenced item IDs/types plus root-coordinate surface normal or axis direction when available.

Hash every source CAD file before and after access. Return `blocked` on any mutation, assembly
version mismatch, unknown occurrence reference or invalid root vector. Mark unavailable geometry
explicitly; never infer it from an occurrence centre. Keep local paths and raw CAD inside the local run workspace.
