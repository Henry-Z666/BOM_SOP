import com.ptc.cipjava.intseq;
import com.ptc.pfc.pfcAssembly.Assembly;
import com.ptc.pfc.pfcAssembly.ComponentPath;
import com.ptc.pfc.pfcAssembly.pfcAssembly;
import com.ptc.pfc.pfcAsyncConnection.AsyncConnection;
import com.ptc.pfc.pfcAsyncConnection.pfcAsyncConnection;
import com.ptc.pfc.pfcBase.Outline3D;
import com.ptc.pfc.pfcBase.Point3D;
import com.ptc.pfc.pfcBase.Transform3D;
import com.ptc.pfc.pfcComponentFeat.ComponentFeat;
import com.ptc.pfc.pfcExceptions.XToolkitCantOpen;
import com.ptc.pfc.pfcFeature.Feature;
import com.ptc.pfc.pfcFeature.Features;
import com.ptc.pfc.pfcFeature.FeatureType;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcModelItem.ModelItemType;
import com.ptc.pfc.pfcModelItem.ModelItemTypes;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcSolid.Solid;

import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

/**
 * Clean-room CAD discovery tool (written from scratch for the clean_run test).
 *
 * Opens the final total assembly read-only, recursively walks every component
 * occurrence and emits a JSON graph: occurrence path (root feature-id chain),
 * model file, 4x4 placement transform and world-space bounding box.
 *
 * args: [0] assembly file name (opened from the working directory)
 *       [1] output JSON path
 */
public class Discovery {

    private static final StringBuilder OCC = new StringBuilder();
    private static int occCount = 0;
    private static int outlineErrors = 0;

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: Discovery <asmFileName> <outputJson>");
            System.exit(2);
        }
        String asmName = args[0];
        String outPath = args[1];

        AsyncConnection conn = null;
        try {
            // Per PTC OTK Java troubleshooting guide: the async native library
            // must be loaded explicitly before the first J-Link call.
            System.loadLibrary("pfcasyncmt");
            System.out.println("[DISCOVERY] pfcasyncmt loaded");
            System.out.println("[DISCOVERY] starting Creo session ...");
            conn = pfcAsyncConnection.AsyncConnection_Start(args.length > 2 ? args[2] : "", null);
            Session session = conn.GetSession();

            // Creo file names carry a .N version suffix; the descriptor API
            // only accepts the bare model name (it opens the highest version).
            String openName = asmName.replaceFirst("\\.\\d+$", "");
            ModelDescriptor descr = pfcModel.ModelDescriptor_CreateFromFileName(openName);
            System.out.println("[DISCOVERY] opening " + openName + " (requested file: " + asmName + ")");
            // OpenFile returns the window; activate it so the model becomes
            // the active model, then fall back to the session model registry.
            com.ptc.pfc.pfcWindow.Window win = session.OpenFile(descr);
            if (win != null) {
                win.Activate();
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

            String rootToken = model.GetFileName().replaceFirst("(?i)\\.asm$", "");
            StringBuilder json = new StringBuilder();
            json.append("{\n");
            json.append("  \"schema\": \"clean-run-discovery/v1\",\n");
            json.append("  \"assembly_file\": ").append(quote(asmName)).append(",\n");
            json.append("  \"assembly_version\": ").append(quote(safe(model.GetVersion()))).append(",\n");
            json.append("  \"root_token\": ").append(quote(rootToken)).append(",\n");

            List<String> records = new ArrayList<>();
            intseq rootPath = intseq.create();
            walk(root, root, rootPath, rootToken, records);

            json.append("  \"occurrence_count\": ").append(occCount).append(",\n");
            json.append("  \"occurrences\": [\n");
            for (int i = 0; i < records.size(); i++) {
                json.append(records.get(i)).append(i < records.size() - 1 ? ",\n" : "\n");
            }
            json.append("  ]\n}\n");

            try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(
                    Paths.get(outPath), StandardCharsets.UTF_8))) {
                w.print(json);
            }
            System.out.println("[DISCOVERY] wrote " + occCount + " occurrences -> " + outPath);
        } finally {
            if (conn != null) {
                try {
                    conn.End();
                } catch (Throwable ignored) {
                }
            }
        }
        System.exit(0);
    }

    private static void walk(Assembly rootAsm, Solid parent, intseq pathIds, String rootToken,
                             List<String> records)
            throws Exception {
        Features feats = parent.ListFeaturesByType(true, FeatureType.FEATTYPE_COMPONENT);
        if (feats == null) return;
        for (int i = 0; i < feats.getarraysize(); i++) {
            Feature feat = feats.get(i);
            ComponentFeat comp;
            try {
                comp = (ComponentFeat) feat;
            } catch (ClassCastException skip) {
                continue;
            }
            int featId = feat.GetId();
            intseq full = intseq.create();
            for (int k = 0; k < pathIds.getarraysize(); k++) full.append(pathIds.get(k));
            full.append(featId);

            String path = rootToken + "/" + joinPath(full);
            String modelName = "?";
            String modelType = "?";
            String version = "?";
            double[][] matrix = new double[4][4];
            boolean transformOk = false;
            double[] bbox = null;
            boolean leafAsm = false;

            try {
                ModelDescriptor md = comp.GetModelDescr();
                modelName = md.GetFileName();
                modelType = md.GetType().toString();
            } catch (Throwable t) {
                modelName = "ERR:" + t.getMessage();
            }

            try {
                Transform3D pos = comp.GetPosition();
                for (int r = 0; r < 4; r++)
                    for (int c = 0; c < 4; c++)
                        matrix[r][c] = pos.GetMatrix().get(r, c);
                transformOk = true;
            } catch (Throwable t) {
                // leave transformOk false
            }

            Solid leaf = null;
            try {
                ComponentPath cpath = pfcAssembly.CreateComponentPath(rootAsm, full);
                leaf = cpath.GetLeaf();
                if (leaf != null) {
                    try {
                        version = safe(((Model) leaf).GetVersion());
                    } catch (Throwable ignored) {
                    }
                    leafAsm = leaf instanceof Assembly;
                    Transform3D world = cpath.GetTransform(true);
                    // The second argument lists item types to EXCLUDE from
                    // the outline; an empty list evaluates everything.
                    ModelItemTypes excludeNone = ModelItemTypes.create();
                    Outline3D outline = leaf.EvalOutline(world, excludeNone);
                    if (outline != null) {
                        Point3D p0 = outline.get(0);
                        Point3D p1 = outline.get(1);
                        bbox = new double[]{p0.get(0), p0.get(1), p0.get(2), p1.get(0), p1.get(1), p1.get(2)};
                    }
                }
            } catch (XToolkitCantOpen openErr) {
                modelName = "MISSING:" + modelName;
            } catch (Throwable t) {
                if (outlineErrors < 3) {
                    System.out.println("[DISCOVERY] outline eval failed for " + path
                            + ": " + t.getClass().getName() + " " + t.getMessage());
                    outlineErrors++;
                }
            }

            occCount++;
            StringBuilder rec = new StringBuilder();
            rec.append("    {\"path\": ").append(quote(path));
            rec.append(", \"feature_id\": ").append(featId);
            rec.append(", \"model\": ").append(quote(modelName));
            rec.append(", \"model_type\": ").append(quote(modelType));
            rec.append(", \"version\": ").append(quote(version));
            rec.append(", \"transform_ok\": ").append(transformOk);
            if (transformOk) {
                rec.append(", \"transform\": [");
                for (int r = 0; r < 4; r++) {
                    rec.append("[");
                    for (int c = 0; c < 4; c++) {
                        rec.append(fmt(matrix[r][c])).append(c < 3 ? "," : "");
                    }
                    rec.append("]").append(r < 3 ? "," : "");
                }
                rec.append("]");
            }
            if (bbox != null) {
                rec.append(", \"world_bbox\": [");
                for (int b = 0; b < 6; b++) rec.append(fmt(bbox[b])).append(b < 5 ? "," : "");
                rec.append("]");
            }
            rec.append("}");
            records.add(rec.toString());

            if (leafAsm && leaf != null) {
                walk(rootAsm, leaf, full, rootToken, records);
            }
        }
    }

    private static String joinPath(intseq ids) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < ids.getarraysize(); i++) {
            if (i > 0) sb.append("/");
            sb.append(ids.get(i));
        }
        return sb.toString();
    }

    private static String fmt(double v) {
        if (Math.abs(v) < 1e-12) v = 0;
        return String.format(java.util.Locale.US, "%.9g", v);
    }

    private static String safe(String s) {
        return s == null ? "" : s;
    }

    private static String quote(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (char ch : safe(s).toCharArray()) {
            if (ch == '"' || ch == '\\') sb.append('\\');
            sb.append(ch);
        }
        return sb.append('"').toString();
    }
}
