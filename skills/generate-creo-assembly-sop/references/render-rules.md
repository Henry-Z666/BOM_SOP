# Formal render rules

`rules/render-rules.md` is the repository's single rendering authority. Apply these current requirements:

- BOM hierarchy/order is process truth; the locked highest-version final ASM is geometry, occurrence, position, and camera truth.
- Stage visibility is completed steps plus current moving occurrences plus required receivers; future and auxiliary objects stay hidden.
- Explosion is receiver-backed root-coordinate translation only; rotations are unchanged. Resolve the unsigned Creo axis sign by maximizing worst AABB clearance across every same-axis constrained receiver, and split repeated occurrences when their signed directions differ. Keep the receiver normal as installation evidence, but permit a pre-render lateral display vector for a long bridge, or a contact-backed part with aspect ratio at least `3`, only when the normal vector retains material staged-context overlap and the lateral candidate removes it.
- Cameras are exactly `fixed_123` and `fixed_456`.
- Formal framing is `native_zoom_to_selected/v1`: select moving plus receiver occurrences, execute `ProCmdZoomIntoOutline` once, and use fixed relative margin `zoom_to_selected_level=0.85`; Creo's selected bounding-box fit supplies the size adaptation.
- Choose between only `fixed_123` and `fixed_456` before rendering by absolute receiver-axis visibility and the projected front-overlap of exploded activity against staged context. Creo surface-normal sign alone is not an outward-side proof.
- Each render attempt exports one raster; bounded scheduler retries get a fresh per-attempt budget.
- Absolute PAN/ZOOM, screen automation, probes, response caches, geometry-detected crops, and pixel-arrow fallbacks are forbidden.
- Arrows use one deterministic CAD surface point transformed from exploded to complete state and rendered by Creo `DisplayList3D`.
- The fixed 1600×1600 output must pass visibility, receiver, centering, arrow, clipping, dimension, and authoritative-assembly hard gates.
- Persist `native-framing-audit/v1`; never save changes to source CAD.
- Recheck the intake BOM and complete ASM/PRT tree hashes before and after rendering and before publication.

Read the full canonical file before changing render contracts or execution.
