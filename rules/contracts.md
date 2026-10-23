# Contracts and hard gates

This file is the single current contract authority for the Agent and executable Skills.

## Authoritative assembly

For each top-level ASM batch, record the exact authoritative ASM filename/version, SHA-256, BOM root row and source-row scope, root coordinate system, actual opened model/version, and calibrated `fixed_123`/`fixed_456` camera basis. Abort when any locked file or hash changes. `assembly-lock/v2`, `creo-cad-forest/v1`, `bom-cad-forest/v1`, and `formal-render-forest/v1` carry parallel roots; each nested single-assembly payload retains its existing contract.

## Formal render task

Use `render-plan/v2` with `creo-render-task/v1`. Every formal task contains:

- stable task/step ID and BOM scope;
- full root paths for moving, receiver, and visible occurrence sets;
- authoritative assembly record and source-backed quantity evidence;
- intake BOM and full ASM/PRT tree hashes, revalidated before/after rendering and before publication;
- receiver-backed root translation and unchanged rotations, including a complete `occurrence_translation_vectors_root` map whose entries equal the common group vector for repeated parts in one BOM-row action;
- camera ID in the locked catalog, including deterministic `interface_oblique` under `installation-interface-view/v1`;
- `square-crop-presentation/v1` with `native-selected-fit/v2`, `final_frame=[1600,1600]`, `source_policy=native_viewport_overscan/v1`, and `crop_policy=fixed_center_square/v1`;
- constraint-backed `receiver_interface_selections`, each containing a stable receiver occurrence path, positive Creo item ID, and `item_type=SURFACE`;
- `selection_scope=moving_occurrences_and_receiver_surfaces/v1` and `coordinate_adjustment=none_native_surface_fit/v1`;
- `level_policy=fixed_native_selection_margin/v1` with initial `zoom_to_selected_level=0.70`, persisted-task compatibility at `0.80` and `0.85`, a bounded focus revision at `0.95`, one native command per render, and free/manual PAN/ZOOM forbidden;
- native arrow anchors, output audit references, process text, and status.

Process-aware tasks additionally retain `process_operation`, `process_confirmation`, `source_bom_rows`, `publication_parameter_status` and `torque-annotations/v1`. Revalidate every process-card/standard workbook digest and source text before compilation, rendering and publication. Do not replace the process confirmation between rendering and publication; a changed confirmation requires recompilation and rendering.

`torque-label-audit/v1` carries the annotation contract hash, exact occurrence/connection/text coverage, native solid-surface target coordinates and camera-plane text rectangles. Missing, edited or incomplete coverage blocks acceptance. A valid first-stage annotation audit still raises `TORQUE_VISUAL_REVIEW_REQUIRED`; it is not an automatic readability or occlusion pass.

Torque callouts use complete Chinese wording, such as `卡箍紧固力矩：10 N.m；紧固后划线`, retaining source units and instructions. Bare numeric labels are not accepted as the display convention. Chinese text uses native solid fills with dark lettering and a cream filled bubble, with clause wrapping and bounds checked; only explicitly applicable connections receive a label.

The validated task contract—not the publication workbook—is execution truth.

## Required audits

- `arrow-projection/v1` records covered occurrence paths, anchor source, complete/exploded root points, direction, merge coverage, and status. Each endpoint difference equals the audited translation for its covered occurrence(s).
- `native-framing-audit/v1` records task/image identity, Creo version/datecode, verified `ProCmdZoomIntoOutline`, actual selected scope, no coordinate adjustment, level, one-command limit, and free/manual PAN/ZOOM prohibition.
- `ai-image-review/v1` 只接收最终渲染图片和最小步骤上下文，记录模型、调用状态、通过结论和问题。它不得修改或豁免任何 CAD、occurrence、可见集、变换、相机、箭头、哈希或状态机硬门。

## Hard render gates

A formal image passes only when all apply:

- actual assembly name/version/hash matches the lock;
- every moving and receiver occurrence resolves by full path;
- moving count matches BOM quantity;
- visible set exactly matches the forward stage and contains no future occurrence;
- required receivers are visible;
- explosion is pure translation with unchanged rotations;
- camera matches the locked catalog or deterministically recomputed installation-interface basis;
- arrow count/merge coverage and same-CAD-point direction pass;
- the final raster contains at least one significant connected arrow component for every non-merged audited arrow;
- the formal image is exported by Creo as an undistorted native-aspect overscan with a 1600-pixel short edge, then centre-cropped once to exactly `1600×1600`; padding, stretching, upscaling, geometry-driven crop bounds, and publication-time crops are forbidden;
- no datum, source CAD annotation, weld cosmetic, UI, or unrelated geometry appears; confirmed generated torque callouts are permitted;
- output file exists and has the contracted dimensions.

Readability, composition, centering, clipping, apparent occlusion and arrow presentation are AI 图片审核范围。结构硬门通过且 AI 返回 `passed=true` 时可自动批准；AI 返回不通过、未配置、超时或格式无效时转人工复查。

Structural failure blocks publication and cannot由AI放行。AI 未批准的真实图片保留人工采用、跳过或有界重渲染路径；重渲染选项仍必须产生版本化步骤修订和可执行任务字段变更。

## Publication gates

- 只有结构硬门通过且经 AI 批准，或经明确人工决定批准的步骤进入正式工作簿。
- An explicitly audited `skip_from_publication` decision may omit a step from the current workbook without removing its durable render/review record; the step remains available for a later rerender and insertion.
- Apply a user-selected batch as one durable transaction: compile/render/validate at most once for the union of rerendered steps, preserve every non-invalidated prior result, and write/replace the XLSX once after all decisions have been applied in memory.
- Preserve the locked process/CAD dependency order and the current dynamic page layout. Process-aware pages may group adjacent operations only; regrouping interleaved operations must not change chronology. Legacy runs retain their existing BOM publication grouping.
- Populate material, quantity, process, control, and tooling fields from traceable sources.
- Validate sheet/image counts, relationships, print areas, merged cells, and visible page layout.

## Adaptive installation presentation (current)

See `rules/render-rules.md` and adaptive-installation-framing.md for the current v15 formula and fixed dual-role threshold. The executable task retains stage_geometry_root, receiver normal, complete native anchor coverage and `installation_view` with policy, derived spans and selected fit level. Legacy fixed margin fields remain for persisted-task compatibility; current levels must match recomputation, not a manual override.

`representative-installation/v1` is display coverage, not a replacement for the operation/BOM quantity. Retain original moving paths and source payload, total locations, representative path and approval/equivalence evidence. `installation-notes/v1` and `installation-note-audit/v1` are separate from torque claims. Positioning plus detail images must be accepted together with full coverage before publication; role detection alone does not satisfy this gate. The compiler records `installation-image-roles/v1` in `image_role_plan`; multi-image expansion/publication remains pending implementation.

Partial clipping of a noncritical distant body is allowed for the installation-interface view; the relevant contact, displayed moving part and arrows remain readable. Rigid long parts may be represented by their contact region with explicit full operation coverage. Geometric native-fit checks do not certify readability or occlusion.

`installation-view-coverage/v1` records location/detail role, full operation occurrence list and displayed subset. Detail groups must form a disjoint complete cover of the operation; location images do not substitute for this check. Native v6 framing includes same-CAD-point arrow envelopes in a physical, normalized camera plane. Diagnostic expansion exists; formal multi-view publication remains gated.


`cad-roi-screen-transform/v1` is an additional native transform audit, not an assertion that only ProCmdZoomIntoOutline ran. It requires a v11–v15 detail role, a positive recomputed installation feature span, `native-viewport-calibration/v1` evidence with two hashed marker snapshots, a viewport fingerprint and finite measured profile. The ROI audit binds the calibration hash, confirms temporary markers were removed and records its final pan/zoom. Missing or changed evidence fails CAD_ROI_EVIDENCE_INVALID; it must not fall back silently.

`receiver-side-clearance/v1` retains original receiver normal/vector and the derived display vector. Installed CAD points/rotations and complete operation occurrence coverage are preserved. Full visible-context AABB evidence is required; potential overlap is not asserted as an actual collision.


`cad_location_screen_transform/v1` requires a v12–v15 location role and the same bound native calibration evidence. `camera_plane_to_final_pixels` maps native planned text rectangles into the exported frame. Number labels must match the complete disjoint source grouping, have at least 24-pixel dimensions and 16-pixel frame margins. Fail missing/changed numbering evidence with LOCATOR_NOTE_EVIDENCE_INVALID; fail label clipping/smallness with LOCATOR_LABEL_CLIPPED/LOCATOR_LABEL_TOO_SMALL. Insufficient continuous context fails LOCATOR_CONTEXT_UNAVAILABLE. These locator gates do not waive any detail-image installation gate or approve publication by themselves.


## v15 通用取景补充契约

当前默认取景决策以 adaptive-installation-framing.md 为准。定位编号采用 ordered-perimeter-lane/v1，anchor_state=authoritative_complete，原生审核 anchor_source=native_complete_installation_anchor；编号目标必须与锁定安装完成点一致。禁止沿用爆炸后的曲面角点充当位置标识。

安装/局部图要求 native-occurrence-difference/v1 逐件可见性证据：同物理相机、其他姿态不变、排除对照及摘要完整、姿态恢复。此临时排除仅用于诊断对照，不改变正式阶段可见性与成图。遮挡、过小、定位上下文不足、标注重绑定缺失仍为阻断。


## 数字气泡与力矩的共同样式

两种编号保留不同语义：定位图的“位置 N”对应 detail_group_index；安装/局部图的“物料 N”对应固定 SOP 页物料表顺序。同物料各实例保持同一序号，但逐实例绘制和审核，不能沿用箭头合并结果减少标注。局部分组不重排物料序号。

两者与力矩共用 Creo DisplayList3D 原生渲染，样式 filled-cream-solid-dark/v1：米黄色实心底色、深色实心字体、边框与引线。物料单独标注时，字号兼顾安装特征尺度与当前待装件投影跨度，避让间距独立于字高；与力矩混排时沿用共享力矩字体基准。定位字号保留整体定位范围依据。禁止改为像素后叠加。

物料映射以 material-annotations/v1 保存页 ID、有序页表、编码、名称与完整 occurrence。先冻结分页与物料表再生成编号；分页或页表变化必须重新绑定和重出受影响图片。没有映射不猜测正式页序号。prepare_framing_samples.py 的 --material-map 可将固定页表接入小批量通用出图。

物料、力矩及普通说明通过 annotation_bundle 进入一次共同布局，connection_id 带独立通道前缀，原生 .note.json 绑定整个 bundle 摘要；验证提取各通道后分别检查，物料数字不能冒充力矩，物料覆盖不能被力矩标签代替。保护当前待装件、完整箭头及安装点附近一倍特征跨度的原生背景；远端承接件不再作为局部标注禁放区。气泡及引线包络纳入原生 ROI，检查边界与最小字号。

含已有标注的自动多图重绑定仍须明确绑定当前组；不能绕过此前的 annotated expansion 阻断。上述页表入口不等于已重建历史成品 SOP。
