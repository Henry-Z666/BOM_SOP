---
name: publish-delivery
description: Fill the retained single-page SOP XLSX template with BOM-backed text and ordered Creo step images, then publish a strict two-item delivery directory.
---

# Publish delivery

Invoke `publish-delivery` through `SkillRuntime` with normalized BOM, locked plan, validation and candidate artifacts. It emits `publication-result/v2`.

Clone the built-in `assets/sop-template.xlsx` page for each main process/batch;
never draw a replacement workbook. Fill project, process, control-point, material and
tool fields only from normalized BOM data, using `待填写` for missing information.
Insert that batch's ordered standardized step images together in the template's
assembly-content area. The built-in layout accepts up to six images in a deterministic
grid; only overflow creates a same-process continuation sheet. Do not create one sheet
per image and do not assume a fixed number of processes.

Publish only explicitly human-approved images; pending images may appear only in a review artifact,
never as a final approved step. Verify workbook structure, retained
template anchors, images and Excel print output.
There is no automatic image-quality approval. A normal current-image/candidate selection or an
explicit `human-review-decision/v1`
may grant delivery eligibility to a real machine-failed image without changing its machine
status. Verify its path and SHA-256, require `publication_transform=none` and `watermark=false`,
then copy and embed the original bytes directly. Placeholders can never be manually approved.
When republishing after a local resolution, merge the previous publication with the current
invalidation set. Preserve the exact image path, SHA-256 and `PASSED` state of every resolved,
non-invalidated step. Never reconstruct those states from the original validation artifact or
silently switch a prior selection back to another candidate.
Before saving, remove stale external defined names whose targets contain `#REF!` or
missing external-workbook references; ordinary Excel rejects such inherited template
residue even when openpyxl can read it.
Keep only the SOP workbook and `步骤图片` in the user delivery directory.
