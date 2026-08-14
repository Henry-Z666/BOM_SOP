# 工程图内置箭头重构 实施计划（drawing-arrows/v1）

> **For agentic workers:** 用 superpowers:executing-plans 逐任务执行。步骤用 `- [ ]` 复选框跟踪。每阶段有明确安全边界（可编译/可验证/无半成品）。

**Goal:** 把箭头从 2D overlay 迁移为 Creo 工程图内 DetailEntity 实体，投影走 View2D.GetTransform 闭式解（零标定），交付 JPG。

**Architecture:** 新建 DrawingRenderer.java 与 Renderer 并行（方案 A）；render_steps.ps1 加 -Channel 开关；PDF 仅中间格式，编排层本地 100dpi 转 JPG。

**Tech Stack:** J-Link (pfcasync.jar) / PowerShell / pymupdf(fitz) / MiniJson。

**上游 spec:** docs/superpowers/specs/2026-08-12-drawing-arrows-refactor-design.md

**已核实 API（javap/官方文档）：** Save()、List2DViews→getarraysize/get/Delete(true)、ListDetailItems(DetailType,Integer)、DetailItem.Delete()、DetailType.DETAIL_NOTE、PDFExportInstructions_Create/PDFOPT_LAUNCH_VIEWER/PDFOPT_RASTER_DPI、CreateBoolArgValue/CreateIntArgValue、GeneralViewCreateInstructions(SetExploded/SetScale)、LineDescriptor_Create/DetailEntityInstructions_Create/ColorRGB_Create。行向量投影 p'=p·M（平移在第4行）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/java/render_steps.ps1` | 渲染编排，加 -Channel 开关 + reaping + PDF→JPG | 改 |
| `src/java/DrawingSpike.java` | 加模板注记删除（P1 验证载体） | 改 |
| `src/java/DrawingRenderer.java` | 工程图箭头渲染核心（单步，meta v2） | 新建 |
| `src/py/auto_review.py` | meta v2 / mm 口径分支（P4） | 改 |
| overlay_arrows.py / 标定族 / Renderer.java 等 | 退役清单（P5） | 删（P5 执行） |

---

## Task 1（P1）：render_steps.ps1 迁入 reaping + 通道骨架 + template_dir

**Files:** Modify `clean_run/src/java/render_steps.ps1`

- [ ] **Step 1：加参数、runStart、template_dir、reaping、PDF→JPG helper**

在 `param(` 块加：
```powershell
    [ValidateSet("model","drawing")] [string]$Channel = "model"
```
在 `Write-Host "[RENDER-ORCH] rendering ..."` 行之前加：
```powershell
$runStart = Get-Date
```
在 `$config | Set-Content "$session\config.pro" -Encoding ASCII` 之前加（drawing 通道解析官方模板，model 通道无害）：
```powershell
    $config += "template_dir C:\Program Files\PTC\Creo 13.4.0.0\Common Files\templates"
```
把循环内的 `& "$runner\run_jlink.ps1" -ClassName Renderer ...` 段替换为按通道分发（drawing 通道先出 PDF，转 JPG 后清理）：
```powershell
    Write-Host "[RENDER-ORCH] ---- step index $i channel=$Channel (session $session) ----"
    $className = if ($Channel -eq "drawing") { "DrawingRenderer" } else { "Renderer" }
    & "$runner\run_jlink.ps1" -ClassName $className `
        -JavaArgs @($plan, "$i", $imgDir, "`"$parametric`"") `
        -SessionDir $session -TimeoutSeconds 900
    if ($LASTEXITCODE -ne 0) { throw "render step $i failed with exit $LASTEXITCODE" }

    if ($Channel -eq "drawing") {
        $stepId = $planDoc.steps[$i].step_id
        $pdf = Join-Path $imgDir "$stepId.pdf"
        $jpg = Join-Path $imgDir "$stepId.jpg"
        if (Test-Path $pdf) {
            python -c "import fitz,sys; d=fitz.open(sys.argv[1]); d[0].get_pixmap(dpi=100).save(sys.argv[2])" $pdf $jpg
            if ($LASTEXITCODE -ne 0) { throw "pdf->jpg failed for $stepId" }
            Remove-Item $pdf -Force
            Write-Host "[RENDER-ORCH] jpg -> $jpg"
        } else { throw "drawing channel produced no pdf for $stepId" }
    }
```
在脚本末尾 `Write-Host "[RENDER-ORCH] all steps rendered ..."` 之前加（spike_run.ps1 已验证的回收，按 runStart 过滤，不误杀用户会话）：
```powershell
Get-Process -Name parametric, xtop -ErrorAction SilentlyContinue |
    Where-Object { $_.StartTime -gt $runStart } |
    ForEach-Object {
        Write-Host "[RENDER-ORCH] reaping $($_.ProcessName) pid $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
```

- [ ] **Step 2：语法检查** — Run: `pwsh -NoProfile -Command "& { $null = [scriptblock]::Create((Get-Content -Raw 'clean_run\src\java\render_steps.ps1')) }"` Expected: 无输出（解析成功）。

---

## Task 2（P1）：DrawingSpike 加模板注记删除

**Files:** Modify `clean_run/src/java/DrawingSpike.java`（template view 删除循环之后）

- [ ] **Step 1：在 `System.out.println("[SPIKE] template views removed: " + removed);` 之后插入注记删除块**
```java
            // ---- template notes (e.g. the garbled scale note) also clutter
            //      the sheet; DetailItemOwner.ListDetailItems enumerates them.
            //      Expected: the scale note is format(sheet-frame) owned and
            //      may be read-only -> catch per-item, fall back to a clean
            //      template later if needed (spec risk #4). ----
            int notesRemoved = 0;
            try {
                com.ptc.pfc.pfcDetail.DetailItems notes = drw.ListDetailItems(
                        com.ptc.pfc.pfcDetail.DetailType.DETAIL_NOTE, 1);
                if (notes != null) {
                    for (int i = notes.getarraysize() - 1; i >= 0; i--) {
                        try {
                            notes.get(i).Delete();
                            notesRemoved++;
                        } catch (Throwable noteFailure) {
                            System.out.println("[SPIKE] note delete skipped: "
                                    + noteFailure);
                        }
                    }
                }
            } catch (Throwable noteListFailure) {
                System.out.println("[SPIKE] note listing failed: "
                        + noteListFailure);
            }
            System.out.println("[SPIKE] template notes removed: " + notesRemoved);
```

- [ ] **Step 2：编译** — Run: `pwsh -NoProfile -File clean_run\src\java\build.ps1` Expected: `[BUILD] OK`.

---

## Task 3（P1）：冒烟验证（安全边界）

- [ ] **Step 1：跑 spike** — Run: `pwsh -NoProfile -File clean_run\src\java\spike_run.ps1 -Step 24` Expected: 退出码 0；stdout 含 `template notes removed:`、`exported ... exists=true`、`reaping`。
- [ ] **Step 2：全图目验** `work\spike\spike_drw.jpg` — 单视图、箭头压座、无残留进程（`Get-Process parametric,xtop` 为空）。**实测结论：notes removed=0，注记未被枚举（format 所属实锤，spec 风险#4 主路径失效）；但画面上乱码注记是否影响交付待诊断。**
- [ ] **Step 3（追加）：注记诊断** — DrawingSpike 注记块改诊断版（sheet 参数分别试 1/null，打印枚举数、类型、文本），重跑取证；根据证据决定：可删→主路径复活；不可删→一次性 TemplateMaker 自制干净模板（需空白 format，pfcDrawingFormat 接口为空无创建工厂，可能须手工建 .frm 或 UI 操作）。

**P1 边界：全图单视图、无乱码、无残留进程。** —— **P1 已完成（2026-08-12）**：blank-sheet 路线全链实证（AddSheet→SetCurrentSheetNumber→RegenerateSheet→DeleteSheet(1)），全图目验干净白面+箭头压座。模板视图/注记删除块不再需要（图纸页随 DeleteSheet 丢弃）。

---

## Task 4（P2）：新建 DrawingRenderer.java（核心单步）

**Files:** Create `clean_run/src/java/DrawingRenderer.java`

- [ ] **Step 1：写入完整类**（DrawingSpike 移植 + sha256 硬门 + SimpRep 可见性 + meta v2，无爆炸链——anchor 直接取 plan）

```java
import com.ptc.cipjava.intseq;
import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcAssembly.Assembly;
import com.ptc.pfc.pfcAssembly.pfcAssembly;
import com.ptc.pfc.pfcAsyncConnection.AsyncConnection;
import com.ptc.pfc.pfcAsyncConnection.pfcAsyncConnection;
import com.ptc.pfc.pfcBase.ColorRGB;
import com.ptc.pfc.pfcBase.Matrix3D;
import com.ptc.pfc.pfcBase.Point3D;
import com.ptc.pfc.pfcBase.Transform3D;
import com.ptc.pfc.pfcBase.pfcBase;
import com.ptc.pfc.pfcDetail.DetailEntityInstructions;
import com.ptc.pfc.pfcDetail.pfcDetail;
import com.ptc.pfc.pfcDrawing.Drawing;
import com.ptc.pfc.pfcDrawing.DrawingCreateOption;
import com.ptc.pfc.pfcDrawing.DrawingCreateOptions;
import com.ptc.pfc.pfcGeometry.pfcGeometry;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcSimpRep.CreateNewSimpRepInstructions;
import com.ptc.pfc.pfcSimpRep.SimpRepActionType;
import com.ptc.pfc.pfcSimpRep.SimpRepItem;
import com.ptc.pfc.pfcSimpRep.SimpRepItems;
import com.ptc.pfc.pfcSimpRep.pfcSimpRep;
import com.ptc.pfc.pfcView2D.GeneralViewCreateInstructions;
import com.ptc.pfc.pfcView2D.View2D;
import com.ptc.pfc.pfcView2D.pfcView2D;
import com.ptc.pfc.pfcWindow.Window;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;

/**
 * drawing-arrows/v1 renderer (scheme A, parallel to Renderer.java).
 * Single planned step -> drawing with built-in DetailEntity arrows.
 * Zero pixel calibration: anchors projected via View2D.GetTransform
 * (row-vector p'=p*M; translation in last row). Complete pose + arrow
 * as motion indicator (SetExploded is NOT used: the ASM stored exploded
 * state disagrees with the per-step anchors).
 *
 * args: [0] plan.json [1] step index [2] outDir [3] parametric cmd
 */
public class DrawingRenderer {

    private static final double VIEW_SCALE = 0.5;   // P3 replaces with closed-form
    private static final double SHEET_W = 1189.0, SHEET_H = 841.0; // A0
    // arrow sheet-mm constants (decoupled from view scale)
    private static final double SHAFT_W = 1.0, HEAD_LEN = 14.0, HEAD_W = 6.0,
            CROSS_R = 8.0, CROSS_W = 0.6;

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("usage: DrawingRenderer <plan.json> <stepIndex> "
                    + "<outDir> [parametricCmd]");
            System.exit(2);
        }
        String planPath = args[0];
        int stepIndex = Integer.parseInt(args[1]);
        String outDir = args[2];
        String proCmd = args.length > 3 ? args[3] : "";

        Map<String, Object> plan = (Map<String, Object>) Renderer.MiniJson.parse(
                new String(Files.readAllBytes(Paths.get(planPath)),
                        StandardCharsets.UTF_8));
        List<Object> steps = (List<Object>) plan.get("steps");
        Map<String, Object> step = (Map<String, Object>) steps.get(stepIndex);
        String stepId = (String) step.get("step_id");
        List<Object> movingList = (List<Object>) step.get("moving");
        List<Object> visibleList = (List<Object>) step.get("visible_paths");
        String cameraId = (String) step.get("camera");
        Map<String, Object> mv = (Map<String, Object>) movingList.get(0);
        double[] tailW = vec3(mv.get("anchor_exploded"));
        double[] headW = vec3(mv.get("anchor_complete"));

        Map<String, Object> manifest = (Map<String, Object>) Renderer.MiniJson.parse(
                new String(Files.readAllBytes(
                        Paths.get(planPath).resolveSibling("manifest.json")),
                        StandardCharsets.UTF_8));
        String expectedHash = (String) manifest.get("sha256_at_batch_start");
        List<Object> cameraRows = (List<Object>)
                ((Map<String, Object>) manifest.get("cameras")).get(cameraId);
        double[][] cam = new double[4][4];
        for (int r = 0; r < 4; r++)
            for (int c = 0; c < 4; c++)
                cam[r][c] = ((Number)
                        ((List<Object>) cameraRows.get(r)).get(c)).doubleValue();

        System.loadLibrary("pfcasyncmt");
        AsyncConnection conn = null;
        try {
            System.out.println("[DRW] step " + stepId + " camera=" + cameraId
                    + " starting Creo ...");
            conn = pfcAsyncConnection.AsyncConnection_Start(proCmd, null);
            Session session = conn.GetSession();

            String asmFile = (String) plan.get("assembly");
            String asmName = asmFile.replaceFirst("\\.\\d+$", "");
            // hard gate: SHA-256 of the assembly copy in the session cwd
            String actualHash = sha256(asmFile);
            if (!actualHash.equalsIgnoreCase(expectedHash)) {
                throw new IllegalStateException("HARD GATE FAILED: hash mismatch "
                        + actualHash + " != " + expectedHash);
            }
            System.out.println("[DRW] hash gate passed: "
                    + actualHash.substring(0, 16) + "...");

            ModelDescriptor descr =
                    pfcModel.ModelDescriptor_CreateFromFileName(asmName);
            Window opened = session.OpenFile(descr);
            if (opened != null) opened.Activate();
            Model model = session.GetActiveModel();
            if (model == null) { Thread.sleep(2000); model = session.GetActiveModel(); }
            if (model == null) model = session.GetModelFromDescr(descr);
            if (model == null) throw new IllegalStateException("[DRW] active model null");
            Assembly assembly = (Assembly) model;
            System.out.println("[DRW] opened " + assembly.GetFileName());

            // ---- visibility: SimpRep default EXCLUDE + visible INCLUDE
            //      (official async member-level filter; Renderer L313-330 port).
            //      The drawing view inherits the model's ACTIVE rep (spec risk#1). ----
            String repName = "DRWCLEAN_" + stepIndex;
            CreateNewSimpRepInstructions repIns =
                    pfcSimpRep.CreateNewSimpRepInstructions_Create(repName);
            repIns.SetIsTemporary(true);
            repIns.SetDefaultAction(SimpRepActionType.SIMPREP_EXCLUDE);
            SimpRepItems repItems = SimpRepItems.create();
            for (Object vo : visibleList) {
                SimpRepItem item = pfcSimpRep.SimpRepItem_Create(
                        pfcSimpRep.SimpRepCompItemPath_Create(pathToIds((String) vo)));
                item.SetAction(pfcSimpRep.SimpRepInclude_Create());
                repItems.append(item);
            }
            repIns.SetItems(repItems);
            assembly.ActivateSimpRep(assembly.CreateSimpRep(repIns));
            System.out.println("[DRW] simp_rep=" + repName
                    + " includes=" + repItems.getarraysize());

            // ---- drawing from the official template ----
            DrawingCreateOptions opts = DrawingCreateOptions.create();
            opts.append(DrawingCreateOption.DRAWINGCREATE_DISPLAY_DRAWING);
            Drawing drw = session.CreateDrawingFromTemplate(
                    "drw" + stepIndex, "a0_drawing", descr, opts);
            Window drwWin = session.GetModelWindow(drw);
            if (drwWin != null) drwWin.Activate();

            // ---- blank-sheet route (P1-validated): the template sheet 1
            //      carries the format frame + garbled scale note (format-owned,
            //      undeletable via ListDetailItems).  Add a format-less sheet,
            //      build the view THERE, then drop sheet 1. ----
            int blankSheet = drw.AddSheet();
            drw.SetCurrentSheetNumber(blankSheet);

            // ---- locked-camera orientation: pure rotation (cam col normalized) ----
            double colN = Math.sqrt(cam[0][0] * cam[0][0]
                    + cam[1][0] * cam[1][0] + cam[2][0] * cam[2][0]);
            Matrix3D m3 = Matrix3D.create();
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++) m3.set(r, c, cam[r][c] / colN);
            m3.set(3, 0, 0.0); m3.set(3, 1, 0.0); m3.set(3, 2, 0.0); m3.set(3, 3, 1.0);
            Transform3D orientation = pfcBase.Transform3D_Create(m3);

            Point3D loc = Point3D.create();
            loc.set(0, SHEET_W / 2.0); loc.set(1, SHEET_H / 2.0); loc.set(2, 0.0);
            GeneralViewCreateInstructions ins = pfcView2D
                    .GeneralViewCreateInstructions_Create(assembly, blankSheet, loc, orientation);
            ins.SetExploded(false);   // complete pose; see class doc
            ins.SetScale(VIEW_SCALE);
            View2D view = drw.CreateView(ins);
            // fresh sheet must be current + regenerated before the view
            // geometry resolves (else GetOutline zero / GetTransform identity)
            drw.RegenerateSheet(blankSheet);
            System.out.println("[DRW] view name=" + view.GetName()
                    + " scale=" + view.GetScale());

            // ---- closed-form projection: View2D.GetTransform (row vector) ----
            Matrix3D vmm = view.GetTransform().GetMatrix();
            double[] tail2 = apply(vmm, tailW);
            double[] head2 = apply(vmm, headW);
            System.out.println("[DRW] tail sheet=(" + fmt(tail2[0]) + ","
                    + fmt(tail2[1]) + ") head sheet=(" + fmt(head2[0]) + ","
                    + fmt(head2[1]) + ")");

            // ---- built-in arrow: shaft + two fins + tail cross (mm constants) ----
            drawLine(drw, view, tail2, head2, SHAFT_W);
            double vx = head2[0] - tail2[0], vy = head2[1] - tail2[1];
            double len = Math.hypot(vx, vy), ux = vx / len, uy = vy / len;
            double px = -uy, py = ux;
            drawLine(drw, view, new double[]{head2[0] - HEAD_LEN * ux + HEAD_W * px,
                    head2[1] - HEAD_LEN * uy + HEAD_W * py}, head2, SHAFT_W);
            drawLine(drw, view, new double[]{head2[0] - HEAD_LEN * ux - HEAD_W * px,
                    head2[1] - HEAD_LEN * uy - HEAD_W * py}, head2, SHAFT_W);
            drawLine(drw, view, new double[]{tail2[0] - CROSS_R, tail2[1]},
                    new double[]{tail2[0] + CROSS_R, tail2[1]}, CROSS_W);
            drawLine(drw, view, new double[]{tail2[0], tail2[1] - CROSS_R},
                    new double[]{tail2[0], tail2[1] + CROSS_R}, CROSS_W);
            drw.RegenerateSheet(blankSheet);
            // drop the format sheet; only the clean blank sheet remains
            drw.DeleteSheet(1);

            // ---- Save BEFORE PDF export (trail: unsaved drawing aborts silently) ----
            drw.Save();
            String pdfFile = outDir + "\\" + stepId + ".pdf";
            drwWin = session.GetModelWindow(drw);
            if (drwWin != null) { drwWin.Activate(); drwWin.Repaint(); }
            com.ptc.pfc.pfcExport.PDFExportInstructions pdfIns =
                    com.ptc.pfc.pfcExport.pfcExport.PDFExportInstructions_Create();
            pdfIns.SetFilePath(pdfFile);
            com.ptc.pfc.pfcExport.PDFOptions pdfOpts =
                    com.ptc.pfc.pfcExport.PDFOptions.create();
            com.ptc.pfc.pfcExport.PDFOption noViewer =
                    com.ptc.pfc.pfcExport.pfcExport.PDFOption_Create();
            noViewer.SetOptionType(com.ptc.pfc.pfcExport.PDFOptionType.PDFOPT_LAUNCH_VIEWER);
            noViewer.SetOptionValue(com.ptc.pfc.pfcArgument.pfcArgument
                    .CreateBoolArgValue(false));
            pdfOpts.append(noViewer);
            com.ptc.pfc.pfcExport.PDFOption dpiOpt =
                    com.ptc.pfc.pfcExport.pfcExport.PDFOption_Create();
            dpiOpt.SetOptionType(com.ptc.pfc.pfcExport.PDFOptionType.PDFOPT_RASTER_DPI);
            dpiOpt.SetOptionValue(com.ptc.pfc.pfcArgument.pfcArgument.CreateIntArgValue(100));
            pdfOpts.append(dpiOpt);
            pdfIns.SetOptions(pdfOpts);
            drw.Export(pdfFile, pdfIns);
            java.io.File pdfCheck = new java.io.File(pdfFile);
            if (!pdfCheck.exists()) throw new IllegalStateException("[DRW] pdf missing");

            // ---- render.json v2 (mm truths for review) ----
            writeMeta(Paths.get(outDir, stepId + ".render.json").toString(),
                    stepId, asmFile, actualHash, cameraId, VIEW_SCALE,
                    tail2, head2, vmm, visibleList);
            System.out.println("[DRW] done");
            System.exit(0);
        } finally {
            if (conn != null) { try { conn.End(); } catch (Throwable t) { } }
        }
    }

    private static void drawLine(Drawing drw, View2D view, double[] a,
            double[] b, double width) throws jxthrowable {
        Point3D p1 = Point3D.create(); p1.set(0, a[0]); p1.set(1, a[1]); p1.set(2, 0.0);
        Point3D p2 = Point3D.create(); p2.set(0, b[0]); p2.set(1, b[1]); p2.set(2, 0.0);
        com.ptc.pfc.pfcGeometry.CurveDescriptor line =
                pfcGeometry.LineDescriptor_Create(p1, p2);
        DetailEntityInstructions dei =
                pfcDetail.DetailEntityInstructions_Create(line, view);
        ColorRGB green = pfcBase.ColorRGB_Create(0.0, 0.75, 0.15);
        dei.SetColor(green);
        dei.SetWidth(width);
        drw.CreateDetailItem(dei);
    }

    /** Row-vector p'=p*M: translation in the LAST ROW (official guide). */
    private static double[] apply(Matrix3D m, double[] w) throws jxthrowable {
        double[] out = new double[2];
        out[0] = m.get(0, 0) * w[0] + m.get(1, 0) * w[1]
                + m.get(2, 0) * w[2] + m.get(3, 0);
        out[1] = m.get(0, 1) * w[0] + m.get(1, 1) * w[1]
                + m.get(2, 1) * w[2] + m.get(3, 1);
        return out;
    }

    private static void writeMeta(String path, String stepId, String asmFile,
            String hash, String cameraId, double scale, double[] tail,
            double[] head, Matrix3D vmm, List<Object> visibleList) throws Exception {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"schema\": \"clean-run-render-meta/v2\",\n");
        sb.append("  \"step_id\": ").append(q(stepId)).append(",\n");
        sb.append("  \"assembly_file\": ").append(q(asmFile)).append(",\n");
        sb.append("  \"sha256\": ").append(q(hash)).append(",\n");
        sb.append("  \"camera\": ").append(q(cameraId)).append(",\n");
        sb.append("  \"sheet_mm\": [").append(SHEET_W).append(",").append(SHEET_H).append("],\n");
        sb.append(String.format(java.util.Locale.US, "  \"view_scale\": %.6f,%n", scale));
        sb.append("  \"view_transform\": ").append(matrixJson(vmm)).append(",\n");
        sb.append(String.format(java.util.Locale.US,
                "  \"tail_sheet_mm\": [%.3f,%.3f],%n", tail[0], tail[1]));
        sb.append(String.format(java.util.Locale.US,
                "  \"head_sheet_mm\": [%.3f,%.3f],%n", head[0], head[1]));
        sb.append("  \"arrow_mm\": {\"shaft_width\":").append(SHAFT_W)
                .append(",\"head_len\":").append(HEAD_LEN)
                .append(",\"head_w\":").append(HEAD_W)
                .append(",\"cross_r\":").append(CROSS_R)
                .append(",\"cross_w\":").append(CROSS_W).append("},\n");
        sb.append("  \"visible_paths\": [");
        for (int i = 0; i < visibleList.size(); i++) {
            if (i > 0) sb.append(",");
            sb.append(q((String) visibleList.get(i)));
        }
        sb.append("],\n");
        sb.append("  \"dpi\": 100\n");
        sb.append("}\n");
        try (PrintWriter w = new PrintWriter(
                Files.newBufferedWriter(Paths.get(path), StandardCharsets.UTF_8))) {
            w.print(sb);
        }
    }

    private static String matrixJson(Matrix3D m) throws jxthrowable {
        StringBuilder sb = new StringBuilder("[");
        for (int r = 0; r < 4; r++) {
            if (r > 0) sb.append(",");
            sb.append("[");
            for (int c = 0; c < 4; c++) {
                if (c > 0) sb.append(",");
                sb.append(String.format(java.util.Locale.US, "%.6f", m.get(r, c)));
            }
            sb.append("]");
        }
        return sb.append("]").toString();
    }

    private static String q(String s) { return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""; }
    private static String fmt(double v) { return String.format(java.util.Locale.US, "%.2f", v); }

    private static double[] vec3(Object o) {
        List<Object> l = (List<Object>) o;
        return new double[]{((Number) l.get(0)).doubleValue(),
                ((Number) l.get(1)).doubleValue(), ((Number) l.get(2)).doubleValue()};
    }

    private static intseq pathToIds(String path) throws Exception {
        String[] parts = path.split("/");
        intseq ids = intseq.create();
        for (int i = 1; i < parts.length; i++) ids.append(Integer.parseInt(parts[i]));
        return ids;
    }

    private static String sha256(String file) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] data = Files.readAllBytes(Paths.get(file));
        byte[] h = md.digest(data);
        StringBuilder sb = new StringBuilder();
        for (byte b : h) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}
```

- [ ] **Step 2：编译** — Run: `pwsh -NoProfile -File clean_run\src\java\build.ps1` Expected: `[BUILD] OK`。（manifest 字段已核实：hash=`sha256_at_batch_start`、cameras=dict。）

---

## Task 5（P2）：drawing 通道单步冒烟 + SimpRep 继承首验

- [x] **Step 1：跑 drawing 通道** — 实测：首进程 CreateView flake（XToolkitBadInputs×2）→ 编排层进程级重试闭环（回收+重跑一次成功）；jpg+render.json(v2) 落盘；无残留进程。
- [x] **Step 2：全图目验 + SimpRep 继承核对** — 实测：SimpRep 继承首验通过（outline (499.44,302.13)-(689.56,537.87) 收窄于完整态、右侧环卡箍隐藏、includes=84）；箭头压座。**追加修复**：乱码比例注记根因=custom scale note（SetScale 生成、绑定视图只读）；官方修法 default_draw_scale 0.5（config）+ 不调 SetScale，实证注记消失（PDF 95212→90476）。

**P2 边界：30.25 JPG 箭头压座 + 可见性正确 + meta v2 落盘。** —— **P2 已完成（2026-08-12）**。

---

## Task 6（P3，门控于 P2 通过）：闭式取景 framing_drawing/v1

- [x] **Step 1：图纸尺寸参数化** — 实测：SheetData.SetWidth/SetHeight 对空白页 no-op；改走官方 `a3_drawing` 模板继承 A3（template_dir 已就位）。
- [x] **Step 2：D 坐标系标定** — 实测解码：view loc/outline/GetTransform 活在归一化 D 系（宽度恒 1000）；GetSheetTransform 给 D→纸 mm：k=m[0][0]=paper_width/1000、y 加 t=m[3][1]（a0:1.189/-81.109，a3:0.420/-28.688）。所有纸 mm 数学（s_fit/loc 居中/箭头常量 /k/meta ×k+t）经 k/t 换算；三重互校吻合。
- [x] **Step 3：闭式取景+union outline** — probe 视图 SetScale(1.0) 量轮廓+tail/head 锚投影（probe 的 scale note 随 probe.Delete 消亡）→ union 包围盒（pad=CROSS_R+2 纸 mm）→ s_fit → **SheetOwner.SetSheetScale(blankSheet,sFit,model)** 下发页默认比例（官方 API 运行时生效；正式视图不调 SetScale，不重生只读注记）；formalLoc=纸中心D−(union中心D−模型中心D)×sFit 使 union 整体居中（修 step13 尾十字裁切：tail y 0.05→18.78）。
- [x] **Step 4：3 步（24/13/20）数值+全图定验** — 实测：24 s_fit=0.4599 view(103.55,19.60)-(311.46,277.40) 箭头压座；13 s_fit=0.3775 尾十字完整入纸；20 s_fit=0.4599 tail(311.06,201.24) 无越界。三张全图定验：整机完整、居中、占比 85-90%、无乱码、无残留进程（CreateView flake 由编排层进程级重试回收）。

**P3 边界：3 步过。** —— **P3 已完成（2026-08-13）**。

## Task 7（P4，门控于 P3）：auto_review 加 meta v2 / mm 口径分支

- [x] **Step 1：v2 分支落地** — auto_review.py 按 `schema==clean-run-render-meta/v2` 分发：C6 旋转锁（view_transform 行∥manifest cam 行，与 v1 转置关系；g=RMS×√3=s_fit/k 闭式自校）、C7 ink 中心 vs 纸中心（px=mm×dpi/25.4，tol 8mm）、C8 视图轮廓面积/图纸面积（floor 25%）、C3 完整姿策略 N/A、C9 绿墨总量+tail/head 落位（tol 3mm）；VLM 单图适配 prompts（V2 豁免绿箭头、V3 箭头头部 grounding、V5/V9 单图改写）；加 --first/--count scoping；review_config 加 C7_center_tol_mm/C8_min_outline_fill/C9_arrow_loc_tol_mm。
- [x] **Step 2：3 步复验** — 实测：24/20 全绿；13 初跑 C4 败→根因实证：4 路径（457/11、457/14、51/13364/111、114）在 **step 0 已安装**（step 0 moving 含之），父根 457/51/13364 于 step 18 再移动→C4 朴素逻辑把“已安装子件可见”误判为“后续步零件提前出现”（t13 C4 部分根因，通道无关）。修 rule_later_blocked：completed 改为 scope 无关前缀集（步 i = steps 0..i-1 moving 并集）+ 已安装子树豁免→13 全绿（1/1），v1 步 15 的 C4 同修。
- [x] **Step 3：v1 回归** — 13..24 混跑：v1 步 14-19/21-24 的 C3/C4/V3 败因均为 t13 既有异常清单，v1 分支代码未动、无回归。

**P4 边界：review 绿。** —— **P4 已完成（2026-08-13）**：13/20/24 三步全绿（规则+VLM 11/11）；v1 无回归（C4 假阳同修）；t13 收窄为仅 C3（planned vs applied 平移分歧，模型通道专属，P5 退役后失效）。

## Task 8（P5，门控于 P4）：全量 + 退役

27 步全量 `-Channel drawing` + 计时（单步 ≤ 现 +50%）；执行退役清单（overlay_arrows.py、arrow_overlay.py、calibrate_cameras.py、screen_probe_cal.py、sp_ladder_cal.py、sp_diag*.py、flip_camera.py、Renderer.java、Calibrate/ScreenProbe/Probe/DiagFeatures/ApplyView/DrawingSpike.java）；更新 review_loop.ps1（去掉 overlay/calibrate 调用）；memory 更新 drawing-arrows-spike/v2。

**P5 边界：全量过审 + 抽查 3 张。**

**P5 修正（2026-08-13，用户三条意见）**：① 新批次必须新 task_code（naming/v1）：本批 `drw_arrow_test_20260813_1037`，旧 full_loop_test 批作废；② exploded-parity/v1：drawing 通道用 ComponentFeat.SetPosition 真实 placement 爆炸（Drawing 视图不依赖临时 DynamicPositioning），尾十字删除；③ session 复制优化（批级单次复制）可行性已分析，待批后独立任务实施。

**P5 进展二（2026-08-13）**：batch-session/v1 已落地并全量验证：render_steps.ps1 批开始单次复制 models 到 `work\session-batch`（CAD 只读、config.pro 每步重写、trail-<i> 按步隔离），27 步零真失败。review 侧 exploded-parity 适配：C4 豁免本步自身 moving 子树（step-0 457/11 挂在 step-18 父根 457 下的假阳）；V3/V5/V9 prompt 改爆炸语义（箭尾贴爆开活动件、头部指空座位）。Round 1 = 25/27；残留 2 项为 VLM 判定：30.4 V5（长板座位标记不醒目，边界判定）、30.6 V3（O 形圈过小，t14 型小件可读性缺陷），均非管线 bug，留待用户裁决。

---

## 自审

- spec 覆盖：§3 管线→Task1/4；§4.1→Task4 常量；§4.2→Task4 SetExploded(false)；§4.3→Task6；§4.4→Task4 writeMeta；§4.5→Task1 JPG 基名；§4.6→Task7；§5 P1-P5→Task1-8；§6 退役→Task8；§7 风险→Task3/5 门控。无遗漏。
- 无占位符：P1/P2 代码完整；P3-P5 为验证门控的边界描述（其精确代码依赖 P1/P2 运行时结论，非占位）。
- 类型一致：MiniJson/sha256/pathToIds/apply/drawLine 与 DrawingSpike、Renderer 同名同签名；meta 字段名 tail_sheet_mm/head_sheet_mm/view_scale 在 Task4 与 Task7 一致。
