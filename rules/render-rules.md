# Formal render rules

## Truth hierarchy

1. The Excel BOM defines process hierarchy, quantity, and source-backed text.
2. The highest-version final total ASM defines occurrence identity, final position, orientation, appearance, root coordinates, and product cameras.
3. Intermediate ASM files may support hierarchy or constraint diagnosis but never formal rendering.

## Stage semantics

For step `n`:

```text
visible = completed steps 1..n-1 + current moving occurrences + required receivers
hidden  = future occurrences + auxiliary/non-part objects
```

An already completed subassembly remains rigid in all later steps. A bottom-level subassembly receives its own close-up construction image before the whole group enters its parent.

## Step planning authority

- `bom-order-authority/v1` — steps follow the BOM row order exactly. Geometry must never reorder steps: a part installed ahead of its receiver produces floating installs; executing the BOM order never can.
- `no-silent-skip/v1` — every BOM row resolves to CAD occurrences or is declared raw material (unit kg/metre, rides with its fabricated parent). Any other unresolved row aborts the plan with a diagnostic. Rows whose parts already ride inside an ancestor installed earlier are recorded as explicit component-detail merges; nothing is ever dropped silently.
- `cad-match/v1` — match BOM rows to CAD model stems by drawing number first, then model/spec. Keys are normalised (case-insensitive, '.' == '_', whitespace removed) so `GB9074.4` matches `GB9074_4`. Occurrence pools are allocated quantity-wise in BOM order.
- `first-placement/v1` — the first BOM group (base weldment) is step 1 and lands as one rigid unit: every root of the group receives the same vertical placement translation, descendants ride, one representative arrow, no receiver. A weldment may span sibling subtrees; a root missing from the moving list leaves its subtree parked mid-air.

## Explosion

- `explosion-contact/v5` — the separation normal is ANY world axis with a measured face contact against an already-installed LEAF occurrence (root-level boxes are too coarse). Insertion mates (shallow interpenetration) outrank touch contacts (a fastener sliding into a hole interpenetrates its receiver while merely brushing a side wall); without any candidate the part exits along the shortest whole-hull clearance travel on any axis.
- `explosion-distance/v5` — separation distance scales with the part: `floor = min(60, max(1.2*size, insertion_depth + 0.5*extent_along_axis + 5))`, `distance = min(260, max(floor, 0.4*extent))`. A fixed 60 mm floor flung 16 mm O-rings to 3.7x their own size so they read as stray markers; small parts must stay hugging their seat, and insertion depth is fully withdrawn plus a small margin. Large parts keep the legacy 60 mm behaviour (their floor saturates there), so no regression.
- `explode-visibility/v2` — the explosion sign/axis must be scored by view-ray occlusion against the installed context, not only by receiver interpenetration: for each candidate exploded pose, cast a ray from the part centre toward each locked camera eye and count installed AABBs intersecting the ray; prefer a pose with zero blockers on at least one locked camera. The receiver-only AABB-intersection proxy (v1) is blind to cavity occlusion: a part exploded into an enclosed cavity intersects no box yet is invisible from every outside camera (observed on 30.25: the chosen sign sent the clamp below the top plate; the context-free focus rep showed it, the full view did not).
- `occlusion-diagnosis/v1` — when a context-free audit export shows action ink that the full-view image lacks (or an arrow lands on blank), FIRST do the numeric explosion read-back (`explode_final_audit`): read-back ok ⇒ occlusion/cavity, a planner direction defect; read-back mismatch ⇒ only then consider transform loss. Never diagnose a transform race from image differences alone.
- Use the receiver/contact surface normal transformed into final-ASM root coordinates.
- Translate the moving object away from the receiver. Do not move within the receiver plane.
- Preserve every 3x3 rotation matrix exactly.
- Use enough distance to distinguish the moving object from its target without creating an unnecessarily long arrow.
- Keep explosion selection independent of camera selection.

## Cameras

- `fixed_123`: the calibrated, upright product default octant.
- `fixed_456`: the exact center-opposite position direction with the same product up reference.
- A formal step chooses one of these two values only.
- `camera-approach/v3` — choose the camera whose eye lies on the approach side of the install (view-from direction dotted with the approach direction = −explosion normal), i.e. behind the moving part so part and receiver face are both visible; tie-break by which camera sees the moving part in front of the receiver. Before calibration exists, a documented analytic fallback (the two views are vertical mirrors) decides on the Y component only.
- Choose the camera in which moving items, receiver locations, and installation boundary are all readable and the receiver face does not collapse into a line.
- PAN and ZOOM only compose the frame; they never change view direction.

## Naming and batch traceability

- `naming/v1` — `step_id = "<bom_level>_<task_code>"` (e.g. `30.2_full_loop_test`); image files are `<step_id>.jpg` / `<step_id>.arrows.jpg` / `<step_id>.render.json`.
- Every large generation run owns a fresh batch folder `out/images/<task_code>_<YYYYMMDD_HHMM>`; plan.json's `images_dir` is the single source of truth consumed by renderer, arrow overlay and review. Batches never mix; `--reuse-batch` reuses the folder only for replans of the same task.
- The batch folder plus the plan's audit trail (merged component-detail rows, material rows, unallocated occurrences) make every generation reproducible and human-reviewable.

## Arrows

- For every moving occurrence, select a deterministic local surface anchor.
- Transform that same anchor through complete and exploded occurrence transforms.
- Draw from `anchor_exploded_root` to `anchor_complete_root`.
- Use green thin lines and small heads. Prefer one arrow per occurrence.
- Select alternate deterministic anchors to avoid overlap. Merge same-direction conflicts only with an explicit coverage audit.
- A rigid subassembly movement produces one representative arrow for the group.
- Arrows projecting to identical screen endpoints (shared group anchor) are deduplicated before the overlap audit; the overlap audit only compares distinct arrows. The absorbed roots are recorded on the representative as `merged_paths` (coverage audit), and the review verifies `path + merged_paths` covers exactly the plan's moving set.

## Appearance and framing

- `sop-context/v1` — the final installation image must keep the whole-machine context. A focus zoom that isolates the action part into a cut-out frame is forbidden (user-rejected: an extracted, magnified part with blank surround reads as a broken picture). If the action part is too small to read, fix it at the planner side (explosion visibility, distance calibre) or with arrows/annotation; never crop the final camera onto the part. The focus measurement rep is diagnostic-only and must not drive the final export camera.
- Render only solid part geometry. Hide datum planes, axes, points, coordinate systems, curves, surface quilts, annotations, weld symbols, and UI/session text.
- Display suppression uses OFFICIAL config options first (`display_axes`, `display_planes`, `display_points`, `display_coord_sys`, `display_annotations`, `spin_center_display`, `todays_date_note` = no in src/config.pro); types with no config switch (CURVE/QUILT/DATUM_SURFACE/ZONE, weld cosmetics) are blanked on auxiliary layers and re-asserted after explosion.
- Fit and center inside Creo. Use one deterministic fixed crop only when the publication frame requires it.
- `centering/v3` — renderer (`unbiasedCentre`) and review (`rule_centering`) share ONE ink-centre calibre: the raw ink centre is used unless a bbox side actually touches a canvas edge (clipped); only then is the centre recovered from the UNclipped edge plus the expected extent, where expected px = `outline_mm × k_px_per_mm × zoom_multiplier` (the mm→px factor is a separate meta field, never folded into the zoom). A shortfall with both edges free is an extent-model error, not clipping; the raw centre stays the honest estimate and the closed-form residual audit corrects it.
- `correction-sequence/v2` — when the ink height sits on the window-band cap, a closed-form correction can flip the edge-clip regime (clipped ↔ free) and the measured pan sensitivity jumps with it; one extra correction pass (three maximum, fixed sequence, never a loop) lets the empirical screen-shift-per-pan gradient reconverge after a regime switch.
- `size-calibre/v2` — the renderer zooms to the 3D outline extent while the review measures the 2D ink silhouette (systematically smaller by occlusion), so the review fill floor (C8) sits below the renderer's nominal occupancy.
- Never use geometry-detected or per-image adaptive cropping. Never upscale a crop.
- Formal installation images currently use a square 1600x1600 frame unless the selected publication contract says otherwise.

## J-Link async boundary

- `jlink-async-boundary/v1` — the sanctioned explode-state authoring API (`wfcAssembly.CreateExplodedState` + `ExplodedAnimationMoveInstructions`, `WModelItem.Hide`, everything under `wfc*` in otk.jar) is synchronous-OTK only: in asynchronous J-Link every wfc server call dies with `CIPRemoteApp.comm == null` (observed NPE) because the synchronous RPC channel is never initialised outside a protk registry load.
- The official asynchronous mechanisms are pfc's own: `ComponentPath.SetTransform` under `DynamicPositioning` for explosion (OTK UG "Transforming Coordinates of an Assembly Member", with a numeric read-back audit and a mathematically equivalent ComponentFeat fallback), Layer blanking for display suppression, and config.pro options for display classes.
- Do not reintroduce wfc imports in async tools; if a future need truly requires wfc, it must run as a synchronous toolkit registered via protk, not in the async renderer.

## Rep-switch hygiene and rejected mechanisms

- `rep-switch-hygiene/v1` — after EVERY `ActivateSimpRep`, re-apply the locked view transform (`camera-reassert/v1`) and re-assert the recorded absolute exploded poses (`explode-reassert/v1`); a rep regeneration may disturb window/display state. Both are absolute and idempotent; they are hygiene, not a fix for any observed defect.
- Rejected (falsified, do not re-walk): (a) rep-capture — that a SimpRep freezes member poses at creation time; moving the focus-rep instructions after the explosion changed nothing. (b) window refit on rep switch — re-locking the view transform changed nothing. (c) rep regeneration clearing DynamicPositioning transforms — the numeric read-back proved poses never lost.

## Arrow projection calibration

- `renderer_calibrated/v1` — overlay_arrows projects world anchors with the Renderer's own proven framing calibration: px = C + k·f·(R2row0·w − m0), py = C − k·f·(R2row1·w − m1), where C is the measured final-ink centre, k·f the calibrated px-per-view-mm at final zoom and m the EvalOutline view centre; the locked camera is orthographic on screen. Cross-validated against the independent focus-rep z=1 ink measurement (30.25).
- Rejected: `solve_affine_full_view/v1` — fitting a per-axis affine between the modelled visible-set view bbox and the ink bbox; the single-bbox correspondence mis-registered ~100 px on 30.25 because modelled bbox extremes do not coincide with silhouette extremes. Do not revive bbox-fit calibration; the Renderer calibration constants are the only projection source of truth.
- `arrow_style_scale/v1` — whole-machine finals (sop-context/v1) shrink the action area to a small frame fraction, so the fixed close-up stroke (3 px) is invisible to VLM and human alike (30.25 V9). Stroke width, head size and LEN_MIN scale inversely with the calibrated px-per-view-mm (k·f), referenced to the proven close-up weight (K_STYLE_REF=2.6, cap 3.5); the scale factor is audited per step.
- `outline-explosion-gap/v1` (open defect, t26 evidence) — the framing outline pass's `worldPoses` come from a freshly created `ComponentPath.GetTransform(true)`, which returns the NOMINAL pose: it does NOT carry the DynamicPositioning explosion displacement (the read-back inside `translateOccurrence` uses the same object and passes, which is why the gap went unnoticed). Evidence: 30.25 outline view_box 816.3x1278.2 centre (98.51,390.00) is bit-identical at 60 mm and 129 mm explosion. Consequence: `outline_centre` m is the COMPLETE-state view centre, so the renderer_calibrated/v1 correspondence C↔m breaks as the exploded ink centre drifts from m (~90 px arrow-tail error at 129 mm). Fix direction: add the projected cumTr displacement of action parts to the overall outline union, exactly as the action bbox already does (rx2 = rx − dvx), so m tracks the exploded ink. Until fixed, renderer_calibrated/v1 is only valid for small explosions.

## Forbidden fallbacks

- No computer-use or screen-coordinate automation.
- No AI-generated geometry or guessed missing occurrence.
- No intermediate ASM as an image source.
- No relative `X:`, `Y:`, or `Z:` camera rotations in a new formal contract.
- No future parts added merely to make a picture easier to understand.

