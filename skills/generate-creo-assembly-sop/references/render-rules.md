# Formal render rules

`rules/render-rules.md` is the repository's single rendering authority. Apply these current requirements:

- BOM hierarchy/order is process truth; the locked highest-version final ASM is geometry, occurrence, position, and camera truth.
- Stage visibility is completed steps plus current moving occurrences plus required receivers; future and auxiliary objects stay hidden.
- Explosion is receiver-backed root-coordinate translation only; rotations are unchanged.
- Cameras are exactly `fixed_123` and `fixed_456`.
- Formal framing is `native_zoom_to_selected/v1`: select moving plus receiver occurrences, execute `ProCmdZoomIntoOutline` once, and use fixed relative margin `zoom_to_selected_level=0.42`; Creo's selected bounding-box fit supplies the size adaptation.
- Each render attempt exports one raster; bounded scheduler retries get a fresh per-attempt budget.
- Absolute PAN/ZOOM, screen automation, probes, response caches, geometry-detected crops, and pixel-arrow fallbacks are forbidden.
- Arrows use one deterministic CAD surface point transformed from exploded to complete state and rendered by Creo `DisplayList3D`.
- The fixed 1600×1600 output must pass visibility, receiver, centering, arrow, clipping, dimension, and authoritative-assembly hard gates.
- Persist `native-framing-audit/v1`; never save changes to source CAD.

Read the full canonical file before changing render contracts or execution.
