---
name: generate-creo-assembly-sop
description: Generate, diagnose, or publish a Creo assembly SOP pipeline from Excel BOM data and Creo Parametric ASM/PRT models. Use when a task involves BOM hierarchy planning, final-assembly occurrence mapping, staged visibility, pure-translation exploded installation images, fixed two-camera selection, same-CAD-point arrows, render validation, or writing validated images and BOM text into an SOP workbook.
---

# Generate Creo Assembly SOP

Use this as the aggregate workflow guide, not as a thirteenth executable step. Start from only a registered BOM and CAD directory. The desktop GUI calls `AgentCore`; `PipelineOrchestrator` invokes the twelve repository skills through `SkillRuntime`.

Run the skills in this order:

1. `intake-preflight` → `normalize-bom` → `lock-assembly` → `discover-cad` → `map-bom-cad` → `plan-assembly` → `clarify-plan`.
2. Stop once at `AWAITING_CONFIRMATION`; lock `PlanRevision` from the user's explicit answers or deterministic defaults.
3. `compile-render-jobs` → `render-batch` → `validate-repair` → `publish-delivery`.
4. For a questioned step, run `resolve-step`, rerender only its invalidation set when required, then republish.

Use the BOM as process truth and each locked top-level Creo ASM as geometry truth for its own batch. A BOM may contain multiple parallel top-level ASM batches; scan and plan all of them independently, retain nested ASMs under their owning root, and publish their validated steps into one SOP. Product-title exclusion requires title position, no ASM match, no descendants and no steps; a row number is never a rule. Derive occurrence, visibility, transform, camera and arrow contracts only from those sources. Free text may confirm only an explicit ±X/±Y/±Z direction and cannot waive any hard gate.

Machine gates control automatic acceptance. After structural gates pass, AI may approve the image
for automatic SOP publication. AI rejection or unavailability falls back to human review. When a
real image exists, preserve it with its machine and AI results. A user may explicitly accept a machine-failed original through a
hashed `human-review-decision/v1`; retain the failure status separately and publish the original
bytes with no watermark or transformation.

Use one persistent Creo worker by default, save a checkpoint after every completed step, and restart the worker after twenty tasks. Continue unrelated steps after an image failure. Publish two to four arrow-layout candidates only when every candidate passes the basic geometry gates. Never create alternate camera, framing, PAN or Zoom candidates. Preserve a real failed image for informed review; use an explicit regeneration placeholder only when no real image exists.

Read `references/render-rules.md` and `references/contracts.md` before changing render or publication contracts. Reuse schemas and derivation mechanisms across products; never reuse product-specific occurrence IDs, coordinates, PAN, ZOOM, paths, or license settings.
