# Contracts and hard gates

`rules/contracts.md` is the repository's single contract authority. Current formal tasks use `render-plan/v2` / `creo-render-task/v1`, full root occurrence paths, locked final-ASM identity/hash, fixed cameras, `native-selected-fit/v2`, native same-CAD-point arrows, and `square-crop-presentation/v1` output at `1600×1600`.

The selected-fit contract must declare:

```text
selection_scope = moving_occurrences_and_receiver_surfaces/v1
coordinate_adjustment = none_native_surface_fit/v1
level_policy = fixed_native_selection_margin/v1
zoom_to_selected_level = 0.70
max_commands_per_render = 1
manual_pan_zoom_forbidden = true
```

Each task must also carry `receiver_interface_selections` with receiver occurrence path, positive Creo surface ID, and `item_type=SURFACE`. A grouped repeated-part task carries a complete `occurrence_translation_vectors_root` map whose entries all equal the common BOM-row installation vector.

Require `arrow-projection/v1` and `native-framing-audit/v1`. Visibility, receiver inclusion, pure translation, fixed camera, final-raster centering, clipping, padding/stretch rejection, arrow coverage, at least one significant raster component per non-merged audited arrow, fixed centre-square crop, clean appearance, exact `1600×1600` dimensions, authoritative assembly identity, and unchanged intake BOM/full ASM-PRT tree hashes are hard gates. Recheck the input hashes before and after rendering and before publication. One attempt emits one formal raster; only bounded scheduler-level system retries receive another attempt.

An audited `skip_from_publication` decision omits that step from the current workbook but retains its review/rerender entry. A multi-selection batch is one transaction: union its rerenders, run compile/render/validate at most once, preserve non-invalidated results, and replace the XLSX once.

Read the full canonical file before changing contracts or validation.
