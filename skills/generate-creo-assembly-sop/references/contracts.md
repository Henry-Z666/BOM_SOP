# Contracts and hard gates

## Authoritative assembly manifest

Record at least:

- schema/version;
- exact final ASM filename and Creo file version;
- SHA-256 at batch start;
- root coordinate system;
- actual opened model/version;
- `fixed_123` and `fixed_456` matrices or position/up vectors.

Abort the batch if the file version or hash changes.

## Formal step contract

Use the current repository formal schema (`creo-render-jobs/v3` with `creo-stage-camera-contract/v3`, or a later unified step contract). Each job must contain:

- stable job ID and BOM step scope;
- authoritative assembly manifest reference;
- complete root occurrence paths for moving, receiver, and visible sets;
- BOM quantity and model/drawing evidence;
- root-coordinate translation vector and its receiver-normal evidence;
- camera ID restricted to `fixed_123` or `fixed_456`;
- explicit framing parameters;
- arrow policy and output audit paths;
- source-backed process text and status.

The formal execution truth is the validated contract, not the Excel publication workbook.

## Arrow projection audit

Use `same_cad_point/v1` or newer. For each arrow record:

- covered occurrence paths;
- stable local anchor and surface identifier;
- complete and exploded root coordinates;
- screen-plane coordinates used for layout;
- merge state and covered occurrences;
- final status.

The root-coordinate difference between arrow endpoints must equal the occurrence translation with the arrow pointing exploded-to-complete.

## Hard render gates

A formal image passes only when all apply:

- actual assembly name/version/hash matches the manifest;
- every moving and receiver occurrence resolves by full path;
- rendered moving occurrence count matches BOM quantity;
- visible set equals the forward stage contract and contains no future occurrence;
- all required receivers are visible;
- moving and complete sets use the same occurrence identities;
- only translation changes; all rotation matrices are unchanged;
- camera is exactly one of the two calibrated fixed matrices;
- moving object, receiver, and installation boundary are readable in frame;
- receiver face does not visually degenerate into a thin line;
- arrow count equals moving occurrence count or an explicit merge covers all occurrences;
- every arrow uses the same local CAD point at both states and points toward installation;
- arrow projections meet minimum length, frame, and non-overlap thresholds;
- no datum, weld symbol, annotation, UI, or unrelated assembly content is visible;
- output dimensions and fixed-frame policy match the job contract.

Failure blocks automatic publication and triggers bounded correction/rerender of the affected
step. If a real image exists, it remains available for informed human review. An explicit human
override grants delivery eligibility without changing the machine failure result.

## Publication gates

- Machine-passed render jobs or real images covered by a verified `human-review-decision/v1`
  may enter the workbook. The decision must bind the exact path and SHA-256, retain the machine
  status, and require byte-identical publication with no watermark or image transformation.
- Placeholders and missing images may never be manually approved.
- Preserve BOM order and one-step-per-sheet/page template semantics.
- Populate material, quantity, process, control, and tooling fields from traceable sources.
- Validate sheet count, image count, image references, print areas, merged cells, and visible page layout.
