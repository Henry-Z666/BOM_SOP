# 工程图内置箭头重构设计（drawing-arrows/v1）

日期：2026-08-12
状态：待用户审阅
上游依据：drawing-arrows-spike/v1（spike 实证三条官方口径）、arrow_policy/v1、jlink-async-boundary/v1、naming/v1

## 1. 背景与目标

现管线：模型窗 ExportRasterImage 光栅 + Python 2D overlay 叠箭头 + 像素标定仿射（k_px_per_mm / focus_affine / 爬梯标定族）。缺陷：标定链长、箭头非实体、像素口径随取景漂移、framing 闭环测量重。

目标：箭头迁移为 Creo 工程图内 DetailEntity **实体**几何，投影走 `View2D.GetTransform` **闭式解（零标定）**，交付仍为 JPG（PDF 仅中间格式，本地 100dpi 转换）。

非目标：不改 plan_steps 规划层；不改 naming/v1（images_dir 唯一真源）；不引入爆炸态视图（spike 实锤 `SetExploded` 取 ASM 内置爆炸态，与逐步位移不一致）；不建辅助 detail view（YAGNI，留后备）。

## 2. 方案选型

- **A（采纳）**：新建 `clean_run/src/java/DrawingRenderer.java` 与 Renderer 并行；`render_steps.ps1` 加 `-Channel drawing|model` 开关；parity 过审后整机退役旧路。理由：安全边界清晰、随时回滚、无半成品。
- B（否决）：Renderer.java（1700 行）原地重构——破坏已过审管线、无回滚点。
- C（否决）：混合（模型窗测量 + 工程图出箭头）——取景已闭式化，测量闭环无存在价值。

## 3. 目标管线（to-be）

`plan_steps.py`（不动）→ `render_steps.ps1 -Channel drawing` → **DrawingRenderer**（单 Creo 会话、多步循环），每步：

1. 打开装配（沿用 Renderer 开模链：OpenFile → GetActiveModel 回退 GetModelFromDescr → null throw）；
2. 激活该步 SimpRep（移植 Renderer rep 链：默认 EXCLUDE + INCLUDE visible_paths；官方异步成员级显示过滤器）；
3. `CreateDrawingFromTemplate`（官方 a3/a0 模板）→ 运行时清理：`List2DViews→View2D.Delete(true)` 删模板视图 + `ListDetailItems(DETAIL_NOTE, 1)→DetailItem.Delete()` 删模板注记（含乱码比例注记）；
4. 建 general view：`SetOrientation`（锁视角纯旋转，cam 列归一化）+ `SetScale`（闭式，见 §4.3）+ `SetLocation`（图纸中心）+ `SetExploded(false)`；
5. 箭头：`View2D.GetTransform` 行向量投影 anchor_exploded/anchor_complete → `LineDescriptor_Create`+`CreateDetailItem` 画杆+两鳍+尾十字（mm 常量，见 §4.1）；
6. `drw.Save()` → `PDFExportInstructions`（LAUNCH_VIEWER=false、RASTER_DPI=100）导出 → 编排层 pymupdf 100dpi 转 JPG → `out/images/{step}.jpg` + `render.json`（v2）。

→ `auto_review.py`（meta v2 / mm 口径分支）。编排层回收本轮衍生 parametric/xtop（spike_run.ps1 已验证，迁入 render_steps.ps1）。

### API 事实清单（均已 javap / 官方文档核实）

| 事实 | 依据 |
|---|---|
| `View2D.GetTransform` 行向量 p′=p·M，平移在第 4 行 | otk_cpp_doc user_guide/Transformations.html 的 4x4_transform_matrix.gif；与旧管线 camera_matrix 逐行同构互校 |
| PDF 导出前必须 `Model.Save()`，否则静默中止（trail「PDF 文件已被终止」） | spike 实证 |
| `SetExploded(true)` 取 ASM 内置爆炸态，不反映 DynamicPositioning | spike 实证 + View_States.html / General_Drawing_Views.html |
| `List2DViews()→View2Ds.getarraysize()/get(i).Delete(true)` | spike 实证（removed: 3） |
| `ListDetailItems(DetailType, Integer sheet)` + `DetailItem.Delete()` | javap DetailItemOwner |
| SimpRep（pfcSimpRep）= 官方异步成员级显示过滤器 | Renderer 现网代码 |
| 视图继承模型激活 rep | **P2 首验项**；回退链：wfc `WView2D.SetSimpRep`(otk.jar) → 仍败请示 |
| `PDFExportInstructions_Create` + `PDFOPT_LAUNCH_VIEWER=false` + `PDFOPT_RASTER_DPI=100` | spike 实证 |
| 异步 JVM 退出后派生 parametric/xtop 不退出，须按启动时间回收 | spike 实证 |

## 4. 关键工程决策

### 4.1 箭头图纸 mm 常量
杆宽 1.0、头长 14、头宽 6、尾十字半径 8 / 宽 0.6（图纸 mm，与视图 scale 解耦）。注记物理尺寸恒定 → review 阈值稳定，arrow_style_scale/v1 自然退役。颜色绿 (0,0.75,0.15)。

### 4.2 视图位姿
完整态 + 方向箭头（与旧管线同口径）；`SetExploded` 恒 false。渲染器**不含爆炸链**——anchor_exploded/anchor_complete 直接取 plan.json，无需 DynamicPositioning。

### 4.3 闭式取景 framing_drawing/v1
scale=1 试建视图 → `GetOutline` 得模型轮廓 W×H（mm）→ `s_fit = min((SW-2m)/W, (SH-2m)/H)`，m=15mm 边距；图纸尺寸参数化：默认 **A3（420×297）**，A0（1189×841）备用。整机上下文强制保留（禁抠图，继承「SOP 最终图必须保留整机上下文」决策）。动作区过小仅作记录；若 review  flagged，辅助 detail view 作后备（本期不建）。

### 4.4 render.json v2 schema
```
schema=clean-run-render-meta/v2; step_id; assembly_file; sha256; camera;
sheet_mm[2]; view_scale; view_transform[4][4]; view_outline_mm[4];
tail_sheet_mm[2]; head_sheet_mm[2]; arrow_mm{shaft_width,head_len,head_w,cross_r,cross_w};
visible_paths[]; dpi=100; jpg_px[2]
```

### 4.5 契约
naming/v1 不动；`.arrows.jpg` 后缀退役，基名即终图；images_dir 唯一真源。

### 4.6 review mm 口径
C7 箭头压座＝head 投影 vs seat anchor 残差 <2mm（meta 真值，免像素）；C8 fill＝视图轮廓面积/图纸面积；视觉类规则像素阈值按 px=mm×100/25.4 换算。

## 5. 分阶段交付（每阶段一个安全边界）

- **P1 编排卫生 + 运行时清理验证**：render_steps.ps1 迁入 reaping + PDF→JPG（通道无关，现 model 通道即受益）；DrawingSpike 加注记删除；30.25 冒烟。边界：全图单视图、无注记、无乱码、无残留进程。
- **P2 DrawingRenderer 核心单步**：spike 升级成类（单会话多步骨架）；render_steps.ps1 加 `-Channel drawing|model` 开关；移植 SimpRep 链；去爆炸链；meta v2 落盘；首验 SimpRep 继承（全图核对 visible/hidden）。边界：30.25 JPG 箭头压座残差 <2mm + 可见性正确。
- **P3 闭式取景**：§4.3 落地；3 步（30.25/30.13/30.20）数值+目验：整机完整、不越界、占比不低于 spike A0 观感。边界：3 步过。
- **P4 review 适配**：auto_review 加 meta v2 分支；3 步新旧 parity 全过审 + 抽查。边界：review 绿。
- **P5 全量 + 退役**：27 步全量 + 计时（单步 ≤ 现 +50%）；执行退役清单；memory 更新 drawing-arrows-spike/v2 + 退役清单。边界：全量过审 + 抽查 3 张。

## 6. 退役清单（P5 执行）

- Python：overlay_arrows.py、arrow_overlay.py、calibrate_cameras.py、screen_probe_cal.py、sp_ladder_cal.py、sp_diag.py、sp_diag2.py、flip_camera.py
- Java：Renderer.java、Calibrate.java、ScreenProbe.java、Probe.java、DiagFeatures.java、ApplyView.java、DrawingSpike.java（P2 吸收后）
- 保留：plan_steps.py、auto_review.py（适配）、validate_clean_run.py、run_jlink.ps1、build.ps1

## 7. 风险与开放项

1. 视图是否继承激活 SimpRep——P2 首验，回退链见 §3 表。
2. 性能：建图+PDF 慢于光栅；单会话多步已设计；P5 计时超阈则优化（视图/图纸复用）。
3. 字体乱码：干净画面无文本即绕开；将来加零件名注记须先补 win_chcn_font。
4. 模板注记删除行为——P1 冒烟验证；**预期**比例注记为 format（图框）所属，可能既不在 ListDetailItems 枚举内也 Delete 不动（只读），回退＝一次性 TemplateMaker 自制干净模板 SaveAs（已预留）。

## 8. 验收

单步：压座残差 <2mm；单视图无杂无乱码；可见性＝visible_paths。
全量：过审率 ≥ 现管线；抽查 3 张；单步耗时 ≤ 现 +50%。

## 9. 假设

plan.json 的 anchor/camera/explosion 策略可信（旧管线过审）；PDF→JPG 100dpi 标定与现管线同级。
