import com.ptc.cipjava.intseq;
import com.ptc.pfc.pfcAssembly.Assembly;
import com.ptc.pfc.pfcAssembly.pfcAssembly;
import com.ptc.pfc.pfcAsyncConnection.AsyncConnection;
import com.ptc.pfc.pfcAsyncConnection.pfcAsyncConnection;
import com.ptc.pfc.pfcBase.Matrix3D;
import com.ptc.pfc.pfcBase.Outline3D;
import com.ptc.pfc.pfcBase.Point3D;
import com.ptc.pfc.pfcBase.Transform3D;
import com.ptc.pfc.pfcBase.pfcBase;
import com.ptc.pfc.pfcDetail.DetailEntityInstructions;
import com.ptc.pfc.pfcDetail.pfcDetail;
import com.ptc.pfc.pfcDrawing.Drawing;
import com.ptc.pfc.pfcDrawing.DrawingCreateOption;
import com.ptc.pfc.pfcDrawing.DrawingCreateOptions;
import com.ptc.pfc.pfcGeometry.CurveDescriptor;
import com.ptc.pfc.pfcGeometry.pfcGeometry;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcSimpRep.CreateNewSimpRepInstructions;
import com.ptc.pfc.pfcSimpRep.SimpRep;
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
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;

/**
 * drawing-arrows/v1 renderer (scheme A, parallel to Renderer.java).
 * One planned step -> one drawing carrying built-in DetailEntity arrows.
 *
 * Zero pixel calibration: the tail/head anchors are projected with
 * View2D.GetTransform, the official closed-form 3D-model-to-2D-sheet map
 * (row-vector convention p' = p*M: translation lives in the LAST ROW,
 * sheet-x/sheet-y are the first two columns; see otk_cpp_doc
 * user_guide/Transformations.html, 4x4_transform_matrix.gif).
 *
 * Pose policy (exploded-parity/v1): the planned per-step translations
 * are applied before the view is created, exactly like Renderer
 * (ComponentPath.SetTransform under DynamicPositioning; wfc
 * ExplodedState authoring is synchronous-OTK only, see
 * jlink-async-boundary/v1).  The arrow tail sits on the exploded part
 * (anchor_exploded), the head presses the seat (anchor_complete).
 * SetExploded is NOT used: it activates the ASM's stored exploded
 * state, which disagrees with the per-step DynamicPositioning anchors.
 *
 * Clean sheet (blank-sheet route, P1-validated): the official template's
 * sheet 1 carries the format frame + garbled scale note (format-owned,
 * undeletable through ListDetailItems).  A format-less sheet is added,
 * the view is built there (after SetCurrentSheetNumber + RegenerateSheet,
 * otherwise GetOutline is zero and GetTransform identity), then sheet 1
 * is deleted.
 *
 * args: [0] plan.json  [1] step index  [2] outDir  [3] parametric cmd
 */
public class DrawingRenderer {

    // arrow sheet-mm constants, decoupled from the view scale (the view
    // scale comes from the closed-form s_fit via SheetOwner.SetSheetScale;
    // P2 proved SetScale on the real view spawns a read-only scale note).
    // pipeline-stability/v1: values are overwritten from
    // rendering_config.json at startup (single authority shared with the
    // planner); the literals below are only the documented fallbacks.
    private static double SHAFT_W = 1.0, HEAD_LEN = 14.0, HEAD_W = 6.0;
    // pipeline paper: A3 landscape via the official a3_drawing template
    // (SheetData.SetWidth/SetHeight proved a no-op on the blank sheet);
    // 15 mm margin each side (framing_drawing/v1)
    private static String TEMPLATE = "a3_drawing";
    private static double MARGIN = 15.0;

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

        // pipeline-stability/v1: every tuned constant comes from ONE file
        // (clean_run/rendering_config.json), shared with the planner, so
        // the two sides can never desync. plan.json lives in
        // clean_run/out/data/, hence three levels up.
        Path cfgPath = Paths.get(planPath).toAbsolutePath().getParent()
                .getParent().getParent().resolve("rendering_config.json");
        Map<String, Object> cfg = (Map<String, Object>) Renderer.MiniJson.parse(
                new String(Files.readAllBytes(cfgPath), StandardCharsets.UTF_8));
        Map<String, Object> drwCfg = (Map<String, Object>) cfg.get("drawing");
        TEMPLATE = (String) drwCfg.get("template");
        MARGIN = ((Number) drwCfg.get("margin_mm")).doubleValue();
        SHAFT_W = ((Number) drwCfg.get("arrow_shaft_w_mm")).doubleValue();
        HEAD_LEN = ((Number) drwCfg.get("arrow_head_len_mm")).doubleValue();
        HEAD_W = ((Number) drwCfg.get("arrow_head_w_mm")).doubleValue();
        System.out.println("[DRW] rendering_config loaded: template=" + TEMPLATE
                + " margin=" + MARGIN + " arrow=" + SHAFT_W + "/" + HEAD_LEN
                + "/" + HEAD_W);

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
            // hard gate: SHA-256 of the session copy (Renderer parity)
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
            if (model == null) {
                Thread.sleep(2000);
                model = session.GetActiveModel();
            }
            if (model == null) model = session.GetModelFromDescr(descr);
            if (model == null) {
                throw new IllegalStateException("[DRW] active model null after OpenFile");
            }
            Assembly assembly = (Assembly) model;
            System.out.println("[DRW] opened " + assembly.GetFileName());

            // ---- explosion (exploded-parity/v1): apply the planned
            //      per-step translations as REAL placement changes
            //      (ComponentFeat.SetPosition) BEFORE the probe view, so
            //      outline/framing and the arrow anchors see the same
            //      pose.  Drawing views do NOT render the temporary
            //      ComponentPath.SetTransform (DynamicPositioning) that
            //      the 3D-window Renderer uses - only real placement
            //      changes reach the view geometry. ----
            java.util.List<Renderer.ExplosionRecord> explosionRecs =
                    new java.util.ArrayList<>();
            for (Object mo : movingList) {
                Map<String, Object> mvEntry = (Map<String, Object>) mo;
                List<Object> tr = (List<Object>) mvEntry.get("translation");
                double dx = ((Number) tr.get(0)).doubleValue();
                double dy = ((Number) tr.get(1)).doubleValue();
                double dz = ((Number) tr.get(2)).doubleValue();
                if (dx == 0.0 && dy == 0.0 && dz == 0.0) continue;
                explosionRecs.add(Renderer.translateViaFeature(session,
                        assembly, pathToIds((String) mvEntry.get("path")),
                        dx, dy, dz));
            }
            System.out.println("[DRW] exploded " + explosionRecs.size()
                    + " occurrence(s)");

            // ---- visibility: SimpRep default EXCLUDE + visible INCLUDE
            //      (official async member-level display filter; ported from
            //      Renderer).  rep-drawing-owner/v1 (root-caused
            //      2026-08-13, API-surface scan): every window keeps its
            //      OWN SimpRep display state - activating a rep on the
            //      assembly never reaches the drawing window (it stays
            //      Master Rep, codepoints-proven).  The drawing carries its
            //      own rep context via Model2D.AddSimplifiedRep and the
            //      views inherit the DRAWING's active rep - so the rep is
            //      registered + activated ON THE DRAWING. ----
            String viewRepName = null;

            // ---- drawing from the official template (sheet 1 = format) ----
            DrawingCreateOptions opts = DrawingCreateOptions.create();
            opts.append(DrawingCreateOption.DRAWINGCREATE_DISPLAY_DRAWING);
            Drawing drw = session.CreateDrawingFromTemplate(
                    "drw" + stepIndex, TEMPLATE, descr, opts);
            Window drwWin = session.GetModelWindow(drw);
            if (drwWin != null) drwWin.Activate();

            SimpRep visRep = createVisibilityRep(assembly, visibleList,
                    stepIndex);
            if (visRep != null) {
                // register the rep on the drawing FIRST: ActivateSimpRep in
                // the drawing window is a silent no-op for reps the drawing
                // does not know (rep-drawing-owner/v1)
                drw.AddSimplifiedRep(visRep);
                assembly.ActivateSimpRep(visRep);
                viewRepName = visRep.GetName();
                System.out.println("[DRW] simp_rep=" + viewRepName
                        + " includes=" + (visibleList != null
                                ? visibleList.size() : 0)
                        + " drw_active=" + activeRepDebug(assembly));
            } else {
                System.out.println("[DRW] no visible_paths - full assembly view");
            }

            // ---- blank-sheet route: add a format-less sheet (inherits the
            //      template's A3 size), build the view there, then drop the
            //      format sheet 1 ----
            int blankSheet = drw.AddSheet();
            drw.SetCurrentSheetNumber(blankSheet);
            // flake-root-cause/v3 (2026-08-13): CreateView on a freshly
            // added but NOT regenerated sheet throws XToolkitBadInputs -
            // regenerating up front removes that first failure.  The very
            // first RegenerateSheet can itself throw on the first process
            // (sheet-level quirk); the delete+re-add rebuild recovers it.
            // The SimpRep is unaffected by any of this: it is owned by the
            // drawing (rep-drawing-owner/v1), not by the sheet flow.
            try {
                drw.RegenerateSheet(blankSheet);
            } catch (Throwable firstRegen) {
                System.out.println("[DRW] first-sheet regenerate failed ("
                        + firstRegen + ") - rebuild sheet");
                double[] sh0 = rebuildSheet(drw, blankSheet);
                blankSheet = (int) sh0[0];
            }
            com.ptc.pfc.pfcSheet.SheetData blankData =
                    drw.GetSheetData(blankSheet);
            double sheetW = blankData.GetWidth();
            double sheetH = blankData.GetHeight();
            // ---- sheet coordinate calibration (framing_drawing/v1, empirically
            //      decoded on a0 AND a3): view loc/outline/transform live in a
            //      normalised D-system (width 1000); GetSheetTransform maps
            //      D -> paper mm: x_p = k*x_D, y_p = k*y_D + t, with
            //      k = paper_width/1000.  All paper-mm maths below goes
            //      through k/t. ----
            Matrix3D sheetM = drw.GetSheetTransform(blankSheet).GetMatrix();
            double kSheet = sheetM.get(0, 0);
            double tSheet = sheetM.get(3, 1);
            System.out.println("[DRW] blank sheet=" + blankSheet
                    + " " + sheetW + "x" + sheetH
                    + " k=" + fmt(kSheet) + " t=" + fmt(tSheet));

            // ---- locked-camera orientation: pure rotation (guide: the
            //      orientation transform must be pure rotation) ----
            double colN = Math.sqrt(cam[0][0] * cam[0][0]
                    + cam[1][0] * cam[1][0] + cam[2][0] * cam[2][0]);
            Matrix3D m3 = Matrix3D.create();
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++)
                    m3.set(r, c, cam[r][c] / colN);
            m3.set(3, 0, 0.0);
            m3.set(3, 1, 0.0);
            m3.set(3, 2, 0.0);
            m3.set(3, 3, 1.0);
            Transform3D orientation = pfcBase.Transform3D_Create(m3);

            Point3D loc = Point3D.create();
            loc.set(0, sheetW / 2.0 / kSheet);          // paper centre in D
            loc.set(1, (sheetH / 2.0 - tSheet) / kSheet);
            loc.set(2, 0.0);

            // ---- closed-form framing (framing_drawing/v1): a probe view at
            //      scale 1 measures the model outline W x H; s_fit sizes the
            //      real view.  The probe's SetScale(1) spawns a scale note,
            //      but the note is view-bound and dies with probe.Delete.
            //      flake-recovery/v1 (safety net only, should not trigger
            //      since the sheet is regenerated up front): drop the sheet,
            //      add a fresh regenerated one, retry - replacing the
            //      ~2-minute process-level restart.  The SimpRep can no
            //      longer be poisoned by failures (drawing-owned,
            //      rep-drawing-owner/v1); gate G1 still verifies that the
            //      view inherited it, failing loudly with exit 77. ----
            View2D probe = null;
            for (int sheetTry = 1; probe == null; sheetTry++) {
                GeneralViewCreateInstructions probeIns = pfcView2D
                        .GeneralViewCreateInstructions_Create(
                                assembly, blankSheet, loc, orientation);
                probeIns.SetExploded(false);
                probeIns.SetScale(1.0);
                try {
                    probe = createView(drw, probeIns);
                } catch (Throwable flake) {
                    if (sheetTry >= 3) throw flake;
                    System.out.println("[DRW] flake-recovery/v1 (probe): "
                            + "poisoned sheet dropped, rebuild #" + sheetTry);
                    double[] sh = rebuildSheet(drw, blankSheet);
                    blankSheet = (int) sh[0];
                    sheetW = sh[1]; sheetH = sh[2];
                    kSheet = sh[3]; tSheet = sh[4];
                    loc.set(0, sheetW / 2.0 / kSheet);
                    loc.set(1, (sheetH / 2.0 - tSheet) / kSheet);
                    loc.set(2, 0.0);
                }
            }
            drw.RegenerateSheet(blankSheet);
            Outline3D po = probe.GetOutline();
            // union the exploded-anchor projections (probe scale 1) into the
            // fit box so the arrow tail/head never land off the sheet
            Matrix3D ptm = probe.GetTransform().GetMatrix();
            double[] tailD = apply(ptm, tailW);
            double[] headD = apply(ptm, headW);
            double minX = Math.min(po.get(0).get(0), Math.min(tailD[0], headD[0]));
            double maxX = Math.max(po.get(1).get(0), Math.max(tailD[0], headD[0]));
            double minY = Math.min(po.get(0).get(1), Math.min(tailD[1], headD[1]));
            double maxY = Math.max(po.get(1).get(1), Math.max(tailD[1], headD[1]));
            double cxm = (po.get(0).get(0) + po.get(1).get(0)) / 2.0;
            double cym = (po.get(0).get(1) + po.get(1).get(1)) / 2.0;
            probe.Delete(true);
            // paper-mm union incl. a small pad for the arrow endpoints
            // (the tail cross is gone; head fins extend back along the shaft)
            double pad = 2.0;
            double unionW = (maxX - minX) * kSheet + 2 * pad;
            double unionH = (maxY - minY) * kSheet + 2 * pad;
            double sFit = Math.min((sheetW - 2 * MARGIN) / unionW,
                    (sheetH - 2 * MARGIN) / unionH);
            System.out.println("[DRW] probe union outline " + fmt(unionW)
                    + "x" + fmt(unionH) + " mm -> s_fit="
                    + String.format(java.util.Locale.US, "%.4f", sFit));

            // ---- official sheet default scale (SheetOwner.SetSheetScale):
            //      the real view inherits it WITHOUT SetScale, so no
            //      read-only custom-scale note is spawned (P2 rule). ----

            // centre the UNION (model + anchors) on the paper: the view loc
            // places the MODEL centre, so shift it by the union/model centre
            // offset scaled to the view scale.
            // flake-recovery/v1: same poisoned-sheet rebuild as the probe;
            // the scale is (re)applied to the CURRENT sheet inside the loop
            // so a rebuilt sheet never loses s_fit.
            View2D view = null;
            for (int sheetTry = 1; view == null; sheetTry++) {
                drw.SetSheetScale(blankSheet, sFit, model);
                Point3D formalLoc = Point3D.create();
                formalLoc.set(0, sheetW / 2.0 / kSheet
                        - ((minX + maxX) / 2.0 - cxm) * sFit);
                formalLoc.set(1, (sheetH / 2.0 - tSheet) / kSheet
                        - ((minY + maxY) / 2.0 - cym) * sFit);
                formalLoc.set(2, 0.0);
                GeneralViewCreateInstructions ins = pfcView2D
                        .GeneralViewCreateInstructions_Create(
                                assembly, blankSheet, formalLoc, orientation);
                ins.SetExploded(false);   // complete pose; see class doc
                try {
                    view = createView(drw, ins);
                } catch (Throwable flake) {
                    if (sheetTry >= 3) throw flake;
                    System.out.println("[DRW] flake-recovery/v1 (view): "
                            + "poisoned sheet dropped, rebuild #" + sheetTry);
                    double[] sh = rebuildSheet(drw, blankSheet);
                    blankSheet = (int) sh[0];
                    sheetW = sh[1]; sheetH = sh[2];
                    kSheet = sh[3]; tSheet = sh[4];
                }
            }
            // fresh sheet must be current + regenerated before the view
            // geometry resolves (else GetOutline zero / GetTransform identity)
            drw.RegenerateSheet(blankSheet);
            double viewScale = view.GetScale();
            SimpRep viewRep = view.GetSimpRep();
            System.out.println("[DRW] view name=" + view.GetName()
                    + " scale=" + viewScale
                    + " view_rep=" + (viewRep != null ? viewRep.GetName() : "null"));

            // ---- rep was activated before view creation (see above); the
            //      view already inherits it - nothing to switch here. ----

            // ---- explode-reassert/v1 (Renderer parity): regeneration may
            //      drop the temporary DynamicPositioning transforms;
            //      re-apply and regenerate so the view redraws at the
            //      exploded pose. ----
            Renderer.reassertExplosion(assembly, explosionRecs, "simp_rep");
            drw.RegenerateSheet(blankSheet);

            Outline3D vo = view.GetOutline();
            double[] outline = new double[]{
                    vo.get(0).get(0), vo.get(0).get(1),
                    vo.get(1).get(0), vo.get(1).get(1)};
            System.out.println("[DRW] view outline paper mm=("
                    + fmt(outline[0] * kSheet) + "," + fmt(outline[1] * kSheet + tSheet)
                    + ")-(" + fmt(outline[2] * kSheet) + ","
                    + fmt(outline[3] * kSheet + tSheet) + ")");

            // ---- closed-form projection: View2D.GetTransform (row vector) ----
            Matrix3D vmm = view.GetTransform().GetMatrix();
            double[] tail2 = apply(vmm, tailW);
            double[] head2 = apply(vmm, headW);
            System.out.println("[DRW] tail paper mm=(" + fmt(tail2[0] * kSheet)
                    + "," + fmt(tail2[1] * kSheet + tSheet) + ") head paper mm=("
                    + fmt(head2[0] * kSheet) + "," + fmt(head2[1] * kSheet + tSheet)
                    + ")");

            // ---- built-in arrow: shaft + two head fins (tail cross
            //      removed on user request) ----
            //      (constants are paper-mm; the D-system needs /k)
            double shaftW = SHAFT_W / kSheet, headLen = HEAD_LEN / kSheet,
                    headWd = HEAD_W / kSheet;
            drawLine(drw, view, tail2, head2, shaftW);
            double vx = head2[0] - tail2[0], vy = head2[1] - tail2[1];
            double len = Math.hypot(vx, vy);
            double ux = vx / len, uy = vy / len;      // shaft unit
            double px = -uy, py = ux;                  // perpendicular
            drawLine(drw, view, new double[]{head2[0] - headLen * ux + headWd * px,
                    head2[1] - headLen * uy + headWd * py}, head2, shaftW);
            drawLine(drw, view, new double[]{head2[0] - headLen * ux - headWd * px,
                    head2[1] - headLen * uy - headWd * py}, head2, shaftW);
            drw.RegenerateSheet(blankSheet);
            // drop the format sheet; only the clean blank sheet remains
            drw.DeleteSheet(1);
            System.out.println("[DRW] format sheet dropped, sheets="
                    + drw.GetNumberOfSheets());

            // ---- view-attached scale note (the garbled "±ÈÁý 0,500") is a
            //      drawing-layer DETAIL_NOTE spawned by view regeneration;
            //      unlike the format-owned template notes it IS enumerable
            //      and deletable here. ----
            int notesRemoved = 0;
            try {
                com.ptc.pfc.pfcDetail.DetailItems notes = drw.ListDetailItems(
                        com.ptc.pfc.pfcDetail.DetailType.DETAIL_NOTE, null);
                if (notes != null) {
                    for (int i = notes.getarraysize() - 1; i >= 0; i--) {
                        try {
                            notes.get(i).Delete();
                            notesRemoved++;
                        } catch (Throwable deleteFailure) {
                            // the view scale note is read-only: hide it
                            // instead (SetDisplayed is the official switch)
                            try {
                                ((com.ptc.pfc.pfcDetail.DetailNoteItem)
                                        notes.get(i)).SetDisplayed(false);
                                notesRemoved++;
                            } catch (Throwable hideFailure) {
                                System.out.println("[DRW] note hide skipped: "
                                        + hideFailure);
                            }
                        }
                    }
                }
            } catch (Throwable noteListFailure) {
                System.out.println("[DRW] note listing failed: "
                        + noteListFailure);
            }
            System.out.println("[DRW] view notes removed/hidden: " + notesRemoved);

            // ---- Save BEFORE PDF export (unsaved drawing aborts
            //      silently) ----
            drw.Save();
            String pdfFile = outDir + "\\" + stepId + ".pdf";
            drwWin = session.GetModelWindow(drw);
            if (drwWin != null) {
                drwWin.Activate();
                drwWin.Repaint();
            }
            com.ptc.pfc.pfcExport.PDFExportInstructions pdfIns =
                    com.ptc.pfc.pfcExport.pfcExport.PDFExportInstructions_Create();
            pdfIns.SetFilePath(pdfFile);
            com.ptc.pfc.pfcExport.PDFOptions pdfOpts =
                    com.ptc.pfc.pfcExport.PDFOptions.create();
            com.ptc.pfc.pfcExport.PDFOption noViewer =
                    com.ptc.pfc.pfcExport.pfcExport.PDFOption_Create();
            noViewer.SetOptionType(com.ptc.pfc.pfcExport.PDFOptionType
                    .PDFOPT_LAUNCH_VIEWER);
            noViewer.SetOptionValue(com.ptc.pfc.pfcArgument.pfcArgument
                    .CreateBoolArgValue(false));
            pdfOpts.append(noViewer);
            com.ptc.pfc.pfcExport.PDFOption dpiOpt =
                    com.ptc.pfc.pfcExport.pfcExport.PDFOption_Create();
            dpiOpt.SetOptionType(com.ptc.pfc.pfcExport.PDFOptionType
                    .PDFOPT_RASTER_DPI);
            dpiOpt.SetOptionValue(com.ptc.pfc.pfcArgument.pfcArgument
                    .CreateIntArgValue(100));
            pdfOpts.append(dpiOpt);
            pdfIns.SetOptions(pdfOpts);
            drw.Export(pdfFile, pdfIns);
            java.io.File pdfCheck = new java.io.File(pdfFile);
            System.out.println("[DRW] exported " + pdfFile
                    + " exists=" + pdfCheck.exists()
                    + " size=" + pdfCheck.length());
            if (!pdfCheck.exists()) {
                throw new IllegalStateException("[DRW] pdf missing after export");
            }

            // ---- render.json v2 (paper-mm truths for the review branch) ----
            double[] outlineP = {outline[0] * kSheet, outline[1] * kSheet + tSheet,
                    outline[2] * kSheet, outline[3] * kSheet + tSheet};
            double[] tailP = {tail2[0] * kSheet, tail2[1] * kSheet + tSheet};
            double[] headP = {head2[0] * kSheet, head2[1] * kSheet + tSheet};

            // ---- stage gates (pipeline-stability/v1): deterministic
            //      per-step self-asserts, persisted in meta.gates.  Any
            //      FAIL exits 77 so the orchestrator aborts WITHOUT the
            //      blind process retry - a gate failure is deterministic,
            //      retrying burns minutes and hides the failing stage. ----
            boolean g1;
            String viewRepActual = null;
            try {
                SimpRep vr = view.GetSimpRep();
                viewRepActual = vr != null ? vr.GetName() : null;
                // rep-inheritance gate v2: the view must carry EXACTLY the
                // rep activated for this attempt (Creo upper-cases names);
                // a Master Rep here means visibility masking silently lost.
                g1 = (visibleList == null || visibleList.isEmpty())
                        || (viewRepActual != null && viewRepName != null
                            && viewRepActual.equalsIgnoreCase(viewRepName));
            } catch (Throwable simpRepCheck) {
                g1 = false;
            }
            double gateTol = 1.0;
            boolean g2 = outlineP[0] >= MARGIN - gateTol
                    && outlineP[1] >= MARGIN - gateTol
                    && outlineP[2] <= sheetW - MARGIN + gateTol
                    && outlineP[3] <= sheetH - MARGIN + gateTol;
            boolean g3 = tailP[0] >= gateTol && tailP[0] <= sheetW - gateTol
                    && tailP[1] >= gateTol && tailP[1] <= sheetH - gateTol
                    && headP[0] >= gateTol && headP[0] <= sheetW - gateTol
                    && headP[1] >= gateTol && headP[1] <= sheetH - gateTol;
            double arrowLen = Math.hypot(headP[0] - tailP[0],
                    headP[1] - tailP[1]);
            boolean g4 = arrowLen >= 2.0;   // explosion survives into view
            boolean g5 = pdfCheck.exists() && pdfCheck.length() > 0;
            boolean gatesPass = g1 && g2 && g3 && g4 && g5;
            System.out.println("[DRW] gates G1_rep=" + g1
                    + " G2_view_in_sheet=" + g2 + " G3_arrows_in_sheet=" + g3
                    + " G4_explode_len=" + fmt(arrowLen) + "mm:" + g4
                    + " G5_pdf=" + g5 + " -> " + (gatesPass ? "PASS" : "FAIL"));

            writeMeta(Paths.get(outDir, stepId + ".render.json").toString(),
                    stepId, asmFile, actualHash, cameraId, viewScale,
                    sheetW, sheetH, outlineP, tailP, headP, vmm, visibleList,
                    viewRepName, viewRepActual,
                    g1, g2, g3, arrowLen, g5, gatesPass);
            if (!gatesPass) {
                System.out.println("[DRW] GATE FAILED - step " + stepId
                        + " violates the fixed-chain contract (see meta.gates)");
                System.exit(77);
            }
            System.out.println("[DRW] done");
            System.exit(0);
        } finally {
            if (conn != null) {
                try { conn.End(); } catch (Throwable t) { }
            }
        }
    }

    /** Codepoint-safe dump of a model's active SimpRep name (the
     *  console codepage garbles non-ASCII names like the master rep). */
    private static String activeRepDebug(com.ptc.pfc.pfcSolid.Solid model) {
        try {
            SimpRep active = model.GetActiveSimpRep();
            String name = active != null ? active.GetName() : "null";
            StringBuilder cp = new StringBuilder();
            for (char ch : name.toCharArray())
                cp.append(String.format("%04x ", (int) ch));
            return name + " codepoints=" + cp;
        } catch (Throwable t) {
            return "<err " + t + ">";
        }
    }

    /** rep-drawing-owner/v1: build the visibility SimpRep on the assembly
     *  (default EXCLUDE + visible INCLUDE); the caller registers and
     *  activates it ON THE DRAWING (Model2D.AddSimplifiedRep), which is
     *  the only rep context the drawing views inherit. */
    private static SimpRep createVisibilityRep(Assembly assembly,
            List<Object> visibleList, int stepIndex)
            throws Exception {
        if (visibleList == null || visibleList.isEmpty())
            return null;
        String repName = "DRWCLEAN_" + stepIndex + "_V1";
        CreateNewSimpRepInstructions repIns =
                pfcSimpRep.CreateNewSimpRepInstructions_Create(repName);
        repIns.SetIsTemporary(false);
        repIns.SetDefaultAction(SimpRepActionType.SIMPREP_EXCLUDE);
        SimpRepItems repItems = SimpRepItems.create();
        for (Object vo : visibleList) {
            SimpRepItem item = pfcSimpRep.SimpRepItem_Create(
                    pfcSimpRep.SimpRepCompItemPath_Create(
                            pathToIds((String) vo)));
            item.SetAction(pfcSimpRep.SimpRepInclude_Create());
            repItems.append(item);
        }
        repIns.SetItems(repItems);
        return assembly.CreateSimpRep(repIns);
    }

    /** CreateView with one in-process retry on the SAME sheet; a
     *  persistent failure is a poisoned sheet and is escalated to the
     *  caller's flake-recovery/v1 sheet rebuild (never a process
     *  restart any more). */
    private static View2D createView(Drawing drw,
            GeneralViewCreateInstructions ins) throws Exception {
        View2D view = null;
        for (int attempt = 1; attempt <= 2 && view == null; attempt++) {
            try {
                view = drw.CreateView(ins);
            } catch (Throwable createFailure) {
                System.out.println("[DRW] CreateView attempt " + attempt
                        + " failed: " + createFailure);
                if (attempt == 2)
                    throw new RuntimeException(
                            "CreateView failed after retry", createFailure);
                Thread.sleep(2000);
            }
        }
        return view;
    }

    /** flake-recovery/v1: drop the poisoned sheet and add a fresh one,
     *  returning {sheetNo, width, height, k, t} recalibrated.  The
     *  regenerate of the NEW sheet can itself throw on the first process
     *  (sheet-level quirk, harmless for the subsequent CreateView); it is
     *  tolerated here and logged. */
    private static double[] rebuildSheet(Drawing drw, int oldSheet)
            throws Exception {
        try { drw.DeleteSheet(oldSheet); } catch (Throwable ignored) { }
        int s = drw.AddSheet();
        drw.SetCurrentSheetNumber(s);
        try {
            drw.RegenerateSheet(s);
        } catch (Throwable regenQuirk) {
            System.out.println("[DRW] rebuild-sheet regenerate quirk ("
                    + regenQuirk + ") - continuing");
        }
        com.ptc.pfc.pfcSheet.SheetData d = drw.GetSheetData(s);
        Matrix3D m = drw.GetSheetTransform(s).GetMatrix();
        return new double[]{s, d.GetWidth(), d.GetHeight(),
                m.get(0, 0), m.get(3, 1)};
    }

    /** One DetailEntity line in sheet coordinates (drawing world units). */
    private static void drawLine(Drawing drw, View2D view, double[] a,
            double[] b, double width) throws Exception {
        Point3D p1 = Point3D.create();
        p1.set(0, a[0]); p1.set(1, a[1]); p1.set(2, 0.0);
        Point3D p2 = Point3D.create();
        p2.set(0, b[0]); p2.set(1, b[1]); p2.set(2, 0.0);
        CurveDescriptor line = pfcGeometry.LineDescriptor_Create(p1, p2);
        DetailEntityInstructions dei =
                pfcDetail.DetailEntityInstructions_Create(line, view);
        com.ptc.pfc.pfcBase.ColorRGB green =
                pfcBase.ColorRGB_Create(0.0, 0.75, 0.15);
        dei.SetColor(green);
        dei.SetWidth(width);
        drw.CreateDetailItem(dei);
    }

    /**
     * Row-vector convention per the official Pro/TOOLKIT guide
     * (user_guide/Transformations.html, 4x4_transform_matrix.gif):
     * p' = p * M, i.e. the translation lives in the LAST ROW and the
     * sheet-x/sheet-y results are the first two COLUMNS.
     */
    private static double[] apply(Matrix3D m, double[] w) throws Exception {
        double[] out = new double[2];
        out[0] = m.get(0, 0) * w[0] + m.get(1, 0) * w[1]
                + m.get(2, 0) * w[2] + m.get(3, 0);
        out[1] = m.get(0, 1) * w[0] + m.get(1, 1) * w[1]
                + m.get(2, 1) * w[2] + m.get(3, 1);
        return out;
    }

    private static void writeMeta(String path, String stepId, String asmFile,
            String hash, String cameraId, double scale, double sheetW,
            double sheetH, double[] outline, double[] tail, double[] head,
            Matrix3D vmm, List<Object> visibleList,
            String repExpected, String repActual,
            boolean g1, boolean g2, boolean g3, double arrowLen,
            boolean g5, boolean gatesPass) throws Exception {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"schema\": \"clean-run-render-meta/v2\",\n");
        sb.append("  \"step_id\": ").append(q(stepId)).append(",\n");
        sb.append("  \"assembly_file\": ").append(q(asmFile)).append(",\n");
        sb.append("  \"sha256\": ").append(q(hash)).append(",\n");
        sb.append("  \"camera\": ").append(q(cameraId)).append(",\n");
        sb.append(String.format(java.util.Locale.US,
                "  \"sheet_mm\": [%.1f,%.1f],%n", sheetW, sheetH));
        sb.append(String.format(java.util.Locale.US,
                "  \"view_scale\": %.6f,%n", scale));
        sb.append(String.format(java.util.Locale.US,
                "  \"view_outline_mm\": [%.2f,%.2f,%.2f,%.2f],%n",
                outline[0], outline[1], outline[2], outline[3]));
        sb.append("  \"view_transform\": ").append(matrixJson(vmm)).append(",\n");
        sb.append(String.format(java.util.Locale.US,
                "  \"tail_sheet_mm\": [%.3f,%.3f],%n", tail[0], tail[1]));
        sb.append(String.format(java.util.Locale.US,
                "  \"head_sheet_mm\": [%.3f,%.3f],%n", head[0], head[1]));
        sb.append("  \"arrow_mm\": {\"shaft_width\":").append(SHAFT_W)
                .append(",\"head_len\":").append(HEAD_LEN)
                .append(",\"head_w\":").append(HEAD_W).append("},\n");
        sb.append("  \"simp_rep_expected\": ")
                .append(repExpected == null ? "null" : q(repExpected))
                .append(",\n");
        sb.append("  \"simp_rep_view_actual\": ")
                .append(repActual == null ? "null" : q(repActual))
                .append(",\n");
        sb.append("  \"gates\": {\"G1_rep_inherited\":").append(g1)
                .append(",\"G2_view_in_sheet\":").append(g2)
                .append(",\"G3_arrows_in_sheet\":").append(g3)
                .append(String.format(java.util.Locale.US,
                        ",\"G4_explode_len_mm\":%.2f,\"G4_pass\":%b",
                        arrowLen, arrowLen >= 2.0))
                .append(",\"G5_pdf_nonempty\":").append(g5)
                .append(",\"pass\":").append(gatesPass).append("},\n");
        sb.append("  \"visible_paths\": [");
        if (visibleList != null) {
            for (int i = 0; i < visibleList.size(); i++) {
                if (i > 0) sb.append(",");
                sb.append(q((String) visibleList.get(i)));
            }
        }
        sb.append("],\n");
        sb.append("  \"dpi\": 100\n");
        sb.append("}\n");
        try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(
                Paths.get(path), StandardCharsets.UTF_8))) {
            w.print(sb);
        }
    }

    private static String matrixJson(Matrix3D m) throws Exception {
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

    private static String q(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    private static String fmt(double v) {
        return String.format(java.util.Locale.US, "%.2f", v);
    }

    private static double[] vec3(Object o) {
        List<Object> l = (List<Object>) o;
        return new double[]{((Number) l.get(0)).doubleValue(),
                ((Number) l.get(1)).doubleValue(),
                ((Number) l.get(2)).doubleValue()};
    }

    private static intseq pathToIds(String path) throws Exception {
        String[] parts = path.split("/");
        intseq ids = intseq.create();
        for (int i = 1; i < parts.length; i++)
            ids.append(Integer.parseInt(parts[i]));
        return ids;
    }

    private static String sha256(String file) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] h = md.digest(Files.readAllBytes(Paths.get(file)));
        StringBuilder sb = new StringBuilder();
        for (byte b : h) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}
