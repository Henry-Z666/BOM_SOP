# Contracts and hard gates

`rules/contracts.md` is the repository's single contract authority. Current formal tasks use `render-plan/v2` / `creo-render-task/v1`, full root occurrence paths, locked final-ASM identity/hash, fixed cameras, `native-selected-fit/v1`, native same-CAD-point arrows, and a fixed 1600×1600 output.

The selected-fit contract must declare:

```text
selection_scope = moving_and_receiver_occurrences/v1
level_policy = fixed_native_selection_margin/v1
zoom_to_selected_level = 0.75
max_commands_per_render = 1
absolute_pan_zoom_forbidden = true
```

Require `arrow-projection/v1` and `native-framing-audit/v1`. Visibility, receiver inclusion, pure translation, fixed camera, final-raster centering, clipping, arrow coverage, clean appearance, image dimensions, authoritative assembly identity, and unchanged intake BOM/full ASM-PRT tree hashes are hard gates. Recheck the input hashes before and after rendering and before publication. One attempt emits one formal raster; only bounded scheduler-level system retries receive another attempt.

Read the full canonical file before changing contracts or validation.
