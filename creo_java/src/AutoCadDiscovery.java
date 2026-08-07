import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcComponentFeat.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcSolid.*;
import com.ptc.pfc.pfcSelect.*;

import java.io.*;

/** Fact-only, asynchronous Creo Java Free OTK assembly extractor. */
public final class AutoCadDiscovery {
  private static String esc(String value) { return value == null ? "" : value.replace("\\", "\\\\").replace("\"", "\\\""); }
  private static String id(int value) { return "C_" + value; }
  private static String pathId(intseq path) throws jxthrowable {
    if (path.getarraysize() == 0) return "ROOT";
    StringBuilder result = new StringBuilder();
    for (int i = 0; i < path.getarraysize(); i++) { if (i > 0) result.append('/'); result.append(path.get(i)); }
    return result.toString();
  }
  private static intseq appendPath(intseq parent, int componentId) throws jxthrowable {
    intseq result = intseq.create(); for (int i = 0; i < parent.getarraysize(); i++) result.append(parent.get(i));
    result.append(componentId); return result;
  }
  private static String vec(Vector3D v) throws jxthrowable { return "[" + v.get(0) + "," + v.get(1) + "," + v.get(2) + "]"; }
  private static String point(Point3D p) throws jxthrowable { return "[" + p.get(0) + "," + p.get(1) + "," + p.get(2) + "]"; }
  private static String transform(Transform3D t) throws jxthrowable {
    return "{\"x_axis\":" + vec(t.GetXAxis()) + ",\"y_axis\":" + vec(t.GetYAxis()) + ",\"z_axis\":" + vec(t.GetZAxis()) + ",\"origin\":" + point(t.GetOrigin()) + "}";
  }
  private static double[][] matrix(Transform3D value) throws jxthrowable {
    Matrix3D source = value.GetMatrix(); double[][] result = new double[4][4];
    for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++) result[row][col] = source.get(row, col);
    return result;
  }
  private static double[][] multiply(double[][] left, double[][] right) {
    double[][] result = new double[4][4];
    for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++)
      for (int middle = 0; middle < 4; middle++) result[row][col] += left[row][middle] * right[middle][col];
    return result;
  }
  private static String transform(double[][] t) {
    return "{\"x_axis\":[" + t[0][0] + "," + t[0][1] + "," + t[0][2] + "],\"y_axis\":[" + t[1][0] + "," + t[1][1] + "," + t[1][2]
      + "],\"z_axis\":[" + t[2][0] + "," + t[2][1] + "," + t[2][2] + "],\"origin\":[" + t[3][0] + "," + t[3][1] + "," + t[3][2] + "]}";
  }
  private static double[][] identity() { double[][] value = new double[4][4]; for (int i = 0; i < 4; i++) value[i][i] = 1.0; return value; }
  private static String pathOccurrence(intseq parentPath, Selection selection) throws jxthrowable {
    if (selection == null || selection.GetPath() == null || selection.GetPath().GetComponentIds().getarraysize() == 0) return "ROOT";
    intseq selected = selection.GetPath().GetComponentIds(); intseq full = intseq.create();
    // Constraint selections inside a child ASM are local to that child.  The
    // graph stores root paths, so prefix the currently traversed ASM path.
    for (int i = 0; i < parentPath.getarraysize(); i++) full.append(parentPath.get(i));
    for (int i = 0; i < selected.getarraysize(); i++) full.append(selected.get(i));
    return pathId(full);
  }
  private static void append(StringBuilder target, String value) { if (target.length() > 0) target.append(','); target.append(value); }
  private static void discoverAssembly(Session session, Assembly root, Assembly current, intseq parentPath,
                                       double[][] parentToRoot, StringBuilder nodes, StringBuilder edges) throws jxthrowable {
    Features features = current.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
    for (int i = 0; i < features.getarraysize(); i++) {
      ComponentFeat component = (ComponentFeat)features.get(i); ModelDescriptor model = component.GetModelDescr();
      intseq componentPath = appendPath(parentPath, component.GetId());
      String path = pathId(componentPath);
      double[][] componentToRoot = multiply(matrix(component.GetPosition()), parentToRoot);
      append(nodes, "{\"id\":\"" + esc(path) + "\",\"occurrence_id\":\"" + esc(path)
        + "\",\"component_path\":" + intArray(componentPath) + ",\"parent_occurrence\":\"" + esc(pathId(parentPath))
        + "\",\"feature_id\":" + component.GetId() + ",\"part_no\":\"" + esc(model.GetFileName())
        + "\",\"model_name\":\"" + esc(model.GetFullName()) + "\",\"transform\":" + transform(componentToRoot) + "}");
      ComponentConstraints constraints = component.GetConstraints();
      for (int j = 0; constraints != null && j < constraints.getarraysize(); j++) {
        ComponentConstraint c = constraints.get(j);
        append(edges, "{\"id\":\"" + esc(path) + "_K_" + (j + 1) + "\",\"occurrences\":[\"" + esc(path)
          + "\",\"" + esc(pathOccurrence(parentPath, c.GetAssemblyReference())) + "\"],\"type\":\""
          + esc(c.GetType().toString()) + "\",\"offset\":" + (c.GetOffset() == null ? "null" : c.GetOffset()) + "}");
      }
      try {
        Model child = session.RetrieveModel(model);
        if (child instanceof Assembly) discoverAssembly(session, root, (Assembly)child, componentPath, componentToRoot, nodes, edges);
      } catch (Throwable unavailableChild) {
        System.err.println("[DISCOVERY-TRACE] child_scan_skipped=" + path);
      }
    }
  }
  private static String intArray(intseq path) throws jxthrowable {
    StringBuilder result = new StringBuilder("[");
    for (int i = 0; i < path.getarraysize(); i++) { if (i > 0) result.append(','); result.append(path.get(i)); }
    return result.append(']').toString();
  }
  public static void main(String[] args) {
    if (args.length != 3) { System.err.println("Usage: AutoCadDiscovery <parametric.exe> <assembly-file> <output.json>"); System.exit(2); }
    AsyncConnection connection = null;
    try {
      System.err.println("[DISCOVERY-TRACE] start");
      System.loadLibrary("pfcasyncmt");
      connection = pfcAsyncConnection.AsyncConnection_Start(args[0], null);
      System.err.println("[DISCOVERY-TRACE] connected");
      System.err.println("[DISCOVERY-TRACE] remote_comm=" + CIPRemoteApp.getComm());
      Session session = connection.GetSession();
      String requestedFile = new File(args[1]).getName();
      java.util.regex.Matcher version = java.util.regex.Pattern.compile("^(.*)\\.([0-9]+)$").matcher(requestedFile);
      String requestedName = version.matches() ? version.group(1) : requestedFile;
      ModelDescriptor descriptor = pfcModel.ModelDescriptor_Create(ModelType.MDL_ASSEMBLY, requestedName, null);
      if (version.matches()) descriptor.SetFileVersion(Integer.valueOf(version.group(2)));
      Model current = session.RetrieveModel(descriptor);
      if (current == null || current.GetType() != ModelType.MDL_ASSEMBLY) throw new IllegalStateException("Creo did not open the requested assembly.");
      Assembly assembly = (Assembly) current;
      if (!assembly.GetFileName().equalsIgnoreCase(requestedName)) throw new IllegalStateException("Opened unexpected assembly: " + assembly.GetFileName());
      if (version.matches() && !Integer.valueOf(version.group(2)).equals(assembly.GetDescr().GetFileVersion())) {
        throw new IllegalStateException("Opened unexpected assembly version: " + assembly.GetDescr().GetFileVersion());
      }
      System.err.println("[DISCOVERY-TRACE] model=" + assembly.GetFileName());
      Features features = assembly.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
      System.err.println("[DISCOVERY-TRACE] top_level_components=" + features.getarraysize());
      StringBuilder nodes = new StringBuilder(); StringBuilder edges = new StringBuilder();
      discoverAssembly(session, assembly, assembly, intseq.create(), identity(), nodes, edges);
      String json = "{\"schema_version\":\"creo-cad-graph/v2\",\"assembly_file\":\"" + esc(args[1]) + "\",\"root_occurrence\":\"ROOT\",\"occurrences\":[" + nodes + "],\"constraints\":[" + edges + "]}";
      try (Writer out = new OutputStreamWriter(new FileOutputStream(args[2]), "UTF-8")) { out.write(json); }
      System.err.println("[DISCOVERY-TRACE] wrote=" + args[2]);
      connection.End();
    } catch (Throwable error) { error.printStackTrace(); try { if (connection != null) connection.End(); } catch (Throwable ignored) {} System.exit(1); }
  }
}
