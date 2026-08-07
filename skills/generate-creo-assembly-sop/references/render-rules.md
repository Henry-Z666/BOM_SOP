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

## Explosion

- Use the receiver/contact surface normal transformed into final-ASM root coordinates.
- Translate the moving object away from the receiver. Do not move within the receiver plane.
- Preserve every 3x3 rotation matrix exactly.
- Use enough distance to distinguish the moving object from its target without creating an unnecessarily long arrow.
- Keep explosion selection independent of camera selection.

## Cameras

- `fixed_123`: the calibrated, upright product default octant.
- `fixed_456`: the exact center-opposite position direction with the same product up reference.
- A formal step chooses one of these two values only.
- Choose the camera in which moving items, receiver locations, and installation boundary are all readable and the receiver face does not collapse into a line.
- PAN and ZOOM only compose the frame; they never change view direction.

## Arrows

- For every moving occurrence, select a deterministic local surface anchor.
- Transform that same anchor through complete and exploded occurrence transforms.
- Draw from `anchor_exploded_root` to `anchor_complete_root`.
- Use green thin lines and small heads. Prefer one arrow per occurrence.
- Select alternate deterministic anchors to avoid overlap. Merge same-direction conflicts only with an explicit coverage audit.
- A rigid subassembly movement produces one representative arrow for the group.

## Appearance and framing

- Render only solid part geometry. Hide datum planes, axes, points, coordinate systems, curves, surface quilts, annotations, weld symbols, and UI/session text.
- Fit and center inside Creo. Use one deterministic fixed crop only when the publication frame requires it.
- Never use geometry-detected or per-image adaptive cropping. Never upscale a crop.
- Formal installation images currently use a square 1600x1600 frame unless the selected publication contract says otherwise.

## Forbidden fallbacks

- No computer-use or screen-coordinate automation.
- No AI-generated geometry or guessed missing occurrence.
- No intermediate ASM as an image source.
- No relative `X:`, `Y:`, or `Z:` camera rotations in a new formal contract.
- No future parts added merely to make a picture easier to understand.

