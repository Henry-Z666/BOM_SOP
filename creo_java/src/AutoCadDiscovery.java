import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcComponentFeat.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcGeometry.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcModelItem.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcSolid.*;
import com.ptc.pfc.pfcSelect.*;

import java.io.*;
import java.util.*;

/** Fact-only, asynchronous Creo Java Free OTK assembly extractor. */
public final class AutoCadDiscovery {
  private static String esc(String value) {
    if (value == null) return "";
    return value.replace("\\", "\\\\").replace("\"", "\\\"")
      .replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t");
  }
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
  private static String matrixJson(Transform3D value) throws jxthrowable {
    Matrix3D source = value.GetMatrix(); StringBuilder result = new StringBuilder("[");
    for (int row = 0; row < 4; row++) {
      if (row > 0) result.append(','); result.append('[');
      for (int col = 0; col < 4; col++) { if (col > 0) result.append(','); result.append(source.get(row, col)); }
      result.append(']');
    }
    return result.append(']').toString();
  }
  private static double[][] identity() { double[][] value = new double[4][4]; for (int i = 0; i < 4; i++) value[i][i] = 1.0; return value; }
  private static intseq appendPaths(intseq prefix, intseq suffix) throws jxthrowable {
    intseq result = intseq.create();
    for (int i = 0; i < prefix.getarraysize(); i++) result.append(prefix.get(i));
    for (int i = 0; suffix != null && i < suffix.getarraysize(); i++) result.append(suffix.get(i));
    return result;
  }
  private static intseq selectionPath(intseq basePath, Selection selection) throws jxthrowable {
    if (selection == null || selection.GetPath() == null) return appendPaths(basePath, null);
    return appendPaths(basePath, selection.GetPath().GetComponentIds());
  }
  private static double[] transformVector(double[][] pose, Vector3D local) throws jxthrowable {
    double[] source = new double[]{local.get(0), local.get(1), local.get(2)}; double[] result = new double[3];
    for (int col = 0; col < 3; col++) for (int row = 0; row < 3; row++) result[col] += source[row] * pose[row][col];
    double length = Math.sqrt(result[0]*result[0] + result[1]*result[1] + result[2]*result[2]);
    if (length < 1.0e-12) throw new IllegalArgumentException("degenerate reference direction");
    for (int i = 0; i < 3; i++) result[i] /= length;
    return result;
  }
  private static double[] transformPoint(double[][] pose, Point3D local) throws jxthrowable {
    double[] source = new double[]{local.get(0), local.get(1), local.get(2)}; double[] result = new double[3];
    for (int col = 0; col < 3; col++) {
      result[col] = pose[3][col];
      for (int row = 0; row < 3; row++) result[col] += source[row] * pose[row][col];
    }
    return result;
  }
  private static double[][] localOutline(Solid solid) throws jxthrowable {
    Outline3D outline = solid.GetGeomOutline();
    if (outline == null) return null;
    Point3D low = outline.get(0), high = outline.get(1);
    if (low == null || high == null) return null;
    return new double[][]{
      {low.get(0), low.get(1), low.get(2)},
      {high.get(0), high.get(1), high.get(2)}
    };
  }
  private static boolean pointWithinLocalOutline(Solid solid, Point3D candidate) throws jxthrowable {
    double[][] outline = localOutline(solid);
    if (outline == null || candidate == null) return false;
    double dx = outline[1][0] - outline[0][0];
    double dy = outline[1][1] - outline[0][1];
    double dz = outline[1][2] - outline[0][2];
    double tolerance = Math.max(1.0e-5, Math.sqrt(dx*dx + dy*dy + dz*dz) * 1.0e-6);
    for (int i = 0; i < 3; i++)
      if (candidate.get(i) < outline[0][i] - tolerance || candidate.get(i) > outline[1][i] + tolerance) return false;
    return true;
  }
  private static String rootBounds(double[][] local, double[][] toRoot) {
    if (local == null) return "{\"status\":\"unavailable\"}";
    double[] low = new double[]{Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY};
    double[] high = new double[]{Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
    for (int mask = 0; mask < 8; mask++) {
      double[] source = new double[]{
        local[(mask & 1) == 0 ? 0 : 1][0],
        local[(mask & 2) == 0 ? 0 : 1][1],
        local[(mask & 4) == 0 ? 0 : 1][2]
      };
      double[] root = new double[3];
      for (int col = 0; col < 3; col++) {
        root[col] = toRoot[3][col];
        for (int row = 0; row < 3; row++) root[col] += source[row] * toRoot[row][col];
        low[col] = Math.min(low[col], root[col]); high[col] = Math.max(high[col], root[col]);
      }
    }
    double dx = high[0] - low[0], dy = high[1] - low[1], dz = high[2] - low[2];
    return "{\"status\":\"available\",\"source\":\"solid_geom_outline/v1\",\"min\":"
      + array(low) + ",\"max\":" + array(high) + ",\"diagonal\":"
      + Math.sqrt(dx*dx + dy*dy + dz*dz) + "}";
  }
  private static String array(double[] value) { return "[" + value[0] + "," + value[1] + "," + value[2] + "]"; }
  private static String error(Throwable value) {
    String message = value.getMessage();
    return esc(value.getClass().getSimpleName() + (message == null ? "" : ": " + message));
  }
  private static Point3D surfaceAnchor(Surface surface) throws jxthrowable {
    Outline3D extent = surface.GetXYZExtents(); Point3D low = extent.get(0), high = extent.get(1); Point3D sample = Point3D.create();
    for (int i = 0; i < 3; i++) sample.set(i, (low.get(i) + high.get(i)) / 2.0);
    return surface.EvalClosestPointOnSurface(sample);
  }
  private static double[] physicalAnchor(Session session, Model model, double[][] toRoot) throws jxthrowable {
    if (model instanceof Assembly) {
      Features components = ((Assembly)model).ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
      List<ComponentFeat> ordered = new ArrayList<>();
      for (int i = 0; components != null && i < components.getarraysize(); i++) ordered.add((ComponentFeat)components.get(i));
      ordered.sort(Comparator.comparingInt(component -> {
        try { return component.GetId(); }
        catch (jxthrowable error) { throw new RuntimeException(error); }
      }));
      for (ComponentFeat component : ordered) {
        try {
          Model child = session.RetrieveModel(component.GetModelDescr());
          double[] anchor = physicalAnchor(session, child, multiply(matrix(component.GetPosition()), toRoot));
          if (anchor != null) return anchor;
        } catch (Throwable ignored) {}
      }
      return null;
    }
    if (!(model instanceof Solid)) return null;
    Solid solid = (Solid)model;
    try {
      SolidBody body = solid.GetDefaultBody(); Surfaces surfaces = body == null ? null : body.ListSurfaces();
      List<Surface> ordered = new ArrayList<>();
      for (int i = 0; surfaces != null && i < surfaces.getarraysize(); i++) ordered.add(surfaces.get(i));
      ordered.sort(Comparator.comparingInt(surface -> {
        try { return surface.GetId(); }
        catch (jxthrowable error) { throw new RuntimeException(error); }
      }));
      for (Surface surface : ordered) {
        try { return transformPoint(toRoot, surfaceAnchor(surface)); }
        catch (Throwable ignored) {}
      }
    } catch (Throwable ignored) {}
    try {
      ModelItems surfaces = solid.ListItems(ModelItemType.ITEM_SURFACE);
      List<Surface> ordered = new ArrayList<>();
      for (int i = 0; surfaces != null && i < surfaces.getarraysize(); i++) ordered.add((Surface)surfaces.get(i));
      ordered.sort(Comparator.comparingInt(surface -> {
        try { return surface.GetId(); }
        catch (jxthrowable error) { throw new RuntimeException(error); }
      }));
      for (Surface surface : ordered) {
        try {
          Point3D anchor = surfaceAnchor(surface);
          if (pointWithinLocalOutline(solid, anchor)) return transformPoint(toRoot, anchor);
        } catch (Throwable ignored) {}
      }
    } catch (Throwable ignored) {}
    return null;
  }
  private static String constraintType(ComponentConstraintType type) {
    int value = type.getValue();
    if (value == ComponentConstraintType._ASM_CONSTRAINT_MATE) return "MATE";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_MATE_OFF) return "MATE_OFF";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_ALIGN) return "ALIGN";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_ALIGN_OFF) return "ALIGN_OFF";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_INSERT) return "INSERT";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_ORIENT) return "ORIENT";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_CSYS) return "CSYS";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_TANGENT) return "TANGENT";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_FIX) return "FIX";
    if (value == ComponentConstraintType._ASM_CONSTRAINT_AUTO) return "AUTO";
    return "TYPE_" + value;
  }
  private static String itemType(ModelItemType type) {
    int value = type.getValue();
    if (value == ModelItemType._ITEM_SURFACE) return "SURFACE";
    if (value == ModelItemType._ITEM_AXIS) return "AXIS";
    if (value == ModelItemType._ITEM_EDGE) return "EDGE";
    if (value == ModelItemType._ITEM_CURVE) return "CURVE";
    if (value == ModelItemType._ITEM_POINT) return "POINT";
    if (value == ModelItemType._ITEM_COORD_SYS) return "COORD_SYS";
    return "ITEM_" + value;
  }
  private static String referenceGeometry(ModelItem item, Selection selection, double[][] toRoot) {
    try {
      Surface surface = null; String source = null;
      if (item instanceof Surface) { surface = (Surface)item; source = "surface"; }
      else if (item instanceof Axis) { surface = ((Axis)item).GetSurf(); source = "axis_surface"; }
      if (surface == null) return "{\"status\":\"unavailable\"}";
      // Prefer a sampled point on the selected surface.  Some Creo datum or
      // construction surfaces expose no finite XYZ extent; their coordinate
      // origin is still a valid point on the receiver plane, but downstream
      // arrow planning must separately prove a physical moving-solid anchor.
      Point3D anchor = null; Vector3D direction;
      try { anchor = surfaceAnchor(surface); } catch (Throwable ignored) {}
      if (surface instanceof TransformedSurface) {
        Transform3D surfaceCoordinates = ((TransformedSurface)surface).GetCoordSys();
        direction = surfaceCoordinates.GetZAxis();
        if (anchor == null) anchor = surfaceCoordinates.GetOrigin();
      } else {
        if (anchor == null) throw new IllegalStateException("surface has no evaluable point");
        UVParams parameters = surface.EvalParameters(anchor);
        direction = surface.Eval3DData(parameters).GetNormal();
      }
      return "{\"status\":\"available\",\"source\":\"" + source + "\",\"point_root\":"
        + array(transformPoint(toRoot, anchor)) + ",\"direction_root\":" + array(transformVector(toRoot, direction)) + "}";
    } catch (Throwable unavailable) {
      return "{\"status\":\"unavailable\",\"error\":\"" + error(unavailable) + "\"}";
    }
  }
  private static String referenceJson(Assembly root, intseq basePath, double[][] baseToRoot, Selection selection, DatumSide side) {
    if (selection == null) return "null";
    try {
      intseq selected = selection.GetPath() == null ? intseq.create() : selection.GetPath().GetComponentIds();
      intseq fullPath = appendPaths(basePath, selected); ModelItem item = selection.GetSelItem();
      double[][] referenceToRoot = baseToRoot;
      if (selected.getarraysize() > 0) {
        try { referenceToRoot = matrix(pfcAssembly.CreateComponentPath(root, fullPath).GetTransform(true)); }
        catch (Throwable prefixedUnavailable) {
          try {
            referenceToRoot = matrix(pfcAssembly.CreateComponentPath(root, selected).GetTransform(true));
            fullPath = appendPaths(intseq.create(), selected);
          } catch (Throwable rootUnavailable) {
            fullPath = appendPaths(basePath, null);
          }
        }
      }
      String selectionString = ""; try { selectionString = selection.GetSelectionString(); } catch (Throwable ignored) {}
      if (item == null) return "{\"occurrence_id\":\"" + esc(pathId(fullPath)) + "\",\"status\":\"no_model_item\"}";
      return "{\"occurrence_id\":\"" + esc(pathId(fullPath)) + "\",\"item_id\":" + item.GetId()
        + ",\"item_type_code\":" + item.GetType().getValue() + ",\"item_type\":\"" + itemType(item.GetType())
        + "\",\"item_name\":\"" + esc(item.GetName()) + "\",\"selection\":\"" + esc(selectionString)
        + "\",\"datum_side\":" + (side == null ? -1 : side.getValue()) + ",\"geometry\":"
        + referenceGeometry(item, selection, referenceToRoot) + "}";
    } catch (Throwable unavailable) {
      return "{\"status\":\"unavailable\",\"error\":\"" + error(unavailable) + "\"}";
    }
  }
  private static void append(StringBuilder target, String value) { if (target.length() > 0) target.append(','); target.append(value); }
  private static void discoverAssembly(Session session, Assembly root, Assembly current, intseq parentPath,
                                       double[][] parentToRoot, StringBuilder nodes, StringBuilder edges,
                                       java.util.Map<String, double[][]> outlineCache) throws jxthrowable {
    Features features = current.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
    for (int i = 0; i < features.getarraysize(); i++) {
      ComponentFeat component = (ComponentFeat)features.get(i); ModelDescriptor model = component.GetModelDescr();
      intseq componentPath = appendPath(parentPath, component.GetId());
      String path = pathId(componentPath);
      double[][] componentToRoot = multiply(matrix(component.GetPosition()), parentToRoot);
      Model child = null; double[][] outline = null;
      try {
        child = session.RetrieveModel(model);
        if (child instanceof Solid) {
          String outlineKey = model.GetFullName().toLowerCase(java.util.Locale.ROOT);
          if (outlineCache.containsKey(outlineKey)) outline = outlineCache.get(outlineKey);
          else { outline = localOutline((Solid)child); outlineCache.put(outlineKey, outline); }
        }
      } catch (Throwable unavailableChild) {
        System.err.println("[DISCOVERY-TRACE] child_scan_skipped=" + path);
      }
      double[] physicalAnchorRoot = null;
      try { physicalAnchorRoot = physicalAnchor(session, child, componentToRoot); } catch (Throwable ignored) {}
      append(nodes, "{\"id\":\"" + esc(path) + "\",\"occurrence_id\":\"" + esc(path)
        + "\",\"component_path\":" + intArray(componentPath) + ",\"parent_occurrence\":\"" + esc(pathId(parentPath))
        + "\",\"feature_id\":" + component.GetId() + ",\"part_no\":\"" + esc(model.GetFileName())
        + "\",\"model_name\":\"" + esc(model.GetFullName()) + "\",\"transform\":" + transform(componentToRoot)
        + ",\"bounds_root\":" + rootBounds(outline, componentToRoot)
        + ",\"physical_anchor_root\":" + (physicalAnchorRoot == null ? "null" : array(physicalAnchorRoot)) + "}");
      ComponentConstraints constraints = component.GetConstraints();
      for (int j = 0; constraints != null && j < constraints.getarraysize(); j++) {
        ComponentConstraint c = constraints.get(j);
        intseq assemblyReferencePath = selectionPath(parentPath, c.GetAssemblyReference());
        append(edges, "{\"id\":\"" + esc(path) + "_K_" + (j + 1) + "\",\"occurrences\":[\"" + esc(path)
          + "\",\"" + esc(pathId(assemblyReferencePath)) + "\"],\"type_code\":" + c.GetType().getValue()
          + ",\"type\":\"" + constraintType(c.GetType()) + "\",\"offset\":" + (c.GetOffset() == null ? "null" : c.GetOffset())
          + ",\"assembly_reference\":" + referenceJson(root, parentPath, parentToRoot, c.GetAssemblyReference(), c.GetAssemblyDatumSide())
          + ",\"component_reference\":" + referenceJson(root, componentPath, componentToRoot, c.GetComponentReference(), c.GetComponentDatumSide()) + "}");
      }
      if (child instanceof Assembly) discoverAssembly(session, root, (Assembly)child, componentPath, componentToRoot, nodes, edges, outlineCache);
    }
  }
  private static String intArray(intseq path) throws jxthrowable {
    StringBuilder result = new StringBuilder("[");
    for (int i = 0; i < path.getarraysize(); i++) { if (i > 0) result.append(','); result.append(path.get(i)); }
    return result.append(']').toString();
  }
  public static void main(String[] args) {
    if (args.length != 3 && args.length != 4) { System.err.println("Usage: AutoCadDiscovery <parametric.exe> <assembly-file> <output.json> [complete-marker]"); System.exit(2); }
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
      discoverAssembly(session, assembly, assembly, intseq.create(), identity(), nodes, edges,
        new java.util.HashMap<String, double[][]>());
      String json = "{\"schema_version\":\"creo-cad-graph/v3\",\"assembly_file\":\"" + esc(requestedFile)
        + "\",\"assembly_name\":\"" + esc(assembly.GetFileName()) + "\",\"assembly_version\":" + assembly.GetDescr().GetFileVersion()
        + ",\"root_coordinate_system\":\"root_asm\",\"root_occurrence\":\"ROOT\",\"default_view_matrix\":"
        + matrixJson(assembly.GetCurrentViewTransform()) + ",\"occurrences\":[" + nodes + "],\"constraints\":[" + edges + "]}";
      try (Writer out = new OutputStreamWriter(new FileOutputStream(args[2]), "UTF-8")) { out.write(json); }
      System.err.println("[DISCOVERY-TRACE] wrote=" + args[2]);
      connection.End();
      connection = null;
      if (args.length == 4) {
        try (Writer marker = new OutputStreamWriter(new FileOutputStream(args[3]), "UTF-8")) { marker.write("complete\n"); }
        System.err.println("[DISCOVERY-TRACE] complete=" + args[3]);
      }
    } catch (Throwable error) { error.printStackTrace(); try { if (connection != null) connection.End(); } catch (Throwable ignored) {} System.exit(1); }
  }
}
