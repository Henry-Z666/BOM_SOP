---
name: generate-creo-assembly-sop
description: Generate, diagnose, or publish a Creo assembly SOP pipeline from Excel BOM data and Creo Parametric ASM/PRT models. Use when a task involves BOM hierarchy planning, final-assembly occurrence mapping, staged visibility, pure-translation exploded installation images, fixed two-camera selection, same-CAD-point arrows, render validation, or writing validated images and BOM text into an SOP workbook.
---

# Generate Creo Assembly SOP

Use the BOM as the process hierarchy and the final Creo assembly as the only geometry, position, and camera truth. Drive Creo through the official asynchronous J-Link Java API; never use screen-coordinate automation.

## Start with a preflight

1. Locate the pipeline workspace. Prefer the current repository; otherwise ask for or locate the `BOM_SOP` checkout.
2. Run `scripts/preflight.ps1 -ProjectRoot <path>` from this skill before changing or executing a formal batch.
3. Read `references/render-rules.md` and `references/contracts.md` before editing a contract, render runner, or publication step.
4. Report the current phase, completed major BOM section, failures, and retry action while a long Creo batch is running. Do not silently wait through a long batch.

Treat a failed preflight or validation as a repair task. Fix in-scope deterministic defects, rerun the failed bounded step, and continue. Do not publish a failed image.

## Execute the workflow

### 1. Normalize the BOM

- Preserve BOM hierarchy, drawing number, model number, name, quantity, process text, control points, and tools.
- Match Creo models by drawing number first, then model number.
- Combine leaf parts that form one BOM-defined subassembly into one construction step when the hierarchy says they are installed together.
- Build bottom-level subassemblies in a close-up before moving the completed rigid group into its parent.
- Mark non-modeled raw material, sealing strip, or process-only rows as non-renderable only when the CAD evidence supports that classification.

### 2. Lock the authoritative assembly

- Select the highest Creo file version of the final total assembly and record its exact filename, version, SHA-256, and root coordinate system.
- Open that exact version through J-Link. Do not silently substitute an intermediate ASM or another file version.
- Recursively map BOM items to full root occurrence paths. Never use a bare component feature ID as the formal identity.

### 3. Plan forward installation stages

- For step `n`, show all completed steps plus the current moving occurrences and required receivers.
- Hide every future occurrence and every non-part auxiliary object.
- Move a completed subassembly as one rigid group; do not separate its children again.
- Derive the explosion vector from the receiver/contact normal in root coordinates. Explosion is translation only and must move away from the receiver.

### 4. Select one of two fixed cameras

- Use only the product-level `fixed_123` or its center-opposite `fixed_456`.
- Choose the one that clearly shows both moving occurrences and receiver locations. Never invent a third per-step camera.
- Keep orientation independent from PAN, ZOOM, CENTER, explosion direction, and output frame.

### 5. Render and annotate

- Run each formal step in an isolated Creo session against a disposable model copy.
- Keep source CAD read-only and never save session changes into it.
- Hide datum planes, axes, points, coordinate systems, curves, annotations, weld marks, and other non-part content.
- Generate green, thin, small-head arrows from the exploded position to the complete position using the same deterministic CAD anchor point.
- Use fixed output framing only. Do not dynamically crop based on detected geometry and do not upscale a cropped image.

### 6. Validate before accepting

Require all hard gates in `references/contracts.md`. At minimum verify exact forward visibility, BOM occurrence counts, visible receiver, pure translation, fixed-camera identity, arrow endpoint/direction, non-overlap or audited merge, image dimensions, and authoritative assembly hash.

Automatically inspect each exported image after validation. If moving parts or receivers are not readable, correct the contract and rerender that bounded step. Never solve a visibility failure by showing future parts.

### 7. Publish the SOP only after images pass

- Keep validated image generation separate from workbook publication.
- Use the existing project SOP template and make one worksheet/page per installation step unless the selected template explicitly defines another layout.
- Insert images in BOM order and populate only source-backed BOM/process/tool/control text.
- Run spreadsheet structural and visual validation before delivery.

## Reuse and migration

For a new product, calibrate the final assembly and its two fixed cameras once, then rebuild occurrence mapping and step contracts. Reuse the workflow and schemas, not product-specific occurrence IDs, translations, PAN, ZOOM, or BOM wording.

Legacy intermediate-assembly or relative-camera contracts may be read for diagnosis but must not produce a new formal batch.

