import com.ptc.cipjava.jxthrowable;
import com.ptc.cipjava.intseq;
import com.ptc.pfc.pfcAssembly.Assembly;
import com.ptc.pfc.pfcAssembly.ComponentPath;
import com.ptc.pfc.pfcAssembly.pfcAssembly;
import com.ptc.pfc.pfcComponentFeat.ComponentFeat;
import com.ptc.pfc.pfcFeature.FeatureType;
import com.ptc.pfc.pfcFeature.Features;
import com.ptc.pfc.pfcAsyncConnection.AsyncConnection;
import com.ptc.pfc.pfcAsyncConnection.pfcAsyncConnection;
import com.ptc.pfc.pfcBase.Matrix3D;
import com.ptc.pfc.pfcBase.Outline3D;
import com.ptc.pfc.pfcBase.Point3D;
import com.ptc.pfc.pfcBase.ScreenTransform;
import com.ptc.pfc.pfcBase.Transform3D;
import com.ptc.pfc.pfcBase.pfcBase;
import com.ptc.pfc.pfcLayer.DisplayStatus;
import com.ptc.pfc.pfcLayer.Layer;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.Models;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcModelItem.ModelItem;
import com.ptc.pfc.pfcModelItem.ModelItemType;
import com.ptc.pfc.pfcModelItem.ModelItemTypes;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcSolid.Solid;
import com.ptc.pfc.pfcSimpRep.CreateNewSimpRepInstructions;
import com.ptc.pfc.pfcSimpRep.SimpRep;
import com.ptc.pfc.pfcSimpRep.SimpRepActionType;
import com.ptc.pfc.pfcSimpRep.SimpRepCompItemPath;
import com.ptc.pfc.pfcSimpRep.SimpRepInstructions;
import com.ptc.pfc.pfcSimpRep.SimpRepItem;
import com.ptc.pfc.pfcSimpRep.SimpRepItems;
import com.ptc.pfc.pfcSimpRep.pfcSimpRep;
import com.ptc.pfc.pfcWindow.JPEGImageExportInstructions;
import com.ptc.pfc.pfcWindow.Window;
import com.ptc.pfc.pfcWindow.pfcWindow;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Clean-run formal renderer (written from scratch).
 *
 * Renders ONE planned step inside an isolated Creo session:
 *   hard gate  : assembly SHA-256 must match the manifest recorded at batch start
 *   visibility : SimpRep with default EXCLUDE + INCLUDE for the visible set
 *   explosion  : pfc ComponentPath.SetTransform under DynamicPositioning
 *                  (official async mechanism; wfc ExplodedState authoring
 *                  is synchronous-OTK only, see jlink-async-boundary/v1)
 *   camera     : one of the two calibrated fixed matrices (world -> view)
 *   framing    : official Refit + measured screen calibration,
 *                  closed-form pan/zoom solve (no probe ladder)
 *   output     : 1600x1600 JPEG + render metadata JSON for arrow projection
 *
 * args: [0] plan.json  [1] step index  [2] output dir  [3] parametric command
 */
public class Renderer {

    /** Creo-native non-part feature classes; hidden at open so only solid
     *  part geometry renders (render-rules: Appearance). */
    private static final FeatureType[] AUXILIARY_FEATURE_TYPES = new FeatureType[]{
            FeatureType.FEATTYPE_WELDING_ROD, FeatureType.FEATTYPE_WELD_FILLET,
            FeatureType.FEATTYPE_WELD_GROOVE, FeatureType.FEATTYPE_WELD_PLUG_SLOT,
            FeatureType.FEATTYPE_WELD_SPOT, FeatureType.FEATTYPE_WELD_PROCESS,
            FeatureType.FEATTYPE_WELD_NOTCH, FeatureType.FEATTYPE_ASSY_WELD_NOTCH,
            FeatureType.FEATTYPE_HULL_WELD_NOTCH, FeatureType.FEATTYPE_WELD_COMBINE,
            FeatureType.FEATTYPE_COSMETIC, FeatureType.FEATTYPE_CABLE_COSMETIC,
            FeatureType.FEATTYPE_ANNOTATION, FeatureType.FEATTYPE_DATUM_PLANE,
            FeatureType.FEATTYPE_DATUM_AXIS, FeatureType.FEATTYPE_DATUM_POINT,
            FeatureType.FEATTYPE_DATUM_SURFACE, FeatureType.FEATTYPE_DATUM_QUILT,
            FeatureType.FEATTYPE_CURVE, FeatureType.FEATTYPE_ZONE
    };

    private static final int IMAGE_SIZE = 1600;
    // Export instructions take INCHES; 16.0 in * 100 dpi = 1600 px.
    private static final double EXPORT_INCHES = 16.0;

    /** Per-model auxiliary layers created by the first blanking pass;
     *  re-asserted after explosion transforms (one JVM = one step). */
    private static final Map<String, Layer> auxiliaryLayers = new HashMap<>();

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("usage: Renderer <plan.json> <stepIndex> <outDir> [parametricCmd]");
            System.exit(2);
        }
        String planPath = args[0];
        int stepIndex = Integer.parseInt(args[1]);
        String outDir = args[2];
        String proCmd = args.length > 3 ? args[3] : "";

        Map<String, Object> plan = (Map<String, Object>) MiniJson.parse(
                new String(Files.readAllBytes(Paths.get(planPath)), StandardCharsets.UTF_8));
        List<Object> steps = (List<Object>) plan.get("steps");
        Map<String, Object> step = (Map<String, Object>) steps.get(stepIndex);
        String stepId = (String) step.get("step_id");
        List<Object> movingList = (List<Object>) step.get("moving");
        List<Object> visibleList = (List<Object>) step.get("visible_paths");
        String cameraId = (String) step.get("camera");

        // ---- framing/v10 action focus: the action set is the moving
        //      closure (roots + rigid descendants); cumulative world
        //      translation per VISIBLE path mirrors overlay
        //      cumulative_translation (sum of every moving entry whose
        //      path is a prefix of the occurrence).
        java.util.Set<String> movingPaths = new java.util.HashSet<>();
        Map<String, double[]> cumTr = new HashMap<>();
        for (Object mo : movingList) {
            movingPaths.add((String) ((Map<String, Object>) mo).get("path"));
        }
        for (Object vo : visibleList) {
            String p = (String) vo;
            double[] t = new double[3];
            for (Object mo2 : movingList) {
                Map<String, Object> m2 = (Map<String, Object>) mo2;
                String p2 = (String) m2.get("path");
                if (p.equals(p2) || p.startsWith(p2 + "/")) {
                    List<Object> tr2 = (List<Object>) m2.get("translation");
                    for (int k = 0; k < 3; k++) {
                        t[k] += ((Number) tr2.get(k)).doubleValue();
                    }
                }
            }
            cumTr.put(p, t);
        }

        // manifest with the calibrated cameras and the batch-start hash
        Path manifestPath = Paths.get(planPath).resolveSibling("manifest.json");
        Map<String, Object> manifest = (Map<String, Object>) MiniJson.parse(
                new String(Files.readAllBytes(manifestPath), StandardCharsets.UTF_8));
        List<Object> cameraRows = (List<Object>)
                ((Map<String, Object>) manifest.get("cameras")).get(cameraId);
        double[][] cam = new double[4][4];
        for (int r = 0; r < 4; r++)
            for (int c = 0; c < 4; c++)
                cam[r][c] = ((Number) ((List<Object>) cameraRows.get(r)).get(c)).doubleValue();
        String expectedHash = (String) manifest.get("sha256_at_batch_start");

        System.loadLibrary("pfcasyncmt");
        AsyncConnection conn = null;
        try {
            System.out.println("[RENDER] step " + stepId + " starting Creo ...");
            conn = pfcAsyncConnection.AsyncConnection_Start(proCmd, null);
            Session session = conn.GetSession();

            String asmName = ((String) plan.get("assembly")).replaceFirst("\\.\\d+$", "");
            String asmFile = (String) plan.get("assembly");

            // ---- hard gate: SHA-256 of the actual assembly file ----
            String actualHash = sha256(Paths.get(asmFile));
            if (!actualHash.equalsIgnoreCase(expectedHash)) {
                throw new IllegalStateException("HARD GATE FAILED: assembly hash mismatch "
                        + actualHash + " != " + expectedHash);
            }
            System.out.println("[RENDER] hash gate passed: " + actualHash.substring(0, 16) + "...");

            ModelDescriptor descr = pfcModel.ModelDescriptor_CreateFromFileName(asmName);
            Window opened = session.OpenFile(descr);
            if (opened != null) {
                opened.Activate();
            }
            Model model = session.GetActiveModel();
            if (model == null) {
                Thread.sleep(2000);
                model = session.GetActiveModel();
            }
            if (model == null) {
                model = session.GetModelFromDescr(descr);
            }
            if (model == null) {
                throw new IllegalStateException("active model is null after OpenFile");
            }
            Assembly root = (Assembly) model;
            System.out.println("[RENDER] opened " + model.GetFileName()
                    + " version=" + model.GetVersion());

            // ---- hide every non-part feature class at open ----
            int hiddenAux = hideAuxiliaryFeatures(session);
            System.out.println("[RENDER] native_auxiliary_features_hidden="
                    + hiddenAux);

            // ---- framing/v10: the focus measurement rep INCLUDEs only
            //      the moving closure, so ONE activation shows exactly
            //      the action ink for the measurement export.  SimpRep
            //      is the official async member-level display filter
            //      (jlink-async-boundary/v1); layer AddItem rejects
            //      nested occurrences (observed XToolkitBadInputs on
            //      40/83 members), so it cannot hide the non-action
            //      set.  The full visible set is restored by
            //      re-activating repIns below.
            //      rep-capture/v1: the instructions object is created
            //      AFTER the explosion transforms below, because a rep
            //      created before them renders its members at the
            //      pre-explosion pose (observed: focus rep showed the
            //      complete pose while repIns showed the exploded one,
            //      splitting the affine calibration). ----
            Map<String, ModelItem> occItems = new HashMap<>();
            for (Object vo : visibleList) {
                String vp = (String) vo;
                try {
                    ResolvedComponent rc = resolveComponent(
                            session, root, pathToIds(vp));
                    occItems.put(vp, rc.feature);
                } catch (Throwable resolveFailure) {
                    // the outline audit skips this path as well
                }
            }

                        // ---- explosion FIRST: ComponentPath.GetTransform is unreliable
            //      for nested paths once a temporary SimpRep is active.
            //      jlink-async-boundary/v1: the sanctioned explode-state
            //      authoring API (wfcAssembly.CreateExplodedState +
            //      ExplodedAnimationMoveInstructions) is wfc/OTK-only; in
            //      asynchronous J-Link every wfc server call dies with
            //      CIPRemoteApp.comm==null (observed NPE) because the
            //      synchronous RPC channel is never initialised outside a
            //      protk registry load.  The official async mechanism is
            //      therefore pfc's own ComponentPath.SetTransform under
            //      DynamicPositioning (OTK UG "Transforming Coordinates of
            //      an Assembly Member"), with a numeric read-back audit. ----
            Assembly assembly = root;
            if (!assembly.GetDynamicPositioning()) {
                assembly.SetDynamicPositioning(true);
            }
            if (!assembly.GetDynamicPositioning()) {
                throw new IllegalStateException(
                        "Creo DynamicPositioning was not enabled");
            }
            int explodedOk = 0;
            List<ExplosionRecord> explosionRecs = new ArrayList<>();
            for (int mi = 0; mi < movingList.size(); mi++) {
                Map<String, Object> mv = (Map<String, Object>) movingList.get(mi);
                String mPath = (String) mv.get("path");
                List<Object> tr = (List<Object>) mv.get("translation");
                double dx = ((Number) tr.get(0)).doubleValue();
                double dy = ((Number) tr.get(1)).doubleValue();
                double dz = ((Number) tr.get(2)).doubleValue();
                if (dx == 0.0 && dy == 0.0 && dz == 0.0) {
                    System.out.println("[RENDER] explode skip " + mPath
                            + " (zero translation)");
                    continue;
                }
                explosionRecs.add(translateOccurrence(session, assembly,
                        pathToIds(mPath), dx, dy, dz));
                explodedOk++;
            }
            System.out.println("[RENDER] exploded occurrences=" + explodedOk
                    + "/" + movingList.size());

            // ---- rep-capture/v1: focus rep instructions created here,
            //      after the explosion, so both reps render the exploded
            //      poses (see the framing/v10 note above). ----
            CreateNewSimpRepInstructions focusIns = null;
            try {
                List<String> actionPaths = new ArrayList<>();
                for (Object vo : visibleList) {
                    String vp = (String) vo;
                    if (isAction(vp, movingPaths)) actionPaths.add(vp);
                }
                if (!actionPaths.isEmpty()
                        && actionPaths.size() < visibleList.size()) {
                    focusIns = pfcSimpRep
                            .CreateNewSimpRepInstructions_Create(
                                    "CLEAN_FOCUS_" + stepIndex);
                    focusIns.SetIsTemporary(true);
                    focusIns.SetDefaultAction(
                            SimpRepActionType.SIMPREP_EXCLUDE);
                    SimpRepItems focusItems = SimpRepItems.create();
                    for (String ap : actionPaths) {
                        SimpRepItem item = pfcSimpRep.SimpRepItem_Create(
                                pfcSimpRep.SimpRepCompItemPath_Create(
                                        pathToIds(ap)));
                        item.SetAction(pfcSimpRep.SimpRepInclude_Create());
                        focusItems.append(item);
                    }
                    focusIns.SetItems(focusItems);
                    System.out.println("[RENDER] focus rep prepared="
                            + actionPaths.size());
                }
            } catch (Throwable repFailure) {
                System.out.println("[RENDER] focus rep prepare failed: "
                        + repFailure);
                focusIns = null;   // whole-machine framing fallback
            }

            // ---- exploded world poses of the visible occurrences, taken
            //      BEFORE the SimpRep activates: ComponentPath.GetTransform
            //      is unreliable for nested paths once a SimpRep is active.
            //      Feeds the official Solid.EvalOutline framing below. ----
            Map<String, double[][]> worldPoses = new HashMap<>();
            for (Object vo : visibleList) {
                String vp = (String) vo;
                try {
                    ComponentPath cp = pfcAssembly.CreateComponentPath(
                            assembly, pathToIds(vp));
                    worldPoses.put(vp, matrixOf(cp.GetTransform(true)));
                } catch (Throwable poseFailure) {
                    System.out.println("[RENDER] framing pose_skipped=" + vp);
                }
            }

            // ---- visibility: SimpRep default EXCLUDE + visible INCLUDE ----
            String repName = "CLEAN_" + stepIndex;
            CreateNewSimpRepInstructions repIns =
                    pfcSimpRep.CreateNewSimpRepInstructions_Create(repName);
            repIns.SetIsTemporary(true);
            repIns.SetDefaultAction(SimpRepActionType.SIMPREP_EXCLUDE);
            SimpRepItems repItems = SimpRepItems.create();
            for (Object vo : visibleList) {
                String vp = (String) vo;
                SimpRepItem item = pfcSimpRep.SimpRepItem_Create(
                        pfcSimpRep.SimpRepCompItemPath_Create(pathToIds(vp)));
                item.SetAction(pfcSimpRep.SimpRepInclude_Create());
                repItems.append(item);
            }
            repIns.SetItems(repItems);
            assembly.ActivateSimpRep(assembly.CreateSimpRep(repIns));
            System.out.println("[RENDER] simp_rep=" + repName
                    + " includes=" + repItems.getarraysize());

            // ---- re-assert the auxiliary blank AFTER every transform and
            //      the SimpRep switch: ComponentPath.SetTransform revives
            //      blanked datum-curve display at the pre-transform pose
            //      (observed on hose trajectory curves), so the layer blank
            //      must be re-applied once the final poses are in place. ----
            for (Layer layer : auxiliaryLayers.values()) {
                layer.SetStatus(DisplayStatus.LAYER_BLANK);
            }
            System.out.println("[RENDER] auxiliary_reblank_after_explode="
                    + auxiliaryLayers.size());
            // ---- explode-reassert/v1: a SimpRep switch's regeneration can
            //      drop the temporary DynamicPositioning transforms
            //      (observed race: final image showed the assembled pose
            //      while the focus audit still had the exploded ink), so
            //      the recorded absolute exploded poses are re-applied
            //      after EVERY rep switch; absolute SetTransform is
            //      idempotent. ----
            reassertExplosion(assembly, explosionRecs, "after_rep_switch");

            // ---- strict camera gate: only the two calibrated views ----
            if (!"fixed_123".equals(cameraId) && !"fixed_456".equals(cameraId)) {
                throw new IllegalStateException("illegal camera id: " + cameraId
                        + " (only fixed_123 / fixed_456 are allowed)");
            }

            // ---- framing policy (zoom_policy/v2 from the portability doc)
            //      tighter margins + higher occupancy than v1: the window
            //      letterbox band already limits the usable height, so the
            //      frame budget must not be spent on empty margin. ----
            final double occupancy = 0.98;
            final int mL = 48, mR = 48, mT = 48, mB = 64, mArrow = 32;
            final double zMin = 0.5, zMax = 8.0, tolPx = 12.0;
            double availW = IMAGE_SIZE - mL - mR - 2.0 * mArrow;
            double availH = IMAGE_SIZE - mT - mB - 2.0 * mArrow;
            double targCx = (mL + IMAGE_SIZE - mR) / 2.0;
            double targCy = (mT + IMAGE_SIZE - mB) / 2.0;

            Window win = session.GetCurrentWindow();
            try {
                // collapse the browser pane; not allowed in some sessions
                win.SetBrowserSize(0.0);
            } catch (Throwable ignored) {
            }

            // ---- camera step 1: rotation only (stored rows / unit scale);
            //      Creo stores rows as given but applies the TRANSPOSE ----
            double colN = Math.sqrt(cam[0][0] * cam[0][0] + cam[1][0] * cam[1][0]
                    + cam[2][0] * cam[2][0]);
            Matrix3D m3 = Matrix3D.create();
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++)
                    m3.set(r, c, cam[r][c] / colN);
            m3.set(3, 3, 1.0);
            model.SetCurrentViewTransform(pfcBase.Transform3D_Create(m3));
            // official screen transform: identity (zero pan, zoom 1.0); the
            // view transform alone carries rotation / zoom / centring
            win.SetScreenTransform(pfcBase.ScreenTransform_Create(0.0, 0.0, 1.0));
            win.Refresh();
            Thread.sleep(800);

            // ---- camera step 2: read back what Creo actually stored ----
            Transform3D d = model.GetCurrentViewTransform();
            Matrix3D dm = d.GetMatrix();
            double s2 = Math.sqrt(dm.get(0, 1) * dm.get(0, 1)
                    + dm.get(1, 1) * dm.get(1, 1) + dm.get(2, 1) * dm.get(2, 1));
            double[][] R2 = new double[3][3];   // rows = right/up/toward
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++)
                    R2[r][c] = dm.get(c, r) / s2;      // stored rows transposed
            double[] t2 = {dm.get(3, 0), dm.get(3, 1), dm.get(3, 2)};

            // ---- view-plane outline audit: per the PTC J-Link reference
            //      Solid.EvalOutline(Trf, null) returns the solid's 3-D
            //      outline in the given coordinate system.  The visible
            //      set's view-space bbox is recorded in the render meta
            //      (outline_mm / outline_centre) as an independent audit
            //      of the measured screen framing below.  Each visible
            //      solid's outline is taken in world coordinates and
            //      rotated by the locked camera; sub-assembly entries are
            //      skipped because their leaf is an Assembly and every
            //      descendant part is listed itself. ----
            double vxMin = 1e18, vyMin = 1e18, vxMax = -1e18, vyMax = -1e18;
            int outlines = 0;
            // framing/v10: same pass accumulates the ACTION bbox (moving
            // closure, exploded UNION complete state - the exact span the
            // arrows cover).  The ComponentFeat cache comes from the
            // pre-SimpRep resolve above.
            double axMin = 1e18, ayMin = 1e18, axMax = -1e18, ayMax = -1e18;
            int actionOutlines = 0;
            for (Object vo : visibleList) {
                String vp = (String) vo;
                double[][] W = worldPoses.get(vp);
                if (W == null) continue;
                Solid leaf;
                try {
                    ModelItem mi = occItems.get(vp);
                    if (!(mi instanceof ComponentFeat)) continue;
                    Model lm = session.RetrieveModel(
                            ((ComponentFeat) mi).GetModelDescr());
                    if (!(lm instanceof Solid)) continue;
                    leaf = (Solid) lm;
                } catch (Throwable leafUnavailable) {
                    continue;
                }
                try {
                    Outline3D ol = leaf.EvalOutline(
                            pfcBase.Transform3D_Create(toMatrix(W)), null);
                    Point3D a = ol.get(0), b = ol.get(1);
                    boolean act = isAction(vp, movingPaths);
                    double[] tCum = cumTr.get(vp);
                    double dvx = 0.0, dvy = 0.0;
                    if (act && tCum != null) {
                        dvx = R2[0][0] * tCum[0] + R2[0][1] * tCum[1]
                                + R2[0][2] * tCum[2];
                        dvy = R2[1][0] * tCum[0] + R2[1][1] * tCum[1]
                                + R2[1][2] * tCum[2];
                    }
                    for (int cx = 0; cx < 2; cx++)
                        for (int cy = 0; cy < 2; cy++)
                            for (int cz = 0; cz < 2; cz++) {
                                double wx = cx == 0 ? a.get(0) : b.get(0);
                                double wy = cy == 0 ? a.get(1) : b.get(1);
                                double wz = cz == 0 ? a.get(2) : b.get(2);
                                double rx = R2[0][0] * wx + R2[0][1] * wy
                                        + R2[0][2] * wz;
                                double ry = R2[1][0] * wx + R2[1][1] * wy
                                        + R2[1][2] * wz;
                                if (rx < vxMin) vxMin = rx;
                                if (rx > vxMax) vxMax = rx;
                                if (ry < vyMin) vyMin = ry;
                                if (ry > vyMax) vyMax = ry;
                                if (act) {
                                    // exploded pose ... and the COMPLETE
                                    // pose (cumulative displacement moved
                                    // back) both bound the action area.
                                    double rx2 = rx - dvx, ry2 = ry - dvy;
                                    if (rx < axMin) axMin = rx;
                                    if (rx2 < axMin) axMin = rx2;
                                    if (rx > axMax) axMax = rx;
                                    if (rx2 > axMax) axMax = rx2;
                                    if (ry < ayMin) ayMin = ry;
                                    if (ry2 < ayMin) ayMin = ry2;
                                    if (ry > ayMax) ayMax = ry;
                                    if (ry2 > ayMax) ayMax = ry2;
                                }
                            }
                    outlines++;
                    if (act) actionOutlines++;
                } catch (Throwable outlineFailure) {
                    System.out.println("[RENDER] outline_skipped=" + vp);
                }
            }
            if (outlines == 0) {
                throw new IllegalStateException(
                        "framing: no visible solid produced an outline");
            }
            double wMm = vxMax - vxMin, hMm = vyMax - vyMin;
            double mX = (vxMin + vxMax) / 2.0, mY = (vyMin + vyMax) / 2.0;
            System.out.println("[RENDER] outline view_box="
                    + String.format(java.util.Locale.US,
                            "%.1fx%.1f mm centre=(%.2f,%.2f) solids=%d",
                            wMm, hMm, mX, mY, outlines));

            // ---- framing official/v9: ONE fixed measure -> calibrate ->
            //      solve sequence (migrated from the proven AI_assembly
            //      renderer; no probe ladder, no iteration):
            //        1 Refit    "~ Command `ProCmdViewRefit`" through the
            //                   official Session.RunMacro mapkey API fits
            //                   AND centres the visible set in one step;
            //                   RunMacro -> Repaint -> FlushCurrentWindow
            //                   is the official async-macro protocol.
            //        2 measure  one export at the Refit state: ink extent
            //                   sizes the zoom multiplier, ink centre c1.
            //        3 pivot    one mild zoom export locates the screen
            //                   zoom pivot A of c(z) = A + z*(c1 - A).
            //        4 gradient four fixed small-pan exports (z=1, z2)
            //                   measure the pan->pixel gradient; official
            //                   pan semantics are zoom-independent, so the
            //                   clean z=1 gradient serves the target zoom.
            //        5 band cap |k1y| is one viewport height in export
            //                   pixels (letterboxed window); f is capped
            //                   so ink + arrow margin fits the band.
            //        6 solve    ONE closed-form pan from the composed
            //                   model c(z,pan) = A + z*(c1-A) + K*pan.
            //        7 verify   one park export + one closed-form trim.
            //        8 final    epsilon-protocol export; up to two
            //                   closed-form residual corrections. ----
            session.RunMacro("~ Command `ProCmdViewRefit`");
            win.Repaint();
            session.FlushCurrentWindow();
            Thread.sleep(800);

            // ---- step 2: ONE measurement export at the Refit state ----
            int[] bb1 = inkBox(exportCurrent(win,
                    "fr_measure_refit.jpg", 900));
            if (bb1 == null) {
                throw new IllegalStateException(
                        "framing: post-refit measurement export has no ink");
            }
            if (bb1[0] < 4 || bb1[2] < 4 || bb1[1] > IMAGE_SIZE - 4
                    || bb1[3] > IMAGE_SIZE - 4) {
                throw new IllegalStateException(
                        "framing: refit view clipped by window edge");
            }
            double w1 = bb1[3] - bb1[2], h1 = bb1[1] - bb1[0];
            double c0x = (bb1[2] + bb1[3]) / 2.0;
            double c0y = (bb1[0] + bb1[1]) / 2.0;

            // ---- step 2b: analytic zoom multiplier, policy target box ----
            double targetW = occupancy * availW;
            double targetH = occupancy * availH;
            double f = Math.max(zMin, Math.min(zMax, Math.min(
                    targetW / w1, targetH / h1)));

            // ---- step 3: one MILD zoom locates the screen zoom pivot
            //      c(z) = A + z*(c1 - A); the ink stays on screen
            //      because z2 is mild. ----
            final double z2 = 1.25;
            int[] bb2 = inkBox(exportState(win, 0.0, 0.0, z2,
                    "fr_measure_z2.jpg", 900));
            if (bb2 == null) {
                throw new IllegalStateException(
                        "framing: z2 measurement export has no ink");
            }
            double c2x = (bb2[2] + bb2[3]) / 2.0;
            double c2y = (bb2[0] + bb2[1]) / 2.0;
            double axC = (c2x - z2 * c0x) / (1.0 - z2);
            double ayC = (c2y - z2 * c0y) / (1.0 - z2);

            // ---- step 4: fixed four-frame pan-gradient calibration at
            //      z=1 and z2 (both low enough that the full ink stays
            //      visible; no high-zoom probe is ever used). ----
            final double dPan = 0.03;
            int[] bbX1 = inkBox(exportState(win, dPan, 0.0, 1.0,
                    "fr_cal_k1x.jpg", 900));
            int[] bbY1 = inkBox(exportState(win, 0.0, dPan, 1.0,
                    "fr_cal_k1y.jpg", 900));
            int[] bbX2 = inkBox(exportState(win, dPan, 0.0, z2,
                    "fr_cal_k2x.jpg", 900));
            int[] bbY2 = inkBox(exportState(win, 0.0, dPan, z2,
                    "fr_cal_k2y.jpg", 900));
            if (bbX1 == null || bbY1 == null || bbX2 == null
                    || bbY2 == null) {
                throw new IllegalStateException(
                        "framing: gradient calibration export has no ink");
            }
            double[] cx1 = unbiasedCentre(bbX1, w1, h1, w1, h1);
            double[] cy1 = unbiasedCentre(bbY1, w1, h1, w1, h1);
            double[] cx2 = unbiasedCentre(bbX2, z2 * w1, z2 * h1,
                    z2 * w1, z2 * h1);
            double[] cy2 = unbiasedCentre(bbY2, z2 * w1, z2 * h1,
                    z2 * w1, z2 * h1);
            System.out.println("[RENDER] calib bbox: base=[" + bb1[0] + ","
                    + bb1[1] + "," + bb1[2] + "," + bb1[3] + "] z2=["
                    + bb2[0] + "," + bb2[1] + "," + bb2[2] + "," + bb2[3]
                    + "] k1x=[" + bbX1[0] + "," + bbX1[1] + "," + bbX1[2]
                    + "," + bbX1[3] + "] k1y=[" + bbY1[0] + "," + bbY1[1]
                    + "," + bbY1[2] + "," + bbY1[3] + "] k2x=[" + bbX2[0]
                    + "," + bbX2[1] + "," + bbX2[2] + "," + bbX2[3]
                    + "] k2y=[" + bbY2[0] + "," + bbY2[1] + "," + bbY2[2]
                    + "," + bbY2[3] + "]");
            System.out.println("[RENDER] calib centres: cx1=("
                    + String.format(java.util.Locale.US, "%.1f,%.1f",
                            cx1[0], cx1[1])
                    + ") cy1=(" + String.format(java.util.Locale.US,
                            "%.1f,%.1f", cy1[0], cy1[1])
                    + ") cx2=(" + String.format(java.util.Locale.US,
                            "%.1f,%.1f", cx2[0], cx2[1])
                    + ") cy2=(" + String.format(java.util.Locale.US,
                            "%.1f,%.1f", cy2[0], cy2[1]) + ")");
            double k1x = (cx1[0] - c0x) / dPan;
            double k1y = (cy1[1] - c0y) / dPan;
            double k2x = (cx2[0] - c2x) / dPan;
            double k2y = (cy2[1] - c2y) / dPan;
            if (k1x * k2x <= 0
                    || Math.abs(k1x) < 50 || Math.abs(k1y) < 50) {
                throw new IllegalStateException(
                        "framing: inconsistent pan gradient k1=(" + k1x + ","
                                + k1y + ") k2=(" + k2x + "," + k2y + ")");
            }
            // ---- official pan semantics: +/-1.0 moves the viewpoint
            //      one window width/height, a screen travel that is
            //      INDEPENDENT of zoom; the clean z=1 gradient is valid
            //      at the target zoom, z2 only cross-checks it. ----
            if (Math.abs(k2x / k1x - 1.0) > 0.5) {
                throw new IllegalStateException(
                        "framing: x pan gradient not zoom-invariant k1="
                                + k1x + " k2=" + k2x);
            }
            if (k1y * k2y <= 0 || Math.abs(k2y / k1y - 1.0) > 0.5) {
                System.out.println("[RENDER] WARN y pan gradient noisy"
                        + " (clip), using z=1 value k1y=" + k1y);
            }
            double kfx = k1x, kfy = k1y;

            // ---- step 5, window-band constraint: one vertical pan unit
            //      is one viewport height, so |k1y| IS the visible band
            //      height in export pixels.  The ink must fit inside the
            //      band with the arrow margin, otherwise the bottom of
            //      the subject is clipped by the letterbox band edge. ----
            double bandH = Math.abs(k1y);
            double vLimit = bandH - mArrow - 24.0;
            if (f * h1 > vLimit) {
                double fCap = Math.max(zMin, vLimit / h1);
                System.out.println("[RENDER] zoom capped by window band: "
                        + String.format(java.util.Locale.US,
                                "%.4f -> %.4f", f, fCap)
                        + " band=" + String.format(java.util.Locale.US,
                                "%.0f", bandH) + "px");
                f = fCap;
            }
            // ---- framing/v10 action focus: when the ACTION area (moving
            //      closure, exploded UNION complete state) can be zoomed
            //      meaningfully larger than the whole machine, frame the
            //      action alone.  The decision is analytic (action outline
            //      mm x the measured ink/outline ratio); the action ink is
            //      then MEASURED with the action-only SimpRep activated
            //      (official async member-level display filter), never
            //      estimated.  Any failure restores the full visible rep
            //      and falls back to whole-machine framing. ----
            boolean focus = false;
            boolean focusCapped = false;
            double wa1 = w1, ha1 = h1, ca1x = c0x, ca1y = c0y;
            double awMm = 0.0, ahMm = 0.0;
            if (actionOutlines > 0) {
                awMm = axMax - axMin;
                ahMm = ayMax - ayMin;
                if (awMm > 1e-6 && ahMm > 1e-6) {
                    double fFocusEst = Math.min(
                            targetW / (awMm * w1 / wMm),
                            targetH / (ahMm * h1 / hMm));
                    System.out.println("[RENDER] action outline="
                            + String.format(java.util.Locale.US,
                                    "%.1fx%.1f mm solids=%d focus_est=%.4f",
                                    awMm, ahMm, actionOutlines, fFocusEst));
                    if (fFocusEst >= 1.25 * f && focusIns != null) {
                        try {
                            assembly.ActivateSimpRep(
                                    assembly.CreateSimpRep(focusIns));
                            relockCamera(model, m3);
                            win.Repaint();
                            session.FlushCurrentWindow();
                            Thread.sleep(500);
                            reassertExplosion(assembly, explosionRecs,
                                    "focus_rep");
                            int[] bbA = inkBox(exportState(win, 0.0, 0.0,
                                    1.0, "fr_focus_measure.jpg", 900));
                            if (bbA != null && bbA[0] >= 4 && bbA[2] >= 4
                                    && bbA[1] <= IMAGE_SIZE - 4
                                    && bbA[3] <= IMAGE_SIZE - 4) {
                                wa1 = bbA[3] - bbA[2];
                                ha1 = bbA[1] - bbA[0];
                                ca1x = (bbA[2] + bbA[3]) / 2.0;
                                ca1y = (bbA[0] + bbA[1]) / 2.0;
                                focus = true;
                            }
                        } catch (Throwable focusFailure) {
                            System.out.println("[RENDER] focus measure failed: "
                                    + focusFailure);
                        }
                        if (!focus) {
                            try {
                                assembly.ActivateSimpRep(
                                        assembly.CreateSimpRep(repIns));
                            } catch (Throwable ignored) {
                            }
                            relockCamera(model, m3);
                            reassertExplosion(assembly, explosionRecs,
                                    "focus_fallback_rep");
                            win.Repaint();
                            session.FlushCurrentWindow();
                            Thread.sleep(500);
                        }
                    }
                }
            }
            if (focus) {
                // focus-final/v2 (sop-context/v1): the action zoom solve
                // is DIAGNOSTIC only; the final export keeps the whole-
                // machine camera so the image retains context.  Cut-out
                // focus frames are forbidden by the render rules.
                double fNeed = Math.min(targetW / wa1, targetH / ha1);
                focusCapped = fNeed > zMax - 1e-9;
                double fFocus = Math.max(zMin, Math.min(zMax, fNeed));
                if (fFocus * ha1 > vLimit) {
                    fFocus = Math.max(zMin, vLimit / ha1);
                    focusCapped = true;
                }
                System.out.println("[RENDER] action focus: ink="
                        + String.format(java.util.Locale.US,
                                "%.1fx%.1f", wa1, ha1)
                        + " centre=(" + String.format(java.util.Locale.US,
                                "%.1f,%.1f", ca1x, ca1y)
                        + ") zoom_diag=" + String.format(java.util.Locale.US,
                                "%.4f", fFocus)
                        + " final_zoom=" + String.format(java.util.Locale.US,
                                "%.4f", f));
            }
            final double expW = f * w1;
            final double expH = f * h1;  // whole-machine ink extent at f

            // ---- step 6: ONE closed-form solve:  c(z,pan) = A + z*(c1-A)
            //      + K*pan  =>  pan = (targ - c0f)/K; framing/v10 focus
            //      uses the MEASURED action ink centre as c1. ----
            // focus-final/v2: the park verification and the final export
            // show the FULL display; restore the whole visible rep now
            // (the focus audit below re-activates the action rep on its
            // own).  Leaving the focus rep active here made the park gate
            // depend on the action part alone staying on screen (30.22/
            // 30.23: the exploded part, amplified by the final zoom about
            // the machine centre, lands past the window edge -> blank
            // park -> throw).
            if (focus) {
                try {
                    assembly.ActivateSimpRep(
                            assembly.CreateSimpRep(repIns));
                } catch (Throwable ignored) {
                }
                relockCamera(model, m3);
                for (Layer layer : auxiliaryLayers.values()) {
                    try {
                        layer.SetStatus(DisplayStatus.LAYER_BLANK);
                    } catch (Throwable ignored) {
                    }
                }
                reassertExplosion(assembly, explosionRecs, "park_rep");
                win.Repaint();
                session.FlushCurrentWindow();
                Thread.sleep(500);
            }
            // focus-final/v2: the frame always centres the machine
            // ink; the action centre only feeds the diagnostic audit.
            double c1x = c0x;
            double c1y = c0y;
            double c0fx = axC + f * (c1x - axC);
            double c0fy = ayC + f * (c1y - ayC);
            double panX = (targCx - c0fx) / kfx;
            double panY = (targCy - c0fy) / kfy;

            // ---- the target state is applied by the verification
            //      export below (exportState sets it absolutely) ----
            System.out.println("[RENDER] analytic framing ink="
                    + String.format(java.util.Locale.US,
                            "%.1fx%.1f", w1, h1)
                    + " refit_centre=("
                    + String.format(java.util.Locale.US,
                            "%.1f,%.1f", c0x, c0y)
                    + ") pivot=(" + String.format(java.util.Locale.US,
                            "%.0f,%.0f", axC, ayC)
                    + ") zoom=" + String.format(java.util.Locale.US,
                            "%.4f", f)
                    + " grad=(" + String.format(java.util.Locale.US,
                            "%.0f,%.0f", kfx, kfy)
                    + ") pan=(" + String.format(java.util.Locale.US,
                            "%.4f,%.4f", panX, panY) + ")");

            // ---- step 7: ONE verification park at the solved state; a
            //      single closed-form correction trims the residual
            //      before the final export (fixed sequence). ----
            int[] bb3 = inkBox(exportState(win, panX, panY, f,
                    "fr_verify.jpg", 900));
            if (bb3 == null) {
                throw new IllegalStateException(
                        "framing: verification park has no ink (pan=("
                                + panX + "," + panY + ") zoom=" + f + ")");
            }
            double[] c3 = unbiasedCentre(bb3, expW, expH,
                    bb3[3] - bb3[2], bb3[1] - bb3[0]);
            panX += (targCx - c3[0]) / kfx;
            panY += (targCy - c3[1]) / kfy;
            System.out.println("[RENDER] park centre=("
                    + String.format(java.util.Locale.US,
                            "%.1f,%.1f", c3[0], c3[1])
                    + ") final pan=(" + String.format(java.util.Locale.US,
                            "%.4f,%.4f", panX, panY) + ")");

            // ---- framing/v10 focus: in focus mode the verify export
            //      already shows the action-only view; ONE audit export
            //      at the corrected state records the action residual
            //      (diagnostic under focus-final/v2), then full display
            //      is restored so the final image renders the complete
            //      visible set framed on the WHOLE machine. ----
            double focusResX = 0.0, focusResY = 0.0;
            int[] focusBbFinal = null;
            if (focus && focusIns != null) {
                // re-activate the action rep for the audit export only;
                // the block's tail restores the full display again.
                try {
                    assembly.ActivateSimpRep(
                            assembly.CreateSimpRep(focusIns));
                } catch (Throwable ignored) {
                }
                relockCamera(model, m3);
                reassertExplosion(assembly, explosionRecs, "focus_rep");
                win.Repaint();
                session.FlushCurrentWindow();
                Thread.sleep(500);
                focusBbFinal = inkBox(exportState(win, panX, panY, f,
                        "fr_focus_audit.jpg", 900));
                if (focusBbFinal != null) {
                    double[] ca = unbiasedCentre(focusBbFinal, expW, expH,
                            focusBbFinal[3] - focusBbFinal[2],
                            focusBbFinal[1] - focusBbFinal[0]);
                    focusResX = ca[0] - targCx;
                    focusResY = ca[1] - targCy;
                }
                try {
                    assembly.ActivateSimpRep(assembly.CreateSimpRep(repIns));
                } catch (Throwable ignored) {
                }
                relockCamera(model, m3);
                // re-assert the auxiliary blanks after the rep switch
                // (same hygiene as after the explosion transforms).
                for (Layer layer : auxiliaryLayers.values()) {
                    try {
                        layer.SetStatus(DisplayStatus.LAYER_BLANK);
                    } catch (Throwable ignored) {
                    }
                }
                reassertExplosion(assembly, explosionRecs, "final_rep");
                win.Repaint();
                session.FlushCurrentWindow();
                Thread.sleep(500);
                System.out.println("[RENDER] focus audit residual=("
                        + String.format(java.util.Locale.US,
                                "%.1f,%.1f", focusResX, focusResY) + ")");
            }

            auditExplosion(assembly, explosionRecs);
            // ---- step 8: final image; the epsilon protocol guarantees
            //      the exported frame is exactly the target state. ----
            String imgName = stepId + ".jpg";
            exportState(win, panX, panY, f, imgName, 900);
            Path imgPath = Paths.get(outDir, imgName).toAbsolutePath();
            Files.createDirectories(imgPath.getParent());
            Files.move(Paths.get(imgName).toAbsolutePath(), imgPath,
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            System.out.println("[RENDER] image -> " + imgPath
                    + " (" + Files.size(imgPath) + " bytes)");

            // ---- step 8b: residual audit.  Whole-machine mode: up to
            //      THREE closed-form correction re-exports when the
            //      tolerance is exceeded (fixed sequence, never a loop).
            //      correction-sequence/v2: when the ink height sits on
            //      the window-band cap, a correction pass can flip the
            //      edge-clip regime (clipped <-> free) and the measured
            //      pan sensitivity jumps with it; one extra pass lets
            //      the empirical gradient (screen shift per pan unit
            //      observed between passes) reconverge after a regime
            //      switch.  focus-final/v2: the correction sequence
            //      always runs on the whole-machine ink; the focus
            //      residual is diagnostic only. ----
            boolean corrected = false;
            double resX = 0.0, resY = 0.0;
            double prevResX = 0.0, prevResY = 0.0;
            double prevX = panX, prevY = panY;
            int[] bbF = inkBox(imgPath);
            if (bbF == null) {
                throw new IllegalStateException(
                        "framing: final export has no ink (pan=(" + panX
                                + "," + panY + ") zoom=" + f + ")");
            }
            // focus-final/v2: the action residual stays diagnostic;
            // the whole-machine ink is the framing target, so the
            // correction sequence always runs on it.
            if (focus) {
                System.out.println("[RENDER] focus residual diag=("
                        + String.format(java.util.Locale.US, "%.1f,%.1f)",
                        focusResX, focusResY));
            }
            {
                double[] cf = unbiasedCentre(bbF, expW, expH,
                        bbF[3] - bbF[2], bbF[1] - bbF[0]);
                resX = cf[0] - targCx;
                resY = cf[1] - targCy;
                System.out.println("[RENDER] framing residual=("
                        + String.format(java.util.Locale.US, "%.1f,%.1f)",
                        resX, resY));
                // up to THREE closed-form corrections (fixed sequence,
                // never a loop); passes 2+ use the empirically observed
                // screen shift per pan unit, which absorbs any
                // zoom-dependent gradient deviation.  Sign: res is
                // (measured - target) and pan+ moves the ink along +g,
                // so the correction is pan -= res/g.
                for (int pass = 0; pass < 3; pass++) {
                    if (Math.abs(resX) <= tolPx && Math.abs(resY) <= tolPx) {
                        break;
                    }
                    double gX = kfx, gY = kfy;
                    if (pass > 0) {
                        double dPx = panX - prevX, dPy = panY - prevY;
                        if (Math.abs(dPx) > 1e-6) {
                            double eX = (resX - prevResX) / dPx;
                            if (Math.abs(eX) > 50) gX = eX;
                        }
                        if (Math.abs(dPy) > 1e-6) {
                            double eY = (resY - prevResY) / dPy;
                            if (Math.abs(eY) > 50) gY = eY;
                        }
                    }
                    prevResX = resX;
                    prevResY = resY;
                    prevX = panX;
                    prevY = panY;
                    panX -= resX / gX;
                    panY -= resY / gY;
                    exportState(win, panX, panY, f, imgName, 900);
                    Files.move(Paths.get(imgName).toAbsolutePath(), imgPath,
                            java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                    corrected = true;
                    bbF = inkBox(imgPath);
                    if (bbF == null) break;
                    double[] cc = unbiasedCentre(bbF, expW, expH,
                            bbF[3] - bbF[2], bbF[1] - bbF[0]);
                    resX = cc[0] - targCx;
                    resY = cc[1] - targCy;
                    System.out.println("[RENDER] framing corrected pan=("
                            + String.format(java.util.Locale.US,
                                    "%.4f,%.4f", panX, panY)
                            + ") residual=("
                            + String.format(java.util.Locale.US,
                                    "%.1f,%.1f)", resX, resY));
                }
            }

            // ---- render metadata for arrow projection and audit ----
            StringBuilder meta = new StringBuilder();
            meta.append("{\n");
            meta.append("  \"schema\": \"clean-run-render-meta/v1\",\n");
            meta.append("  \"step_id\": ").append(q(stepId)).append(",\n");
            meta.append("  \"assembly_file\": ").append(q(model.GetFileName())).append(",\n");
            meta.append("  \"assembly_version\": ").append(q(safe(model.GetVersion()))).append(",\n");
            meta.append("  \"sha256\": ").append(q(actualHash)).append(",\n");
            meta.append("  \"camera\": ").append(q(cameraId)).append(",\n");
            meta.append("  \"camera_matrix\": ").append(matrixJson(cam)).append(",\n");
            meta.append(String.format(java.util.Locale.US,
                    "  \"zoom_factor\": %.9f,%n", f));
            meta.append(String.format(java.util.Locale.US,
                    "  \"view_scale\": %.9f,%n", s2));
            meta.append(String.format(java.util.Locale.US,
                    "  \"view_scale_base\": %.9f,%n", s2));
            meta.append("  \"view_rot\": [").append(String.format(java.util.Locale.US,
                    "[%.9f,%.9f,%.9f],[%.9f,%.9f,%.9f],[%.9f,%.9f,%.9f]]",
                    R2[0][0], R2[0][1], R2[0][2], R2[1][0], R2[1][1], R2[1][2],
                    R2[2][0], R2[2][1], R2[2][2])).append(",\n");
            meta.append(String.format(java.util.Locale.US,
                    "  \"view_trans\": [%.6f,%.6f,%.6f],%n", t2[0], t2[1], t2[2]));
            meta.append(String.format(java.util.Locale.US,
                    "  \"framing\": {\"schema\": \"framing_measurement/v9\", "
                            + "\"method\": \"official/v9-refit\", "
                            + "\"refit_bbox\": [%d,%d,%d,%d], "
                            + "\"park_bbox\": [%d,%d,%d,%d], "
                            + "\"final_bbox\": [%s], "
                            + "\"pivot\": [%.1f,%.1f], "
                            + "\"zoom_multiplier\": %.6f, "
                            + "\"refit_centre\": [%.1f,%.1f], "
                            + "\"pan\": [%.6f,%.6f], "
                            + "\"k_px_per_mm\": %.6f, "
                            + "\"outline_mm\": [%.2f,%.2f], "
                            + "\"outline_centre\": [%.2f,%.2f], "
                            + "\"solids\": %d, "
                            + "\"target_center\": [%.1f,%.1f], "
                            + "\"residual\": [%.1f,%.1f], "
                            + "\"corrected\": %b, "
                            + "\"policy\": {\"occupancy\": %.2f, \"margins\": [%d,%d,%d,%d], "
                            + "\"arrow_margin\": %d, \"zoom_limits\": [%.1f,%.1f], "
                            + "\"tolerance_px\": %.1f}},%n",
                    bb1[0], bb1[1], bb1[2], bb1[3],
                    bb3[0], bb3[1], bb3[2], bb3[3],
                    bbF == null ? "null" : String.format("%d,%d,%d,%d",
                            bbF[0], bbF[1], bbF[2], bbF[3]),
                    axC, ayC, f, c0x, c0y, panX, panY, w1 / wMm,
                    wMm, hMm, mX, mY, outlines,
                    targCx, targCy, resX, resY, corrected,
                    occupancy, mL, mR, mT, mB, mArrow, zMin, zMax, tolPx));
            // ---- framing/v10 focus record (focus_affine/v1): when
            //      enabled, overlay and review consume the affine below
            //      instead of re-solving from the full-view bbox.  The
            //      affine maps overlay view_point coords (v = s2*R2*w+t2)
            //      to export pixels: screen = A + z*(p1 - A) + K*pan
            //      with p1 taken from the MEASURED z=1 action ink. ----
            StringBuilder fb = new StringBuilder();
            fb.append("  \"focus\": {\"schema\": \"focus_affine/v1\", ");
            fb.append("\"enabled\": ").append(focus)
                    .append(", \"drives_final\": false, ");
            fb.append(String.format(java.util.Locale.US,
                    "\"action_solids\": %d, ", actionOutlines));
            fb.append(String.format(java.util.Locale.US,
                    "\"action_outline_mm\": [%.2f,%.2f], ", awMm, ahMm));
            if (focus) {
                double amXa = (axMin + axMax) / 2.0;
                double amYa = (ayMin + ayMax) / 2.0;
                double gx = wa1 / awMm, gy = ha1 / ahMm;
                double sfx = f * gx;
                double ofx = (1.0 - f) * axC + f * (ca1x - amXa * gx)
                        + kfx * panX;
                double sfy = -f * gy;
                double ofy = (1.0 - f) * ayC + f * (ca1y + amYa * gy)
                        + kfy * panY;
                double axF = sfx / s2, bxF = ofx - sfx * t2[0] / s2;
                double ayF = sfy / s2, byF = ofy - sfy * t2[1] / s2;
                fb.append(String.format(java.util.Locale.US,
                        "\"action_ink_refit\": [%.1f,%.1f,%.1f,%.1f], ",
                        wa1, ha1, ca1x, ca1y));
                fb.append("\"action_ink_final\": ");
                if (focusBbFinal == null) {
                    fb.append("null, ");
                } else {
                    fb.append(String.format(java.util.Locale.US,
                            "[%d,%d,%d,%d], ", focusBbFinal[0],
                            focusBbFinal[1], focusBbFinal[2],
                            focusBbFinal[3]));
                }
                fb.append(String.format(java.util.Locale.US,
                        "\"residual\": [%.1f,%.1f], ", resX, resY));
                fb.append(String.format(java.util.Locale.US,
                        "\"capped\": %b, ", focusCapped));
                fb.append(String.format(java.util.Locale.US,
                        "\"affine\": [%.9f,%.9f,%.9f,%.9f]},%n",
                        axF, bxF, ayF, byF));
            } else {
                fb.append("\"action_ink_refit\": null, "
                        + "\"action_ink_final\": null, "
                        + "\"residual\": null, \"capped\": null, "
                        + "\"affine\": null},\n");
            }
            meta.append(fb);
            meta.append("  \"image_size\": ").append(IMAGE_SIZE).append(",\n");
            meta.append("  \"image_file\": ").append(q(imgPath.getFileName().toString())).append(",\n");
            meta.append("  \"moving\": [\n");
            for (int i = 0; i < movingList.size(); i++) {
                Map<String, Object> mv = (Map<String, Object>) movingList.get(i);
                meta.append("    {\"path\": ").append(q((String) mv.get("path")));
                meta.append(", \"anchor_complete\": ").append(vecJson((List<Object>) mv.get("anchor_complete")));
                meta.append(", \"anchor_exploded\": ").append(vecJson((List<Object>) mv.get("anchor_exploded")));
                meta.append(", \"translation\": ").append(vecJson((List<Object>) mv.get("translation")));
                meta.append("}").append(i < movingList.size() - 1 ? "," : "").append("\n");
            }
            meta.append("  ]\n}\n");
            Path metaPath = Paths.get(outDir, stepId + ".render.json");
            try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(metaPath, StandardCharsets.UTF_8))) {
                w.print(meta);
            }
            System.out.println("[RENDER] metadata -> " + metaPath);
        } finally {
            if (conn != null) {
                try { conn.End(); } catch (Throwable ignored) {}
            }
        }
        System.exit(0);
    }

    /** Export the CURRENT screen state without touching the transform.
     *  Repaint synchronously, wait for the redraw to finish, then capture
     *  exactly what is on screen. */
    private static Path exportCurrent(Window win, String name, long waitMs)
            throws Exception {
        win.Repaint();
        Thread.sleep(waitMs);
        JPEGImageExportInstructions exp =
                pfcWindow.JPEGImageExportInstructions_Create(
                        EXPORT_INCHES, EXPORT_INCHES);
        exp.SetDotsPerInch(com.ptc.pfc.pfcWindow.DotsPerInch.RASTERDPI_100);
        win.ExportRasterImage(name, exp);
        return Paths.get(name).toAbsolutePath();
    }

    /** Set ONE absolute screen state (pan,zoom), repaint, wait for the
     *  redraw to finish, then export.  ExportRasterImage can capture the
     *  state BEFORE the latest SetScreenTransform, so a tiny epsilon pan
     *  is pushed after the target state and never rendered. */
    private static Path exportState(Window win, double px, double py,
            double z, String name, long waitMs) throws Exception {
        win.SetScreenTransform(pfcBase.ScreenTransform_Create(px, py, z));
        win.Repaint();
        Thread.sleep(waitMs);
        win.SetScreenTransform(pfcBase.ScreenTransform_Create(
                px + 0.002, py, z));
        win.Repaint();
        JPEGImageExportInstructions exp =
                pfcWindow.JPEGImageExportInstructions_Create(
                        EXPORT_INCHES, EXPORT_INCHES);
        exp.SetDotsPerInch(com.ptc.pfc.pfcWindow.DotsPerInch.RASTERDPI_100);
        win.ExportRasterImage(name, exp);
        return Paths.get(name).toAbsolutePath();
    }

    /** Edge-clip-unbiased ink centre {x,y} (centering/v3, shared calibre
     *  with auto_review rule_centering): the raw centre is biased ONLY when
     *  a side is actually clipped by the canvas edge; the true centre is
     *  then recovered from the UNclipped edge plus the expected extent
     *  (extent scales linearly with zoom).  A shortfall with BOTH edges
     *  free is an extent-model error, not clipping - the raw centre stays
     *  the honest estimate and the residual audit corrects it in closed
     *  form (v2 recovered anyway, and from the wrong edge, which actively
     *  dragged the final pan off target). */
    private static double[] unbiasedCentre(int[] bb, double expW,
            double expH, double measW, double measH) {
        double cx = (bb[2] + bb[3]) / 2.0;
        double cy = (bb[0] + bb[1]) / 2.0;
        final int edge = 6;
        final int size = IMAGE_SIZE;
        boolean lClip = bb[2] <= edge, rClip = bb[3] >= size - edge;
        if (measW < expW - edge && (lClip != rClip)) {
            cx = lClip ? bb[3] - expW / 2.0          // left clipped
                       : bb[2] + expW / 2.0;         // right clipped
        }
        boolean tClip = bb[0] <= edge, bClip = bb[1] >= size - edge;
        if (measH < expH - edge && (tClip != bClip)) {
            cy = tClip ? bb[1] - expH / 2.0          // top clipped
                       : bb[0] + expH / 2.0;         // bottom clipped
        }
        return new double[] {cx, cy};
    }

    /** framing/v10: an occurrence belongs to the ACTION set when it is a
     *  moving root or a descendant of one (rigid closure). */
    private static boolean isAction(String path, java.util.Set<String> mv) {
        for (String mp : mv) {
            if (path.equals(mp) || path.startsWith(mp + "/")) return true;
        }
        return false;
    }

    /** Copy a plain 4x4 array into a Matrix3D (for Transform3D_Create). */
    private static Matrix3D toMatrix(double[][] m) throws jxthrowable {
        Matrix3D out = Matrix3D.create();
        for (int r = 0; r < 4; r++)
            for (int c = 0; c < 4; c++)
                out.set(r, c, m[r][c]);
        return out;
    }

    /** Background-aware ink bounding box {row0,row1,col0,col1} or null.
     *  Pixels inside the bottom-left exclusion zone are ignored: with an
     *  active SimpRep Creo stamps a "…:CLEAN_n" label there that would
     *  otherwise poison every framing measurement. */
    private static int[] inkBox(Path jpeg) {
        try {
            java.awt.image.BufferedImage img =
                    javax.imageio.ImageIO.read(jpeg.toFile());
            if (img == null) return null;
            int w = img.getWidth(), h = img.getHeight();
            int exX = (int) (w * 0.35);   // label zone: cols < 35%
            int exY = (int) (h * 0.72);   //              rows > 72%
            double[] g = new double[w * h];
            for (int y = 0; y < h; y++)
                for (int x = 0; x < w; x++) {
                    int rgb = img.getRGB(x, y);
                    g[y * w + x] = (((rgb >> 16) & 255)
                            + ((rgb >> 8) & 255) + (rgb & 255)) / 3.0;
                }
            int n = 50;
            double[] corner = new double[4 * n * n];
            int ci = 0;
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++) {
                    corner[ci++] = g[y * w + x];
                    corner[ci++] = g[y * w + (w - 1 - x)];
                    corner[ci++] = g[(h - 1 - y) * w + x];
                    corner[ci++] = g[(h - 1 - y) * w + (w - 1 - x)];
                }
            java.util.Arrays.sort(corner);
            double bg = corner[corner.length / 2];
            int r0 = -1, r1 = -1, c0 = -1, c1 = -1;
            for (int y = 0; y < h; y++)
                for (int x = 0; x < w; x++) {
                    if (y > exY && x < exX) continue;   // label exclusion
                    if (Math.abs(g[y * w + x] - bg) > 12) {
                        if (r0 < 0) r0 = y;
                        r1 = y;
                        if (c0 < 0 || x < c0) c0 = x;
                        if (x > c1) c1 = x;
                    }
                }
            if (r0 < 0 || (r1 - r0) < 4 || (c1 - c0) < 4) return null;
            return new int[]{r0, r1, c0, c1};
        } catch (Exception e) {
            return null;
        }
    }

    /** Pure translation of one occurrence in ROOT coordinates, applied
     *  BEFORE any SimpRep is activated. Prefers the official ComponentPath
     *  API with a numeric read-back verification; on rejection falls back to
     *  a mathematically equivalent ComponentFeat reposition resolved along
     *  the same root path.  Returns the absolute exploded pose record so
     *  the explosion can be re-asserted after later SimpRep switches. */
    static ExplosionRecord translateOccurrence(Session session, Assembly root,
            intseq ids, double dx, double dy, double dz) throws Exception {
        try {
            ComponentPath cp = pfcAssembly.CreateComponentPath(root, ids);
            Transform3D pose = cp.GetTransform(true);
            Point3D origin = pose.GetOrigin();
            double exX = origin.get(0) + dx;
            double exY = origin.get(1) + dy;
            double exZ = origin.get(2) + dz;
            origin.set(0, exX); origin.set(1, exY); origin.set(2, exZ);
            pose.SetOrigin(origin);
            cp.SetTransform(true, pose);
            Transform3D after = cp.GetTransform(true);
            if (!sameRotation(pose, after)) {
                throw new IllegalStateException("rotation changed for "
                        + pathText(ids));
            }
            Point3D ao = after.GetOrigin();
            if (Math.abs(ao.get(0) - exX) > 1e-3
                    || Math.abs(ao.get(1) - exY) > 1e-3
                    || Math.abs(ao.get(2) - exZ) > 1e-3) {
                throw new IllegalStateException("translation not applied for "
                        + pathText(ids));
            }
            System.out.println("[RENDER] transform_audit occurrence="
                    + pathText(ids) + " api=ComponentPath origin="
                    + String.format(java.util.Locale.US,
                            "[%.3f,%.3f,%.3f]", ao.get(0), ao.get(1), ao.get(2)));
            return new ExplosionRecord(ids, true, after, null, null, null,
                    new double[]{exX, exY, exZ});
        } catch (jxthrowable pathUnsupported) {
            System.out.println("[RENDER] component_path_fallback occurrence="
                    + pathText(ids));
        }
        return translateViaFeature(session, root, ids, dx, dy, dz);
    }

    /** Reposition the component feature's placement itself (a real model
     *  change, unlike ComponentPath.SetTransform which is temporary
     *  DynamicPositioning).  Drawing views only render real placement
     *  changes (exploded-parity/v1: the drawing channel therefore uses
     *  this mechanism exclusively).  Package-visible for
     *  DrawingRenderer. */
    static ExplosionRecord translateViaFeature(Session session, Assembly root,
            intseq ids, double dx, double dy, double dz) throws Exception {
        ResolvedComponent resolved = resolveComponent(session, root, ids);
        Transform3D local = resolved.feature.GetPosition();
        // root vector -> parent coordinates (row-vector convention: d * M)
        double[] rootDelta = {dx, dy, dz};
        double[] parentDelta = new double[3];
        for (int col = 0; col < 3; col++) {
            for (int row = 0; row < 3; row++) {
                parentDelta[col] += rootDelta[row]
                        * resolved.parentToRoot[row][col];
            }
        }
        Point3D lo = local.GetOrigin();
        double exX = lo.get(0) + parentDelta[0];
        double exY = lo.get(1) + parentDelta[1];
        double exZ = lo.get(2) + parentDelta[2];
        lo.set(0, exX); lo.set(1, exY); lo.set(2, exZ);
        local.SetOrigin(lo);
        resolved.feature.SetPosition(local);
        Transform3D after = resolved.feature.GetPosition();
        if (!sameRotation(local, after)) {
            throw new IllegalStateException("rotation changed for "
                    + pathText(ids));
        }
        Point3D ao = after.GetOrigin();
        if (Math.abs(ao.get(0) - exX) > 1e-3
                || Math.abs(ao.get(1) - exY) > 1e-3
                || Math.abs(ao.get(2) - exZ) > 1e-3) {
            throw new IllegalStateException(
                    "translation not applied for " + pathText(ids));
        }
        double[][] rootPose = mul4(matrixOf(after), resolved.parentToRoot);
        System.out.println("[RENDER] transform_audit occurrence="
                + pathText(ids) + " api=ComponentFeatFallback origin="
                + String.format(java.util.Locale.US, "[%.3f,%.3f,%.3f]",
                        rootPose[3][0], rootPose[3][1], rootPose[3][2]));
        return new ExplosionRecord(ids, false, null, after,
                resolved.feature, resolved.parentToRoot,
                new double[]{rootPose[3][0], rootPose[3][1], rootPose[3][2]});
    }

    /** Absolute exploded pose recorded at first application (root or
     *  parent coordinates per mechanism) so the explosion can be
     *  re-asserted idempotently after SimpRep switches.
     *  Package-visible: DrawingRenderer reuses the explosion mechanism
     *  until Renderer retires (drawing-arrows/v1 Task 8). */
    static final class ExplosionRecord {
        final intseq ids;
        final boolean viaPath;
        final Transform3D rootPose;
        final Transform3D localPose;
        final ComponentFeat feat;
        final double[][] parentToRoot;
        final double[] expectedRootOrigin;
        ExplosionRecord(intseq ids, boolean viaPath, Transform3D rootPose,
                Transform3D localPose, ComponentFeat feat,
                double[][] parentToRoot, double[] expectedRootOrigin) {
            this.ids = ids;
            this.viaPath = viaPath;
            this.rootPose = rootPose;
            this.localPose = localPose;
            this.feat = feat;
            this.parentToRoot = parentToRoot;
            this.expectedRootOrigin = expectedRootOrigin;
        }
    }

    /** camera-reassert/v1: a SimpRep switch's regeneration can refit the
     *  window to the currently visible geometry (observed: focus-rep and
     *  repIns exports under identical screen params produced different
     *  base cameras, so the focus affine calibrated on the wrong fit and
     *  arrows landed in blank space).  Re-applying the locked view
     *  transform after every switch makes the camera rep-independent. */
    private static void relockCamera(Model model, Matrix3D m3) {
        try {
            model.SetCurrentViewTransform(pfcBase.Transform3D_Create(m3));
        } catch (Throwable ignored) {
        }
    }

    /** explode-reassert/v1: unconditionally re-apply the recorded absolute
     *  exploded poses after a SimpRep switch (its regeneration may drop
     *  the temporary DynamicPositioning transforms). */
    static void reassertExplosion(Assembly root,
            List<ExplosionRecord> recs, String why) {
        int n = 0;
        for (ExplosionRecord r : recs) {
            try {
                if (r.viaPath) {
                    pfcAssembly.CreateComponentPath(root, r.ids)
                            .SetTransform(true, r.rootPose);
                } else {
                    r.feat.SetPosition(r.localPose);
                }
                n++;
            } catch (Throwable t) {
                System.out.println("[RENDER] reassert_failed (" + why
                        + "): " + t);
            }
        }
        System.out.println("[RENDER] explode_reassert " + why + " n=" + n
                + "/" + recs.size());
    }

    /** Numeric read-back observability of the explosion right before the
     *  final export (GetTransform under a SimpRep is unreliable for nested
     *  paths, so a mismatch is logged, not fatal). */
    private static void auditExplosion(Assembly root,
            List<ExplosionRecord> recs) {
        int ok = 0, mismatch = 0;
        for (ExplosionRecord r : recs) {
            try {
                double[] o;
                if (r.viaPath) {
                    Point3D po = pfcAssembly.CreateComponentPath(root, r.ids)
                            .GetTransform(true).GetOrigin();
                    o = new double[]{po.get(0), po.get(1), po.get(2)};
                } else {
                    double[][] rp = mul4(matrixOf(r.feat.GetPosition()),
                            r.parentToRoot);
                    o = new double[]{rp[3][0], rp[3][1], rp[3][2]};
                }
                boolean good = true;
                for (int k = 0; k < 3; k++) {
                    good &= Math.abs(o[k] - r.expectedRootOrigin[k]) <= 1e-3;
                }
                if (good) {
                    ok++;
                } else {
                    mismatch++;
                }
            } catch (Throwable t) {
                mismatch++;
            }
        }
        System.out.println("[RENDER] explode_final_audit ok=" + ok
                + " mismatch=" + mismatch);
    }

    private static final class ResolvedComponent {
        final ComponentFeat feature;
        final double[][] parentToRoot;
        ResolvedComponent(ComponentFeat feature, double[][] parentToRoot) {
            this.feature = feature;
            this.parentToRoot = parentToRoot;
        }
    }

    /** Walk the occurrence path feature-by-feature; accumulates the
     *  parent-assembly transforms so a leaf position can be expressed in
     *  root coordinates without ComponentPath. */
    private static ResolvedComponent resolveComponent(Session session,
            Assembly root, intseq ids) throws jxthrowable {
        Assembly current = root;
        double[][] currentToRoot = identity4();
        for (int depth = 0; depth < ids.getarraysize(); depth++) {
            int wanted = ids.get(depth);
            ComponentFeat found = null;
            Features features = current.ListFeaturesByType(
                    Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
            for (int index = 0; index < features.getarraysize(); index++) {
                ComponentFeat candidate =
                        (ComponentFeat) features.get(index);
                if (candidate.GetId() == wanted) {
                    found = candidate;
                    break;
                }
            }
            if (found == null) {
                throw new IllegalArgumentException(
                        "occurrence path not found: " + pathText(ids));
            }
            if (depth == ids.getarraysize() - 1) {
                return new ResolvedComponent(found, currentToRoot);
            }
            Model child = session.RetrieveModel(found.GetModelDescr());
            if (!(child instanceof Assembly)) {
                throw new IllegalArgumentException(
                        "path crosses a non-assembly: " + pathText(ids));
            }
            currentToRoot = mul4(matrixOf(found.GetPosition()), currentToRoot);
            current = (Assembly) child;
        }
        throw new IllegalArgumentException("empty occurrence path");
    }

    private static String pathText(intseq ids) throws jxthrowable {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < ids.getarraysize(); i++) {
            if (i > 0) sb.append('/');
            sb.append(ids.get(i));
        }
        return sb.toString();
    }

    private static double[][] identity4() {
        double[][] m = new double[4][4];
        for (int i = 0; i < 4; i++) m[i][i] = 1.0;
        return m;
    }

    private static double[][] matrixOf(Transform3D t) throws jxthrowable {
        Matrix3D src = t.GetMatrix();
        double[][] m = new double[4][4];
        for (int r = 0; r < 4; r++)
            for (int c = 0; c < 4; c++) m[r][c] = src.get(r, c);
        return m;
    }

    private static double[][] mul4(double[][] a, double[][] b) {
        double[][] m = new double[4][4];
        for (int r = 0; r < 4; r++)
            for (int c = 0; c < 4; c++)
                for (int k = 0; k < 4; k++) m[r][c] += a[r][k] * b[k][c];
        return m;
    }

    /** Creo may normalize a matrix by a few ulps after Set/GetPosition;
     *  1e-7 keeps a real rotation change a hard failure. */
    private static boolean sameRotation(Transform3D first, Transform3D second)
            throws jxthrowable {
        double[][] a = matrixOf(first), b = matrixOf(second);
        for (int r = 0; r < 3; r++)
            for (int c = 0; c < 3; c++) {
                if (Math.abs(a[r][c] - b[r][c]) > 1.0e-7) return false;
            }
        return true;
    }

    /** Blanks weld symbols, datums, curves, cosmetics and annotations in
     *  EVERY model already loaded in the session (opening the assembly
     *  loads the full subtree, so no per-component RetrieveModel is
     *  needed - retrieval failures previously left whole subtrees with
     *  visible datum curves).  Feature-type based only, no visual
     *  heuristic. */
    private static int hideAuxiliaryFeatures(Session session) throws jxthrowable {
        int hidden = 0;
        Models all = session.ListModels();
        for (int i = 0; all != null && i < all.getarraysize(); i++) {
            Model m = all.get(i);
            try {
                hidden += blankAuxiliaryFeatures(m);
            } catch (Throwable modelFailure) {
                System.out.println("[RENDER] auxiliary_hide_failed="
                        + m.GetFileName() + " : " + modelFailure);
            }
        }
        return hidden;
    }

    private static int blankAuxiliaryFeatures(Model model) throws jxthrowable {
        int hidden = 0;
        Layer auxiliaryLayer = null;
        for (FeatureType type : AUXILIARY_FEATURE_TYPES) {
            try {
                Features features = null;
                if (model instanceof Solid) {
                    features = ((Solid) model).ListFeaturesByType(
                            Boolean.FALSE, type);
                } else if (model instanceof Assembly) {
                    features = ((Assembly) model).ListFeaturesByType(
                            Boolean.FALSE, type);
                } else {
                    continue;
                }
                for (int i = 0; features != null && i < features.getarraysize(); i++) {
                    if (auxiliaryLayer == null) {
                        auxiliaryLayer = model.CreateLayer("AI_SOP_AUXILIARY");
                        auxiliaryLayers.put(model.GetFileName(), auxiliaryLayer);
                    }
                    auxiliaryLayer.AddItem(features.get(i));
                    hidden++;
                }
            } catch (Throwable typeFailure) {
                System.out.println("[RENDER] auxiliary_type_skipped="
                        + model.GetFileName() + " : " + type + " : "
                        + typeFailure);
            }
        }
        if (auxiliaryLayer != null) {
            auxiliaryLayer.SetStatus(DisplayStatus.LAYER_BLANK);
        }
        return hidden;
    }

    private static intseq pathToIds(String path) throws Exception {
        String[] parts = path.split("/");
        intseq ids = intseq.create();
        for (int i = 1; i < parts.length; i++) ids.append(Integer.parseInt(parts[i]));
        return ids;
    }

    private static String sha256(Path p) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] data = Files.readAllBytes(p);
        byte[] hash = md.digest(data);
        StringBuilder sb = new StringBuilder();
        for (byte b : hash) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    private static String q(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (char ch : safe(s).toCharArray()) {
            if (ch == '"' || ch == '\\') sb.append('\\');
            sb.append(ch);
        }
        return sb.append('"').toString();
    }

    private static String safe(String s) { return s == null ? "" : s; }

    private static String matrixJson(double[][] m) {
        StringBuilder sb = new StringBuilder("[");
        for (int r = 0; r < 4; r++) {
            sb.append("[");
            for (int c = 0; c < 4; c++) {
                sb.append(String.format(java.util.Locale.US, "%.9f", m[r][c]));
                if (c < 3) sb.append(",");
            }
            sb.append("]");
            if (r < 3) sb.append(",");
        }
        return sb.append("]").toString();
    }

    private static String vecJson(List<Object> v) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < v.size(); i++) {
            sb.append(String.format(java.util.Locale.US, "%.6f", ((Number) v.get(i)).doubleValue()));
            if (i < v.size() - 1) sb.append(",");
        }
        return sb.append("]").toString();
    }

    /** Minimal recursive-descent JSON parser (objects, arrays, strings,
     *  numbers, booleans, null). Written from scratch for the clean run. */
    static class MiniJson {
        private final String s;
        private int i;

        private MiniJson(String s) { this.s = s; }

        static Object parse(String text) {
            MiniJson p = new MiniJson(text);
            p.ws();
            Object v = p.value();
            p.ws();
            if (p.i != p.s.length()) throw new IllegalStateException("trailing JSON content");
            return v;
        }

        private Object value() {
            ws();
            if (i >= s.length()) throw new IllegalStateException("unexpected end");
            char c = s.charAt(i);
            if (c == '{') return obj();
            if (c == '[') return arr();
            if (c == '"') return str();
            if (c == 't') { expect("true"); return Boolean.TRUE; }
            if (c == 'f') { expect("false"); return Boolean.FALSE; }
            if (c == 'n') { expect("null"); return null; }
            return num();
        }

        private Map<String, Object> obj() {
            Map<String, Object> m = new LinkedHashMap<>();
            i++; // {
            ws();
            if (peek() == '}') { i++; return m; }
            while (true) {
                ws();
                String k = str();
                ws();
                if (s.charAt(i) != ':') throw new IllegalStateException("expected ':'");
                i++;
                m.put(k, value());
                ws();
                char c = s.charAt(i);
                if (c == ',') { i++; continue; }
                if (c == '}') { i++; return m; }
                throw new IllegalStateException("expected ',' or '}'");
            }
        }

        private List<Object> arr() {
            List<Object> l = new ArrayList<>();
            i++; // [
            ws();
            if (peek() == ']') { i++; return l; }
            while (true) {
                l.add(value());
                ws();
                char c = s.charAt(i);
                if (c == ',') { i++; continue; }
                if (c == ']') { i++; return l; }
                throw new IllegalStateException("expected ',' or ']'");
            }
        }

        private String str() {
            if (s.charAt(i) != '"') throw new IllegalStateException("expected string");
            i++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') return sb.toString();
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'n': sb.append('\n'); break;
                        case 't': sb.append('\t'); break;
                        case 'r': sb.append('\r'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'u':
                            sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                            i += 4;
                            break;
                        default: throw new IllegalStateException("bad escape");
                    }
                } else {
                    sb.append(c);
                }
            }
        }

        private Number num() {
            int start = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            String t = s.substring(start, i);
            if (t.contains(".") || t.contains("e") || t.contains("E")) return Double.parseDouble(t);
            return Long.parseLong(t);
        }

        private void expect(String w) {
            if (!s.startsWith(w, i)) throw new IllegalStateException("expected " + w);
            i += w.length();
        }

        private char peek() { return s.charAt(i); }

        private void ws() {
            while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++;
        }
    }
}
