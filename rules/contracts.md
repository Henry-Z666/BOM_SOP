# Contracts and hard gates

This file is the single current contract authority for the Agent and executable Skills.

## Authoritative assembly

Record the exact final ASM filename/version, SHA-256, root coordinate system, actual opened model/version, and calibrated `fixed_123`/`fixed_456` camera basis. Abort when the locked file or hash changes.

## Formal render task

Use `render-plan/v2` with `creo-render-task/v1`. Every formal task contains:

- stable task/step ID and BOM scope;
- full root paths for moving, receiver, and visible occurrence sets;
- authoritative assembly record and source-backed quantity evidence;
- intake BOM and full ASM/PRT tree hashes, revalidated before/after rendering and before publication;
- receiver-backed root translation and unchanged rotations, including a complete `occurrence_translation_vectors_root` map whose entries equal the common group vector for repeated parts in one BOM-row action;
- camera ID restricted to `fixed_123` or `fixed_456`;
- `square-crop-presentation/v1` with `native-selected-fit/v2`, `final_frame=[1600,1600]`, `source_policy=native_viewport_overscan/v1`, and `crop_policy=fixed_center_square/v1`;
- constraint-backed `receiver_interface_selections`, each containing a stable receiver occurrence path, positive Creo item ID, and `item_type=SURFACE`;
- `selection_scope=moving_occurrences_and_receiver_surfaces/v1` and `coordinate_adjustment=none_native_surface_fit/v1`;
- `level_policy=fixed_native_selection_margin/v1` with initial `zoom_to_selected_level=0.70`, persisted-task compatibility at `0.80` and `0.85`, a bounded focus revision at `0.95`, one native command per render, and free/manual PAN/ZOOM forbidden;
- native arrow anchors, output audit references, process text, and status.

The validated task contract—not the publication workbook—is execution truth.

## Required audits

- `arrow-projection/v1` records covered occurrence paths, anchor source, complete/exploded root points, direction, merge coverage, and status. Each endpoint difference equals the audited translation for its covered occurrence(s).
- `native-framing-audit/v1` records task/image identity, Creo version/datecode, verified `ProCmdZoomIntoOutline`, moving-plus-receiver-surface scope, no coordinate adjustment, level, one-command limit, and free/manual PAN/ZOOM prohibition.
- Automatic image-quality audits are not part of approval. Existing visibility-audit code may remain dormant, but it cannot accept, reject, or rewrite a step.

## Hard render gates

A formal image passes only when all apply:

- actual assembly name/version/hash matches the lock;
- every moving and receiver occurrence resolves by full path;
- moving count matches BOM quantity;
- visible set exactly matches the forward stage and contains no future occurrence;
- required receivers are visible;
- explosion is pure translation with unchanged rotations;
- camera is one of the two locked fixed matrices;
- arrow count/merge coverage and same-CAD-point direction pass;
- the final raster contains at least one significant connected arrow component for every non-merged audited arrow;
- the formal image is exported by Creo as an undistorted native-aspect overscan with a 1600-pixel short edge, then centre-cropped once to exactly `1600×1600`; padding, stretching, upscaling, geometry-driven crop bounds, and publication-time crops are forbidden;
- no datum, annotation, weld cosmetic, UI, or unrelated geometry appears;
- output file exists and has the contracted dimensions.

Readability, composition, centering, clipping, apparent occlusion, arrow presentation, and view preference are human-review concerns. They never approve or reject an image automatically.

Structural failure blocks publication. Image composition is not automatically accepted or rejected: every structurally valid real raster enters human review. A selected rerender option must produce a versioned step revision and an executable render-task field change.

## Publication gates

- Only explicitly human-approved steps enter a formal workbook.
- An explicitly audited `skip_from_publication` decision may omit a step from the current workbook without removing its durable render/review record; the step remains available for a later rerender and insertion.
- Apply a user-selected batch as one durable transaction: compile/render/validate at most once for the union of rerendered steps, preserve every non-invalidated prior result, and write/replace the XLSX once after all decisions have been applied in memory.
- Preserve BOM order and the current dynamic page layout.
- Populate material, quantity, process, control, and tooling fields from traceable sources.
- Validate sheet/image counts, relationships, print areas, merged cells, and visible page layout.
