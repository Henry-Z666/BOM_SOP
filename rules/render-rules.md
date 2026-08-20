# Formal render rules

This file is the single current rendering authority for the Agent and all executable Skills.

## Truth and stage

1. The BOM defines process hierarchy, quantity, order, and source-backed text.
2. The highest-version final total ASM defines occurrence identity, position, orientation, appearance, root coordinates, and fixed cameras.
3. Intermediate assemblies and historical render outputs are diagnostic evidence only.

For step `n`:

```text
visible = completed steps 1..n-1 + current moving occurrences + required receivers
hidden  = future occurrences + auxiliary/non-part objects
```

Use full root occurrence paths. Completed subassemblies remain rigid. Geometry may add a missing receiver dependency but may not silently reorder or omit BOM work.

## Explosion and cameras

- Select receiver evidence from the current occurrence's measured Creo assembly constraints. Prefer `INSERT`, then mate/alignment-style constraints; exclude receivers that move in the same step. If no usable receiver point and direction exist, leave the step unresolved instead of guessing.
- Orient the receiver normal away from its measured receiver point toward the moving occurrence origin. Grouped occurrences use their normalized mean outward normal.
- Use the deterministic display distance compiled from the locked assembly occurrence-origin extent (`8%` of its diagonal, or `80` when no usable extent exists).
- Explosion is translation only, away from the receiver; preserve every 3×3 rotation matrix and validate the applied translation read-back. Raster visibility remains a hard gate; no unimplemented ray-cast or clearance solver may be claimed as evidence.
- Formal cameras are exactly `fixed_123` and its center-opposite `fixed_456`; never create a third per-step view.
- Receiver faces 1/2/3 map to `fixed_123`; faces 4/5/6 map to `fixed_456`. Compile a question when the fixed view is in the wrong half-space, collapses the receiver face toward a silhouette, or gives no projected explosion length; final raster validation remains authoritative.

## Native adaptive framing

- Formal framing is `native_zoom_to_selected/v1` only.
- Add the complete moving and receiver occurrence paths to Creo's Selection Buffer, then execute `ProCmdZoomIntoOutline` once.
- Set `zoom_to_selected_level = 0.42`. Creo's fit to the selected moving-plus-receiver bounding box provides the size adaptation; this fixed relative margin must not be multiplied by a second CAD-size ratio.
- External absolute PAN/ZOOM, screen-coordinate automation, probe renders, response caches, dynamic geometry crops, and post-crop upscaling are forbidden.
- One render attempt may export one formal raster. Scheduler-level retries remain bounded and receive one new raster budget per attempt.
- Persist `native-framing-audit/v1` with Creo version/datecode, command verification, selection scope, and level.

## Arrows

- For every moving occurrence, choose a deterministic local solid-surface anchor.
- Transform the same local point through complete and exploded occurrence transforms.
- Draw from `anchor_exploded_root` to `anchor_complete_root` with a green thin line and small head.
- Prefer one arrow per occurrence. Merge only same-direction conflicts with explicit occurrence coverage.
- Native `DisplayList3D` is the only formal renderer; JPEG pixel overlays are forbidden.

## Appearance and hard framing checks

- Render solid part geometry only; hide datums, curves, quilts, annotations, weld cosmetics, and UI/session text in Creo.
- Export one native 1800×2400 raster, then apply only the fixed center 1600×1600 publication crop. The crop must not detect geometry or rescale pixels.
- The final 1600×1600 raster must keep the moving item, receiver, installation boundary, and arrow readable and centered within the compiled gate.
- Non-critical completed background may cross at most two frame edges; moving items, required receivers, and arrows may not be lost.
- Raster validation remains a hard gate even though Creo performs the native centering.

## Async J-Link boundary

- Use official asynchronous pfc APIs: `ComponentPath.SetTransform` under `DynamicPositioning`, temporary SimpRep exclusions, Selection Buffer, `RunMacro`, layers, and display options.
- Do not introduce synchronous-only `wfc*` calls into the asynchronous renderer.
- Source CAD is read-only. Each formal task opens an isolated copy and erases its session state afterward.

## Forbidden fallbacks

- No computer-use or screen-coordinate automation.
- No AI-generated geometry or guessed occurrence.
- No intermediate ASM as a formal image source.
- No relative X/Y/Z camera rotations, pixel arrows, probe framing, or silent fallback from native selected fit.
- No future parts added merely to make a picture easier to understand.
