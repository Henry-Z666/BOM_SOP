# Formal render rules

This file is the single current rendering authority for the Agent and all executable Skills.

## Truth and stage

1. The BOM defines material hierarchy and quantity. In the process-operation pipeline, confirmed, source-backed operations add process scope, ordering dependencies and parameters; keep every CAD receiver dependency and recompute forward state after ordering. Legacy runs without a process package retain BOM-backed ordering and text.
2. Each exact-matched top-level ASM batch locks its own highest-version authoritative ASM. That ASM alone defines occurrence identity, position, orientation, appearance, root coordinates, and fixed cameras inside the batch. Never merge coordinates or occurrence paths across parallel roots.
3. Intermediate assemblies and historical render outputs are diagnostic evidence only.

A BOM may be a forest with multiple parallel top-level ASM roots. Plan every root as an independent main batch, keep nested ASMs inside their containing batch, and concatenate validated tasks into one SOP. A row outside every root remains an explicit unscoped exception; never guess its batch. A product-title row is excluded only when title-level position, no exact ASM, no descendants, and no assembly instruction are all evidenced. Row number is never evidence.

For step `n`:

```text
visible = completed steps 1..n-1 + current moving occurrences + required receivers
hidden  = future occurrences + auxiliary/non-part objects
```

Use full root occurrence paths. Completed subassemblies remain rigid. Geometry may add a missing receiver dependency but may not silently reorder or omit BOM work.
Creo `FIX` against `ROOT` proves only final placement. It does not prove that a later BOM item is already installed, so it must not promote that occurrence into `initial_completed_occurrences` or make it visible before its BOM step. Only the BOM foundation may seed initial stage context.

## Explosion and cameras

- Select receiver evidence from the current occurrence's measured Creo assembly constraints. Prefer `INSERT`, then mate/alignment-style constraints; exclude receivers that move in the same step. If no usable receiver point and direction exist, leave the step unresolved instead of guessing.
- Treat Creo `SURFACE`/`INSERT` directions as unsigned axes. When native solid bounds exist, test both signed translations and choose the sign that maximizes the worst post-translation clearance across every same-axis constrained receiver. Use the receiver-point half-space only as a deterministic fallback when bounds are missing or the clearance scores tie.
- Group repeated occurrences from the same BOM row by compatible unsigned installation axis. Resolve one common signed direction for the whole group (majority clearance evidence, with stable occurrence order as an exact-tie fallback), and apply it to every occurrence in that shared step. Split when the measured installation axes are genuinely incompatible, or when contracting occurrence chains into a group creates a proven dependency cycle. In the latter case split only groups on that cycle, rebuild each member from its original CAD evidence, and retain all occurrences and receiver/physical precedence edges. Record `GROUP_DEPENDENCY_SPLIT`; an irreducible occurrence-level cycle still blocks planning.
- Use the deterministic display distance compiled from the locked assembly occurrence-origin extent (`8%` of its diagonal, or `80` when no usable extent exists).
- Explosion is translation only and preserves every 3×3 rotation matrix. The interface normal remains the installation-axis truth. If a normal explosion still has material AABB overlap with staged context and is ambiguous because it telescopes along a long bridge, or because a part with aspect ratio at least `3` has strong contact-backed lateral evidence, the presentation vector may switch to one root axis within the receiver plane. The lateral candidate must reduce overlap to a bounded minimum and is selected before rendering; it is not a pixel review or retry.
- A structured operator direction may confirm only the sign of an existing measured Creo receiver axis. Free text cannot generate coordinates, and the confirmation cannot replace the measured axis.
- Current installation framing uses deterministic `interface_oblique`, derived from the measured receiver normal and root up reference. For strongly elongated repeated groups, use the distribution direction projected into the receiver plane. Retain `fixed_123` and `fixed_456` for persisted-task compatibility. Camera changes affect presentation only; no geometry transform may change.
- Lock the derived camera in the executable task. No render-time random camera search or free rotation is allowed.
- The legacy lossless occurrence/interface label audit and its camera-repair choices are temporarily frozen; this does not prohibit the explicit v15 CAD ROI or moving-visibility evidence contracts. Do not generate audit rasters, emit `NO_ELIGIBLE_FIXED_CAMERA`, or ask the user to choose a rerender goal until the GUI can show the failed evidence as a preview.

## Native adaptive framing

- Current policy is `single-image-balanced-fit/v15`, implemented by `installation_view.py`; persisted policies retain their original values.
- Derive `ratio = receiver_union_span / max(moving_union_span, numerical_epsilon)` from locked native bounds. For a contact-face fit set `level = 0.50 + 0.32 * clamp(log2(max(ratio, 1))/6, 0, 1)`; for a multi-component group fit use `0.75 + 0.20 * clamp(log2(max(ratio, 1))/6, 0, 1)`. All ratios are dimensionless; use only a numeric epsilon for zero-span protection. Confirmed installation notes use 0.35; torque labels cap the value at 0.55. Do not tune a step by its row number, task ID, product name or manual multiplier.
- In Creo, select the native moving solid face nearest its constraint-backed anchor; resolve physical descendants for rigid subassemblies. Project selected faces and moving bounds into the locked camera plane. Use `margin = clamp(sqrt(max(1, body_span/face_span)), 1.20, 1.80)` for unannotated tasks; apply the viewport-aspect correction and divide the fit level by this margin. Under v6, normalize camera basis columns to physical units before comparing faces with native arrow anchors. Extend the margin to include both ends of every arrow and its head thickness about the selected-face center; do not cap this safety margin at the ordinary context-margin limit. Execute `ProCmdZoomIntoOutline` once.
- Do not fit the full receiver body just to preserve its outer extent. Keep useful local positioning context; distant noncritical body edges may leave the frame.
- Missing native contact faces may use the moving-component fit fallback with an explicit `component_fallback` audit. Missing required moving geometry, receiver or constraint evidence still blocks rendering.
- Single installation image is the default. Any nonempty moving set with `moving_union_span / max(installation_feature_span, numerical_epsilon) > 4` requests two roles: `location` and `detail`. The current threshold is fixed for regression, including a single large thin part. A positioning view may contain small parts, but must be paired with readable detail before publication.
- A representative detail is permitted only with confirmed equivalent installation coverage. Retain the complete operation occurrence set, quantity, representative path and approval/equivalence evidence. State the total number of locations explicitly (for example “共四处”). Different interfaces or installation methods need separate details; do not silently discard them.
- `installation_image_roles` currently detects requested roles. `expand_installation_views` provides diagnostic expansion: a location view plus spatial detail groups whose pairwise center distance is at most 4 times the installation feature span. Preserve all original operation paths and one-time complete detail coverage in `installation-view-coverage/v1`. Do not infer equivalent work. Formal compiler expansion and grouped publication are not yet integrated; keep these operations pending until reviewed.
- Record dimensions, composition policy and fit level in the task, and native selected face IDs / fallback in `.focus.json`. This is a CAD-driven heuristic. The native per-occurrence exclusion evidence additionally checks obvious occlusion and size; it does not certify every contact surface.
- Operator-supplied or free absolute PAN/ZOOM, screen-coordinate automation, probe renders, response caches, dynamic geometry crops, and post-crop upscaling are forbidden. Only the evidence-bound CAD ROI adapter described below may apply a derived post-command ScreenTransform.
- One render attempt may export one formal raster plus one native background diagnostic under `native-background-difference/v1`. The diagnostic has exactly the same camera, crop, stage and model positions, taken before adding arrows/callouts. It is not a framing probe or an alternative formal image. Hash both rasters and reject missing/changed evidence; use their difference to distinguish arrows from native red CAD geometry. Scheduler-level retries remain bounded and receive one new raster budget per attempt.
- Camera label rasters are currently frozen and are not produced. The retained implementation must remain internal and dormant.
- Persist `native-framing-audit/v1` with Creo version/datecode, command verification, selection scope, and level.
- Recompute the intake BOM and full ASM/PRT tree hashes before and after formal rendering and again before publication. Any file-set or digest change blocks the run.

## Arrows

- For every moving occurrence, choose a deterministic local solid-surface anchor.
- Transform the same local point through complete and exploded occurrence transforms.
- Draw from `anchor_exploded_root` to `anchor_complete_root` with a thick red line and clearly visible head.
- For a grouped repeated-part step, every arrow endpoint difference must equal the group's common translation vector.
- Prefer one arrow per occurrence. Merge only same-direction conflicts with explicit occurrence coverage.
- Native `DisplayList3D` is the only formal renderer; JPEG pixel overlays are forbidden.

## Torque callouts

- A torque claim retains its source workbook digest, sheet, cell or DrawingML anchor, original unit, tolerance and applicability context. No diameter-only substitution or unchecked unit conversion is permitted.
- Explicit operation and connection confirmations bind a selected claim to full moving occurrence paths and acknowledge every competing claim. Incomplete confirmations remain pending and cannot enter formal SOP publication, even if image review passes.
- Generated torque bubbles and leaders use a temporary native `DisplayList3D`; confirmed installation-quantity notes may use the same native renderer through a separate `installation-notes/v1` channel and `.note.json` audit. Never encode quantity notes as torque claims. Preserve the cleanup rule for source CAD annotations.
- The first-stage renderer uses camera-facing vector text, native solid-surface anchors and projected bounds. Avoid text/geometry overlap, overlapping labels, crossed leaders and installation-arrow coverage. No suitable layout must fail explicitly.
- Raster clipping, pointer interpretation and depth occlusion still require explicit human review. The initial implementation must not claim lossless occlusion certification or auto-approve annotated rasters.

## Appearance and hard framing checks

- Render solid part geometry only; hide datums, curves, quilts, annotations, weld cosmetics, and UI/session text in Creo.
- Apply the locked stage exclusions before auxiliary cleanup. Traverse only stage-related full occurrence paths (visible models, ancestors and included rigid descendants); excluded future branches cannot contribute graphics. Deduplicate layer edits by model, never the occurrence traversal of a repeated ASM. Reuse the authoritative model within a bounded native batch only after restoring every transform, temporary visibility state and display list; never reuse un-restored stage state.
- Before auxiliary layer edits, resolve the entire stage-related model subtree. A missing visible model blocks the step with `CREO_VISIBLE_MODEL_MISSING` and its full occurrence path/model name; do not spend time cleaning earlier models first or automatically repeat an unchanged missing-input failure. Model collection is per job, not a cross-step CAD-state cache.
- Export an undistorted native-viewport overscan whose short edge is 1600 pixels, then apply exactly one deterministic centre crop to `1600×1600`. Padding/letterboxing, stretching, upscaling, geometry-detected crop bounds, and per-image adaptive crops are forbidden.
- Before the selected-object fit, scale the configured fit level by the inverse native viewport aspect ratio so every moving item and concrete receiver interface remains inside the central square crop. The final square raster must keep every moving item, receiver interface, installation boundary, and arrow readable and centered within the compiled gate.
- Non-critical completed background may cross at most two frame edges; moving items, required receivers, and arrows may not be lost. The number of significant red arrow components may not be lower than the validated arrow-audit count.
- Raster validation remains a hard gate even though Creo performs the native centering.

## Async J-Link boundary

- Use official asynchronous pfc APIs: `ComponentPath.SetTransform` under `DynamicPositioning`, temporary SimpRep exclusions, Selection Buffer, `RunMacro`, layers, and display options.
- Do not introduce synchronous-only `wfc*` calls into the asynchronous renderer.
- Component-label evidence must be produced through the same asynchronous J-Link session or an equivalent lossless CAD projection adapter; never recover occurrence identity by classifying the beauty image.
- Source CAD is read-only. Each formal task opens an isolated copy and erases its session state afterward.

## Forbidden fallbacks

- No computer-use or screen-coordinate automation.
- No AI-generated geometry or guessed occurrence.
- No intermediate ASM as a formal image source.
- No relative X/Y/Z camera rotations, pixel arrows, probe framing, or silent fallback from activity-coordinate fit.
- No future parts added merely to make a picture easier to understand.

## Clearance-bounded presentation and group coverage (v8)

- A disproportionately long compiled display travel may shrink to 1.5 times the maximum moving thickness projected along the existing translation axis. Require a complete set of native moving bounds, uniform existing translations, one context bound per visible nonmoving occurrence, and no strict AABB intersection with any visible context at the candidate position. Otherwise retain the original distance. Keep the original vector and clearance evidence in `presentation_translation`; update every expected exploded arrow point. Never change the complete position, rotation, dependency order or source CAD.
- Single parts use protected contact-face framing; multiple moving occurrences use protected component-group framing. All arrows remain same-CAD-point arrows. Red arrow display uses `COLOR_DRAWING`, with restoration after the job, so selection-highlight colors are not recolored by the arrow renderer.
- Diagnostic location images have native group-number bubbles linked to full CAD occurrence paths; details must cover every original moving occurrence once. No torque claim is generated by a location number. A location image is not an installation detail and cannot by itself approve a whole operation.
- A useful positioning context requires a visible reference body whose native span is at least half the moving-group union span. If absent, mark `insufficient_continuous_visible_context`; do not add future parts or invent a product background. This is a conservative heuristic and still needs contextual visual review.
- Validation reports distinguish positional-reference and installation-detail roles. Do not lower detail arrow gates to make locator images pass; retain the pixel diagnostics and separately review locator numbers, references and all-group completeness. Native callout placement and apparent occlusion still need review.

Under v10, `installation_feature_span = min(largest_part_span, 2 * maximum_part_thickness_along_receiver_normal)`. Detail groups use complete constraint-anchor positions, falling back to native box centers only when anchors are incomplete. Detail images always use protected contact-face framing, including for multi-occurrence local groups; location images use complete-group framing. Retain complete disjoint operation coverage.


### Native CAD ROI (v11–v15)

For detail-role views, the native adapter may derive an additional ScreenTransform from the physical anchor/arrow ROI. This is distinct from the initial native selection fit, which retains its existing command audit. Human-supplied PAN/ZOOM remain prohibited.

Two temporary marker-calibration exports are permitted once per viewport fingerprint. They calibrate known native CAD coordinates, not beauty-based candidate cameras or real-part identity. Delete the temporary display list and restore colors/transform in finally before exporting the background and final image. Bind calibration evidence by SHA-256. Final output remains a native viewport export with the same fixed square centre crop; no subject-driven pixel crop, padding or stretching.

Installation-view arrow hard gates remain unchanged. A locator failing these gates is not a publishable installation image, even if its native group numbers are visible. Accept complete location/detail sets only after role-appropriate coverage and readability checks.


v12 and later location-role views protect the union of native geometry bounds, exact planned group-number boxes and leaders. Annotation layout is shared between planning and native display, not recomputed by a second independent renderer. Validate native group-number identities, a disjoint complete detail-group cover, matching note contract hashes, 16-pixel label frame margins and at least 24-pixel box dimensions. Location views use these gates rather than installation-arrow raster readability; all CAD arrow endpoint/occurrence audits remain active. Detail images retain all installation-arrow raster gates. Complete group publication and human-readable occlusion acceptance remain separate.


## v15 通用取景补充契约

当前默认取景决策以 adaptive-installation-framing.md 为准。定位编号采用 ordered-perimeter-lane/v1，anchor_state=authoritative_complete，原生审核 anchor_source=native_complete_installation_anchor；编号目标必须与锁定安装完成点一致。禁止沿用爆炸后的曲面角点充当位置标识。

安装/局部图要求 native-occurrence-difference/v1 逐件可见性证据：同物理相机、其他姿态不变、排除对照及摘要完整、姿态恢复。此临时排除仅用于诊断对照，不改变正式阶段可见性与成图。遮挡、过小、定位上下文不足、标注重绑定缺失仍为阻断。


## 数字气泡与力矩的共同样式

两种编号保留不同语义：定位图的“位置 N”对应 detail_group_index；安装/局部图的“物料 N”对应固定 SOP 页物料表顺序。同物料各实例保持同一序号，但逐实例绘制和审核，不能沿用箭头合并结果减少标注。局部分组不重排物料序号。

两者与力矩共用 Creo DisplayList3D 原生渲染，样式 filled-cream-solid-dark/v1：米黄色实心底色、深色实心字体、边框与引线。物料单独标注时，字号兼顾安装特征尺度与当前待装件投影跨度，避让间距独立于字高；与力矩混排时沿用共享力矩字体基准。定位字号保留整体定位范围依据。禁止改为像素后叠加。

物料映射以 material-annotations/v1 保存页 ID、有序页表、编码、名称与完整 occurrence。先冻结分页与物料表再生成编号；分页或页表变化必须重新绑定和重出受影响图片。没有映射不猜测正式页序号。prepare_framing_samples.py 的 --material-map 可将固定页表接入小批量通用出图。

物料、力矩及普通说明通过 annotation_bundle 进入一次共同布局，connection_id 带独立通道前缀，原生 .note.json 绑定整个 bundle 摘要；验证提取各通道后分别检查，物料数字不能冒充力矩，物料覆盖不能被力矩标签代替。保护当前待装件、完整箭头及安装点附近一倍特征跨度的原生背景；远端承接件不再作为局部标注禁放区。气泡及引线包络纳入原生 ROI，检查边界与最小字号。

含已有标注的自动多图重绑定仍须明确绑定当前组；不能绕过此前的 annotated expansion 阻断。上述页表入口不等于已重建历史成品 SOP。
