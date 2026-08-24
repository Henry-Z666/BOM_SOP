# Formal render rules

`rules/render-rules.md` is the repository's single rendering authority. Apply these current requirements:

- BOM hierarchy/order is process truth; the locked highest-version final ASM is geometry, occurrence, position, and camera truth.
- Stage visibility is completed steps plus current moving occurrences plus required receivers; future and auxiliary objects stay hidden.
- A Creo `FIX` to `ROOT` is final-placement evidence, not process-order evidence. Never promote a later BOM occurrence into initial completed context; only the BOM foundation may seed it.
- Explosion is receiver-backed root-coordinate translation only; rotations are unchanged. Keep repeated occurrences from one BOM row in one step when their unsigned axes are compatible, resolve one common signed direction from the group's clearance evidence, and apply that vector to every occurrence and arrow. Keep the receiver normal as installation evidence, but permit a pre-render lateral display vector for a long bridge, or a contact-backed part with aspect ratio at least `3`, only when the normal vector retains material staged-context overlap and the lateral candidate removes it.
- Cameras are exactly `fixed_123` and `fixed_456`.
- Formal framing is `native_receiver_surface_fit/v1`: select every moving occurrence plus the exact constraint-backed receiver surfaces by occurrence path and Creo surface ID, then execute `ProCmdZoomIntoOutline` once. Never select the complete receiver body and never apply a post-command PAN/ZOOM correction. Use initial `zoom_to_selected_level=0.70`; retain `0.80` and `0.85` for persisted-task compatibility and `0.95` only for the bounded focus revision.
- Choose between only `fixed_123` and `fixed_456` before rendering by absolute receiver-axis visibility and the projected front-overlap of exploded activity against staged context. Creo surface-normal sign alone is not an outward-side proof.
- Each render attempt exports one raster; bounded scheduler retries get a fresh per-attempt budget.
- Free/manual PAN/ZOOM, screen automation, probes, response caches, geometry-detected crops, and pixel-arrow fallbacks are forbidden.
- Arrows use one deterministic CAD surface point transformed from exploded to complete state and rendered by Creo `DisplayList3D`.
- Export an undistorted native-viewport overscan with a 1600-pixel short edge, then apply one fixed centre crop to `1600×1600`. Adjust selected-fit level by inverse viewport aspect before rendering so activity remains inside the square. Padding, stretching, upscaling, and geometry-adaptive crop bounds are forbidden.
- Persist `native-framing-audit/v1`; never save changes to source CAD.
- Recheck the intake BOM and complete ASM/PRT tree hashes before and after rendering and before publication.

Read the full canonical file before changing render contracts or execution.
