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
Creo `FIX` against `ROOT` proves only final placement. It does not prove that a later BOM item is already installed, so it must not promote that occurrence into `initial_completed_occurrences` or make it visible before its BOM step. Only the BOM foundation may seed initial stage context.

## Explosion and cameras

- Select receiver evidence from the current occurrence's measured Creo assembly constraints. Prefer `INSERT`, then mate/alignment-style constraints; exclude receivers that move in the same step. If no usable receiver point and direction exist, leave the step unresolved instead of guessing.
- Treat Creo `SURFACE`/`INSERT` directions as unsigned axes. When native solid bounds exist, test both signed translations and choose the sign that maximizes the worst post-translation clearance across every same-axis constrained receiver. Use the receiver-point half-space only as a deterministic fallback when bounds are missing or the clearance scores tie.
- Group repeated occurrences from the same BOM row by compatible unsigned installation axis. Resolve one common signed direction for the whole group (majority clearance evidence, with stable occurrence order as an exact-tie fallback), and apply it to every occurrence in that shared step. Split only when the measured installation axes are genuinely incompatible.
- Use the deterministic display distance compiled from the locked assembly occurrence-origin extent (`8%` of its diagonal, or `80` when no usable extent exists).
- Explosion is translation only and preserves every 3×3 rotation matrix. The interface normal remains the installation-axis truth. If a normal explosion still has material AABB overlap with staged context and is ambiguous because it telescopes along a long bridge, or because a part with aspect ratio at least `3` has strong contact-backed lateral evidence, the presentation vector may switch to one root axis within the receiver plane. The lateral candidate must reduce overlap to a bounded minimum and is selected before rendering; it is not a pixel review or retry.
- A structured operator direction may confirm only the sign of an existing measured Creo receiver axis. Free text cannot generate coordinates, and the confirmation cannot replace the measured axis.
- Formal cameras are exactly `fixed_123` and its center-opposite `fixed_456`; never create a third per-step view.
- Evaluate only `fixed_123` and `fixed_456`. Use absolute receiver-axis alignment, projected explosion, and staged-context AABB front overlap to lock one camera deterministically; exact ties prefer `fixed_123`. No formal render-time camera switch is allowed.
- The lossless occurrence/interface label audit and its camera-repair choices are temporarily frozen. Do not generate audit rasters, emit `NO_ELIGIBLE_FIXED_CAMERA`, or ask the user to choose a rerender goal until the GUI can show the failed evidence as a preview.

## Native adaptive framing

- Formal framing is `native_receiver_surface_fit/v1` only.
- Add every complete moving occurrence path and each constraint-backed receiver `SURFACE` selection to Creo's Selection Buffer, then execute `ProCmdZoomIntoOutline` once.
- Select the receiver interface by stable occurrence path plus Creo surface ID. Never select the complete receiver occurrence; a large main body therefore cannot make the installation activity unreadably small.
- Set the initial `zoom_to_selected_level = 0.70`. It supplies one fixed relative margin around the moving geometry and every concrete receiver interface. Retain `0.80` and `0.85` for persisted-task compatibility and `0.95` only for the bounded focus revision.
- Operator-supplied or free absolute PAN/ZOOM, screen-coordinate automation, probe renders, response caches, dynamic geometry crops, and post-crop upscaling are forbidden. No post-command PAN/ZOOM correction is applied.
- One render attempt may export one formal raster. Scheduler-level retries remain bounded and receive one new raster budget per attempt.
- Camera label rasters are currently frozen and are not produced. The retained implementation must remain internal and dormant.
- Persist `native-framing-audit/v1` with Creo version/datecode, command verification, selection scope, and level.
- Recompute the intake BOM and full ASM/PRT tree hashes before and after formal rendering and again before publication. Any file-set or digest change blocks the run.

## Arrows

- For every moving occurrence, choose a deterministic local solid-surface anchor.
- Transform the same local point through complete and exploded occurrence transforms.
- Draw from `anchor_exploded_root` to `anchor_complete_root` with a green thin line and small head.
- For a grouped repeated-part step, every arrow endpoint difference must equal the group's common translation vector.
- Prefer one arrow per occurrence. Merge only same-direction conflicts with explicit occurrence coverage.
- Native `DisplayList3D` is the only formal renderer; JPEG pixel overlays are forbidden.

## Appearance and hard framing checks

- Render solid part geometry only; hide datums, curves, quilts, annotations, weld cosmetics, and UI/session text in Creo.
- Export an undistorted native-viewport overscan whose short edge is 1600 pixels, then apply exactly one deterministic centre crop to `1600×1600`. Padding/letterboxing, stretching, upscaling, geometry-detected crop bounds, and per-image adaptive crops are forbidden.
- Before the selected-object fit, scale the configured fit level by the inverse native viewport aspect ratio so every moving item and concrete receiver interface remains inside the central square crop. The final square raster must keep every moving item, receiver interface, installation boundary, and arrow readable and centered within the compiled gate.
- Non-critical completed background may cross at most two frame edges; moving items, required receivers, and arrows may not be lost. The number of significant green arrow components may not be lower than the validated arrow-audit count.
- Raster validation remains a hard gate even though Creo performs the native centering.

## Async J-Link boundary

- Use official asynchronous pfc APIs: `ComponentPath.SetTransform` under `DynamicPositioning`, temporary SimpRep exclusions, Selection Buffer, `RunMacro`, layers, and display options.
- Do not introduce synchronous-only `wfc*` calls into the asynchronous renderer.
- Component-label evidence must be produced through the same asynchronous J-Link session or an equivalent lossless CAD projection adapter; never recover occurrence identity by classifying the beauty image.
- Source CAD is read-only. Each formal task opens an isolated copy and erases its session state afterward.

## Forbidden fallbacks

- No computer-use or screen-coordinate automation.
- No AI-generated geometry or guessed occurrence.
- No intermediate ASM as a formal image source.
- No relative X/Y/Z camera rotations, pixel arrows, probe framing, or silent fallback from activity-coordinate fit.
- No future parts added merely to make a picture easier to understand.
