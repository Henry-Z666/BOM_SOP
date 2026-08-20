# Contracts and hard gates

This file is the single current contract authority for the Agent and executable Skills.

## Authoritative assembly

Record the exact final ASM filename/version, SHA-256, root coordinate system, actual opened model/version, and calibrated `fixed_123`/`fixed_456` camera basis. Abort when the locked file or hash changes.

## Formal render task

Use `render-plan/v2` with `creo-render-task/v1`. Every formal task contains:

- stable task/step ID and BOM scope;
- full root paths for moving, receiver, and visible occurrence sets;
- authoritative assembly record and source-backed quantity evidence;
- receiver-backed root translation and unchanged rotations;
- camera ID restricted to `fixed_123` or `fixed_456`;
- `fixed-frame-presentation/v1` with `native-selected-fit/v1`;
- `selection_scope=moving_and_receiver_occurrences/v1`;
- `level_policy=fixed_native_selection_margin/v1` with `zoom_to_selected_level=0.42`, one command per render, and absolute PAN/ZOOM forbidden;
- native arrow anchors, output audit references, process text, and status.

The validated task contract—not the publication workbook—is execution truth.

## Required audits

- `arrow-projection/v1` records covered occurrence paths, anchor source, complete/exploded root points, direction, merge coverage, and status. Endpoint difference equals the audited translation.
- `native-framing-audit/v1` records task/image identity, Creo version/datecode, verified `ProCmdZoomIntoOutline`, selection scope, level, one-command limit, and absolute PAN/ZOOM prohibition.

## Hard render gates

A formal image passes only when all apply:

- actual assembly name/version/hash matches the lock;
- every moving and receiver occurrence resolves by full path;
- moving count matches BOM quantity;
- visible set exactly matches the forward stage and contains no future occurrence;
- required receivers are visible;
- explosion is pure translation with unchanged rotations;
- camera is one of the two locked fixed matrices;
- moving item, receiver, installation boundary, and receiver face are readable;
- final raster composition passes compiled size, centering, clipping, and fixed-frame thresholds;
- arrow count/merge coverage, same-CAD-point direction, size, border, and non-overlap gates pass;
- no datum, annotation, weld cosmetic, UI, or unrelated geometry appears;
- output is exactly 1600×1600 and the fixed crop did not rescale pixels.

Failure blocks automatic publication. A real raster may remain for review; bounded system retry is allowed only at scheduler level and never creates extra framing variants inside one attempt.

## Publication gates

- Only machine-passed or explicitly human-approved steps enter a formal workbook.
- Preserve BOM order and the current dynamic page layout.
- Populate material, quantity, process, control, and tooling fields from traceable sources.
- Validate sheet/image counts, relationships, print areas, merged cells, and visible page layout.
