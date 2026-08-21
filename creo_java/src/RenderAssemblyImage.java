import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcComponentFeat.*;
import com.ptc.pfc.pfcDisplay.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcModelItem.*;
import com.ptc.pfc.pfcLayer.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcSelect.*;
import com.ptc.pfc.pfcSolid.*;
import com.ptc.pfc.pfcSimpRep.*;
import com.ptc.pfc.pfcWindow.*;

import java.io.File;
import java.awt.image.BufferedImage;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.imageio.ImageIO;

/** Creates a native Creo JPEG from an isolated model copy through J-Link async. */
public final class RenderAssemblyImage {
  private static final FeatureType[] AUXILIARY_FEATURE_TYPES = new FeatureType[] {
    FeatureType.FEATTYPE_WELDING_ROD, FeatureType.FEATTYPE_WELD_FILLET,
    FeatureType.FEATTYPE_WELD_GROOVE, FeatureType.FEATTYPE_WELD_PLUG_SLOT,
    FeatureType.FEATTYPE_WELD_SPOT, FeatureType.FEATTYPE_WELD_PROCESS,
    FeatureType.FEATTYPE_WELD_NOTCH, FeatureType.FEATTYPE_ASSY_WELD_NOTCH,
    FeatureType.FEATTYPE_HULL_WELD_NOTCH, FeatureType.FEATTYPE_WELD_COMBINE,
    FeatureType.FEATTYPE_COSMETIC, FeatureType.FEATTYPE_CABLE_COSMETIC,
    FeatureType.FEATTYPE_ANNOTATION, FeatureType.FEATTYPE_DATUM_PLANE,
    FeatureType.FEATTYPE_DATUM_AXIS, FeatureType.FEATTYPE_DATUM_POINT,
    FeatureType.FEATTYPE_DATUM_SURFACE, FeatureType.FEATTYPE_DATUM_QUILT,
    FeatureType.FEATTYPE_CURVE
  };

  private static double[] parseVectorTerm(String term, String prefix) {
    String[] values = term.substring(prefix.length()).split(":");
    if (values.length != 3) throw new IllegalArgumentException(prefix.replace(":", "") + " requires x:y:z");
    return new double[] { Double.parseDouble(values[0]), Double.parseDouble(values[1]), Double.parseDouble(values[2]) };
  }

  private static double dot(double[] left, double[] right) {
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
  }

  private static double[] cross(double[] left, double[] right) {
    return new double[] { left[1] * right[2] - left[2] * right[1],
      left[2] * right[0] - left[0] * right[2], left[0] * right[1] - left[1] * right[0] };
  }

  private static double[] normalize(double[] value, String name) {
    double length = Math.sqrt(dot(value, value));
    if (length < 1.0e-10) throw new IllegalArgumentException(name + " cannot be zero");
    return new double[] { value[0] / length, value[1] / length, value[2] / length };
  }

  /** Builds one absolute, right-handed root-ASM view transform. */
  private static Transform3D absoluteCameraTransform(double[] positionDirection, double[] upReference, double[] lookAtRoot) throws jxthrowable {
    double[] back = normalize(positionDirection, "ABS");
    double[] upRef = normalize(upReference, "UP");
    double[] rightRaw = cross(upRef, back);
    if (Math.sqrt(dot(rightRaw, rightRaw)) < 1.0e-8) throw new IllegalArgumentException("UP is parallel to ABS");
    double[] right = normalize(rightRaw, "camera right");
    double[] up = normalize(cross(back, right), "camera up");
    Matrix3D matrix = Matrix3D.create();
    for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++) matrix.set(row, col, row == col ? 1.0 : 0.0);
    // Creo transforms row vectors: view right/up/back are matrix columns.
    for (int row = 0; row < 3; row++) {
      matrix.set(row, 0, right[row]); matrix.set(row, 1, up[row]); matrix.set(row, 2, back[row]);
    }
    if (lookAtRoot != null) {
      // Creo multiplies root points as row vectors. Translate the selected
      // staged centre to the view origin without changing model geometry.
      matrix.set(3, 0, -dot(lookAtRoot, right));
      matrix.set(3, 1, -dot(lookAtRoot, up));
      matrix.set(3, 2, -dot(lookAtRoot, back));
    }
    return pfcBase.Transform3D_Create(matrix);
  }

  private static String rotationLog(Transform3D transform) throws jxthrowable {
    Matrix3D matrix = transform.GetMatrix(); StringBuilder result = new StringBuilder();
    for (int row = 0; row < 3; row++) for (int col = 0; col < 3; col++) result.append(String.format(" %.9f", matrix.get(row, col)));
    return result.toString();
  }

  private static String pointText(Point3D point) throws jxthrowable {
    return "[" + point.get(0) + "," + point.get(1) + "," + point.get(2) + "]";
  }

  /** Applies the locked absolute root-ASM camera used by formal rendering. */
  private static void applyCamera(Assembly model, String cameraSpec) throws jxthrowable {
    double[] absolute = null, upReference = null;
    for (String rawTerm : cameraSpec.split(",")) {
      String term = rawTerm.trim();
      if (term.regionMatches(true, 0, "ABS:", 0, 4)) absolute = parseVectorTerm(term, "ABS:");
      else if (term.regionMatches(true, 0, "UP:", 0, 3)) upReference = parseVectorTerm(term, "UP:");
      else if (!term.regionMatches(true, 0, "FIT_SELECTED:", 0, 13))
        throw new IllegalArgumentException("Unknown formal camera term: " + term);
    }
    if (absolute == null || upReference == null)
      throw new IllegalArgumentException("Formal camera requires ABS and UP");
    Transform3D transform = absoluteCameraTransform(absolute, upReference, null);
    model.SetCurrentViewTransform(transform);
    System.err.println("[RENDER] camera_absolute_position_direction=" + java.util.Arrays.toString(normalize(absolute, "ABS")));
    System.err.println("[RENDER] camera_absolute_up_reference=" + java.util.Arrays.toString(normalize(upReference, "UP")));
    System.err.println("[RENDER] camera_absolute_matrix=" + rotationLog(transform));
  }

  /**
   * Hides only Creo-native non-part feature classes in disposable model copies.
   * This is deliberately feature-type based: no visual colour/name heuristic is
   * permitted in the SOP pipeline.
   */
  private static int hideAuxiliaryFeatures(Session session, Model model, java.util.Set<String> visited) throws jxthrowable {
    String key = model.GetFileName();
    if (!visited.add(key)) return 0;
    int hidden = 0;
    if (model instanceof Solid) {
      Solid solid = (Solid)model;
      Layer auxiliaryLayer = null;
      for (FeatureType type : AUXILIARY_FEATURE_TYPES) {
        Features features = solid.ListFeaturesByType(Boolean.FALSE, type);
        for (int i = 0; features != null && i < features.getarraysize(); i++) {
          if (auxiliaryLayer == null) auxiliaryLayer = model.CreateLayer("AI_SOP_AUXILIARY");
          auxiliaryLayer.AddItem(features.get(i));
          hidden++;
        }
      }
      if (auxiliaryLayer != null) auxiliaryLayer.SetStatus(DisplayStatus.LAYER_BLANK);
    }
    if (model instanceof Assembly) {
      Features components = ((Assembly)model).ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
      for (int i = 0; i < components.getarraysize(); i++) {
        ComponentFeat component = (ComponentFeat)components.get(i);
        try {
          Model child = session.RetrieveModel(component.GetModelDescr());
          hidden += hideAuxiliaryFeatures(session, child, visited);
        } catch (Throwable unavailableChild) {
          // A suppressed/placeholder occurrence can have no retrievable child
          // model in this representation.  It cannot contribute graphics, so
          // retain the parent render and continue with the remaining parts.
          System.err.println("[RENDER] auxiliary_scan_skipped_component=" + component.GetId());
        }
      }
    }
    return hidden;
  }

  /**
   * Creo's descriptor name deliberately excludes the file-version suffix, but
   * the version itself must be set explicitly.  Omitting SetFileVersion lets
   * Creo silently substitute its latest in-session version, which invalidates
   * a batch manifest pinned to (for example) .asm.2.
   */
  private static ModelDescriptor assemblyDescriptor(String assemblyFile) throws jxthrowable {
    String fileName = new File(assemblyFile).getName();
    java.util.regex.Matcher matcher = java.util.regex.Pattern.compile("^(.*)\\.([0-9]+)$").matcher(fileName);
    String name = matcher.matches() ? matcher.group(1) : fileName;
    ModelDescriptor descriptor = pfcModel.ModelDescriptor_Create(ModelType.MDL_ASSEMBLY, name, null);
    if (matcher.matches()) descriptor.SetFileVersion(Integer.valueOf(matcher.group(2)));
    return descriptor;
  }

  /** Paths use the stable root-occurrence syntax "51/4888;80/12". */
  private static java.util.List<intseq> parseOccurrencePaths(String encoded) throws jxthrowable {
    java.util.List<intseq> result = new java.util.ArrayList<intseq>();
    if (encoded == null || encoded.trim().isEmpty()) return result;
    for (String rawPath : encoded.split(";")) {
      String trimmed = rawPath.trim(); if (trimmed.isEmpty()) continue;
      intseq path = intseq.create();
      for (String rawId : trimmed.split("/")) {
        String id = rawId.trim();
        if (id.regionMatches(true, 0, "C_", 0, 2)) id = id.substring(2);
        path.append(Integer.parseInt(id));
      }
      if (path.getarraysize() == 0) throw new IllegalArgumentException("Empty occurrence path: " + rawPath);
      result.add(path);
    }
    return result;
  }

  private static String occurrencePathId(intseq path) throws jxthrowable {
    StringBuilder value = new StringBuilder();
    for (int i = 0; i < path.getarraysize(); i++) {
      if (i > 0) value.append('/'); value.append(path.get(i));
    }
    return value.toString();
  }

  private static Double nativeSelectedFitLevel(String cameraSpec) {
    for (String rawTerm : cameraSpec.split(",")) {
      String term = rawTerm.trim();
      if (!term.regionMatches(true, 0, "FIT_SELECTED:", 0, 13)) continue;
      double level = Double.parseDouble(term.substring(13));
      if (!Double.isFinite(level) || level < 0.1 || level > 2.0)
        throw new IllegalArgumentException("FIT_SELECTED level must be between 0.1 and 2.0");
      return level;
    }
    return null;
  }

  /** Uses Creo's selected-object bounding-box fit; no absolute PAN/ZOOM. */
  private static void applyNativeSelectedFit(Session session, Window window, Assembly assembly,
      java.util.List<intseq> occurrencePaths, double level) throws jxthrowable {
    if (session.UIGetCommand("ProCmdZoomIntoOutline") == null)
      throw new IllegalStateException("Creo command is unavailable: ProCmdZoomIntoOutline");
    SelectionBuffer buffer = session.GetCurrentSelectionBuffer();
    String previousLevel = session.GetConfigOption("zoom_to_selected_level");
    try {
      session.SetConfigOption("zoom_to_selected_level", String.format(java.util.Locale.ROOT, "%.6f", level));
      buffer.Clear();
      for (intseq ids : occurrencePaths) {
        ComponentPath path = pfcAssembly.CreateComponentPath(assembly, ids);
        buffer.AddSelection(pfcSelect.CreateComponentSelection(path));
      }
      Selections selected = buffer.GetContents();
      int count = selected == null ? 0 : selected.getarraysize();
      if (count != occurrencePaths.size())
        throw new IllegalStateException("Selection buffer mismatch: expected=" + occurrencePaths.size() + " actual=" + count);
      window.Repaint();
      session.FlushCurrentWindow();
      session.RunMacro("~ Command `ProCmdZoomIntoOutline`");
      window.Repaint();
      session.FlushCurrentWindow();
      ScreenTransform screen = window.GetScreenTransform();
      System.err.println("[PERSISTENT] native_selected_fit=ProCmdZoomIntoOutline selected=" + count
          + " level=" + level + " zoom=" + screen.GetZoom()
          + " pan=[" + screen.GetPanX() + "," + screen.GetPanY() + "]");
    } finally {
      try { buffer.Clear(); } catch (Throwable ignored) {}
      try {
        session.SetConfigOption("zoom_to_selected_level",
            previousLevel == null || previousLevel.trim().isEmpty() ? "1" : previousLevel);
      } catch (Throwable ignored) {}
      window.Repaint();
      session.FlushCurrentWindow();
    }
  }

  /** Encodes only locked occurrence IDs and root-coordinate points; no paths leave the run. */
  private static java.util.Map<String,double[]> parsePlannedAnchors(String encoded) {
    java.util.Map<String,double[]> result = new java.util.HashMap<String,double[]>();
    if (encoded == null || encoded.trim().isEmpty()) return result;
    for (String raw : encoded.split(";")) {
      int separator = raw.indexOf('=');
      if (separator <= 0 || separator == raw.length() - 1)
        throw new IllegalArgumentException("Malformed planned arrow anchor");
      String occurrence = raw.substring(0, separator).trim();
      String[] coordinates = raw.substring(separator + 1).split(":", -1);
      if (coordinates.length != 3 || result.containsKey(occurrence))
        throw new IllegalArgumentException("Invalid planned arrow anchor occurrence=" + occurrence);
      double[] point = new double[3];
      for (int index = 0; index < 3; index++) {
        point[index] = Double.parseDouble(coordinates[index]);
        if (!Double.isFinite(point[index]))
          throw new IllegalArgumentException("Non-finite planned arrow anchor occurrence=" + occurrence);
      }
      result.put(occurrence, point);
    }
    return result;
  }

  private static double[][] identityMatrix() { double[][] value = new double[4][4]; for (int i = 0; i < 4; i++) value[i][i] = 1.0; return value; }
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
  private static String rotationLog(double[][] value) {
    StringBuilder result = new StringBuilder();
    for (int row = 0; row < 3; row++) for (int col = 0; col < 3; col++) result.append(String.format(" %.9f", value[row][col]));
    return result.toString();
  }
  private static boolean sameRotation(Transform3D first, Transform3D second) throws jxthrowable {
    // Creo may normalize an unchanged matrix by a few ulps after SetTransform.
    // Compare numerically, not by formatted strings; a real rotation remains a
    // hard failure at the 1e-7 tolerance.
    double[][] a = matrix(first), b = matrix(second);
    for (int row = 0; row < 3; row++) for (int col = 0; col < 3; col++) {
      if (Math.abs(a[row][col] - b[row][col]) > 1.0e-7) return false;
    }
    return true;
  }
  private static final class ResolvedComponent {
    final ComponentFeat feature; final double[][] parentToRoot;
    ResolvedComponent(ComponentFeat feature, double[][] parentToRoot) { this.feature = feature; this.parentToRoot = parentToRoot; }
  }
  private static final class DynamicPose {
    final String occurrenceId; final intseq ids; final Transform3D pose; final boolean componentPathApi;
    DynamicPose(String occurrenceId, intseq ids, Transform3D pose, boolean componentPathApi) {
      this.occurrenceId = occurrenceId; this.ids = ids; this.pose = pose; this.componentPathApi = componentPathApi;
    }
  }
  /** Fallback for Creo builds where ComponentPath.GetTransform rejects a live path. */
  private static ResolvedComponent resolveComponent(Session session, Assembly root, intseq ids) throws jxthrowable {
    Assembly current = root; double[][] currentToRoot = identityMatrix();
    for (int depth = 0; depth < ids.getarraysize(); depth++) {
      int wanted = ids.get(depth); ComponentFeat found = null;
      Features features = current.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
      for (int index = 0; index < features.getarraysize(); index++) {
        ComponentFeat candidate = (ComponentFeat)features.get(index);
        if (candidate.GetId() == wanted) { found = candidate; break; }
      }
      if (found == null) throw new IllegalArgumentException("Occurrence path not found: " + occurrencePathId(ids));
      if (depth == ids.getarraysize() - 1) return new ResolvedComponent(found, currentToRoot);
      Model child = session.RetrieveModel(found.GetModelDescr());
      if (!(child instanceof Assembly)) throw new IllegalArgumentException("Occurrence path crosses a non-assembly: " + occurrencePathId(ids));
      currentToRoot = multiply(matrix(found.GetPosition()), currentToRoot); current = (Assembly)child;
    }
    throw new IllegalArgumentException("Empty occurrence path");
  }
  private static void translateResolved(Session session, Assembly root, intseq ids, double dx, double dy, double dz) throws jxthrowable {
    // Prefer the official ComponentPath API.  Creo 13 Student can reject
    // GetTransform for some live paths; retain a mathematically equivalent
    // ComponentFeat fallback, still resolved from the same root path.
    try {
      ComponentPath path = pfcAssembly.CreateComponentPath(root, ids);
      Transform3D pose = path.GetTransform(true); String before = rotationLog(pose); Point3D origin = pose.GetOrigin();
      origin.set(0, origin.get(0) + dx); origin.set(1, origin.get(1) + dy); origin.set(2, origin.get(2) + dz);
      pose.SetOrigin(origin); path.SetTransform(true, pose); Transform3D after = path.GetTransform(true);
      if (!sameRotation(pose, after)) throw new IllegalStateException("Rotation changed for occurrence " + occurrencePathId(ids) + " before=" + before + " after=" + rotationLog(after));
      System.err.println("[RENDER] transform_audit occurrence=" + occurrencePathId(ids) + " api=ComponentPath rotation=" + rotationLog(after) + " origin=" + pointText(after.GetOrigin()));
      return;
    } catch (jxthrowable unsupportedPathTransform) {
      System.err.println("[RENDER] component_path_transform_fallback occurrence=" + occurrencePathId(ids));
    }
    ResolvedComponent resolved = resolveComponent(session, root, ids);
    Transform3D local = resolved.feature.GetPosition(); String before = rotationLog(local);
    // Root vector -> parent coordinates.  For row vectors this is deltaRoot * R^T.
    double[] rootDelta = new double[] { dx, dy, dz }; double[] parentDelta = new double[3];
    for (int col = 0; col < 3; col++) for (int row = 0; row < 3; row++) parentDelta[col] += rootDelta[row] * resolved.parentToRoot[row][col];
    Point3D origin = local.GetOrigin();
    origin.set(0, origin.get(0) + parentDelta[0]); origin.set(1, origin.get(1) + parentDelta[1]); origin.set(2, origin.get(2) + parentDelta[2]);
    local.SetOrigin(origin); resolved.feature.SetPosition(local);
    Transform3D after = resolved.feature.GetPosition();
    if (!sameRotation(local, after)) throw new IllegalStateException("Rotation changed for occurrence " + occurrencePathId(ids) + " before=" + before + " after=" + rotationLog(after));
    double[][] rootPose = multiply(matrix(after), resolved.parentToRoot);
    System.err.println("[RENDER] transform_audit occurrence=" + occurrencePathId(ids) + " api=ComponentFeatPathFallback rotation=" + rotationLog(rootPose)
      + " origin=[" + rootPose[3][0] + "," + rootPose[3][1] + "," + rootPose[3][2] + "]");
  }
  private static java.util.List<DynamicPose> captureDynamicPoses(Session session, Assembly root,
      java.util.List<intseq> occurrencePaths) throws jxthrowable {
    java.util.List<DynamicPose> result = new java.util.ArrayList<DynamicPose>();
    for (intseq ids : occurrencePaths) {
      String occurrenceId = occurrencePathId(ids);
      try {
        Transform3D pose = pfcAssembly.CreateComponentPath(root, ids).GetTransform(true);
        result.add(new DynamicPose(occurrenceId, ids, pose, true));
      } catch (jxthrowable unsupportedPathTransform) {
        result.add(new DynamicPose(occurrenceId, ids,
            resolveComponent(session, root, ids).feature.GetPosition(), false));
      }
    }
    return result;
  }
  private static void restoreDynamicPoses(Session session, Assembly root,
      java.util.List<DynamicPose> poses, String excludedTarget) throws jxthrowable {
    for (DynamicPose item : poses) {
      if (item.occurrenceId.equals(excludedTarget)
          || item.occurrenceId.startsWith(excludedTarget + "/")) continue;
      if (item.componentPathApi)
        pfcAssembly.CreateComponentPath(root, item.ids).SetTransform(true, item.pose);
      else
        resolveComponent(session, root, item.ids).feature.SetPosition(item.pose);
    }
  }
  private static boolean stageRelated(String path, java.util.Set<String> desired) {
    for (String keep : desired) if (keep.equals(path) || keep.startsWith(path + "/") || path.startsWith(keep + "/")) return true;
    return false;
  }
  private static void addStageExclusions(Session session, Assembly assembly, String prefix, java.util.Set<String> desired, SimpRepItems items) throws jxthrowable {
    Features features = assembly.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
    for (int i = 0; i < features.getarraysize(); i++) {
      ComponentFeat component = (ComponentFeat)features.get(i); String path = prefix.isEmpty() ? "" + component.GetId() : prefix + "/" + component.GetId();
      if (!stageRelated(path, desired)) {
        intseq ids = parseOccurrencePaths(path).get(0);
        SimpRepItem item = pfcSimpRep.SimpRepItem_Create(pfcSimpRep.SimpRepCompItemPath_Create(ids));
        item.SetAction(pfcSimpRep.SimpRepExclude_Create()); items.append(item); continue;
      }
      try { Model child = session.RetrieveModel(component.GetModelDescr()); if (child instanceof Assembly) addStageExclusions(session, (Assembly)child, path, desired, items); } catch (Throwable ignored) {}
    }
  }

  private static void addVisibilityExclusions(Session session, Assembly assembly, String prefix,
      java.util.Set<String> desired, String excludedTarget, SimpRepItems items) throws jxthrowable {
    Features features = assembly.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
    for (int i = 0; i < features.getarraysize(); i++) {
      ComponentFeat component = (ComponentFeat)features.get(i);
      String path = prefix.isEmpty() ? "" + component.GetId() : prefix + "/" + component.GetId();
      if (path.equals(excludedTarget) || !stageRelated(path, desired)) {
        intseq ids = parseOccurrencePaths(path).get(0);
        SimpRepItem item = pfcSimpRep.SimpRepItem_Create(pfcSimpRep.SimpRepCompItemPath_Create(ids));
        item.SetAction(pfcSimpRep.SimpRepExclude_Create()); items.append(item); continue;
      }
      try {
        Model child = session.RetrieveModel(component.GetModelDescr());
        if (child instanceof Assembly)
          addVisibilityExclusions(session, (Assembly)child, path, desired, excludedTarget, items);
      } catch (Throwable ignored) {}
    }
  }

  private static void activateVisibilityRep(Session session, Assembly assembly,
      java.util.Set<String> desired, String excludedTarget, String name) throws jxthrowable {
    CreateNewSimpRepInstructions instructions = pfcSimpRep.CreateNewSimpRepInstructions_Create(name);
    instructions.SetIsTemporary(true);
    instructions.SetDefaultAction(SimpRepActionType.SIMPREP_INCLUDE);
    SimpRepItems items = SimpRepItems.create();
    addVisibilityExclusions(session, assembly, "", desired, excludedTarget, items);
    instructions.SetItems(items);
    assembly.ActivateSimpRep(assembly.CreateSimpRep(instructions));
  }

  private static LinkedHashMap<String,Integer> parseVisibilityLabels(String encoded) {
    LinkedHashMap<String,Integer> result = new LinkedHashMap<String,Integer>();
    for (String raw : encoded.split(";")) {
      String item = raw.trim(); if (item.isEmpty()) continue;
      int separator = item.lastIndexOf('=');
      if (separator <= 0 || separator == item.length() - 1) throw new IllegalArgumentException("Invalid visibility label: " + item);
      String occurrence = item.substring(0, separator).trim();
      int label = Integer.parseInt(item.substring(separator + 1).trim());
      if (label <= 0 || label > 0xFFFFFF || result.put(occurrence, label) != null)
        throw new IllegalArgumentException("Invalid or duplicate visibility label: " + item);
    }
    if (result.isEmpty()) throw new IllegalArgumentException("No visibility labels supplied");
    return result;
  }

  private static void exportVisibilityBitmap(Window window, Session session, Path output) throws Exception {
    window.Repaint(); session.FlushCurrentWindow();
    BitmapImageExportInstructions instructions = pfcWindow.BitmapImageExportInstructions_Create(8.0, 8.0);
    instructions.SetDotsPerInch(DotsPerInch.RASTERDPI_100);
    instructions.SetImageDepth(RasterDepth.RASTERDEPTH_24);
    window.ExportRasterImage(output.toString(), instructions);
    if (!Files.isRegularFile(output) || Files.size(output) == 0L)
      throw new IllegalStateException("Creo did not export visibility bitmap: " + output);
  }

  private static int visibilityPixelDelta(int left, int right) {
    int red = Math.abs(((left >> 16) & 255) - ((right >> 16) & 255));
    int green = Math.abs(((left >> 8) & 255) - ((right >> 8) & 255));
    int blue = Math.abs((left & 255) - (right & 255));
    return Math.max(red, Math.max(green, blue));
  }

  private static int medianColor(java.util.List<BufferedImage> images,
      java.util.List<Integer> pool, int x, int y) {
    int[] red = new int[pool.size()], green = new int[pool.size()], blue = new int[pool.size()];
    for (int offset = 0; offset < pool.size(); offset++) {
      int pixel = images.get(pool.get(offset).intValue()).getRGB(x, y);
      red[offset] = (pixel >> 16) & 255; green[offset] = (pixel >> 8) & 255; blue[offset] = pixel & 255;
    }
    java.util.Arrays.sort(red); java.util.Arrays.sort(green); java.util.Arrays.sort(blue);
    int middle = pool.size() / 2;
    int r = red[middle], g = green[middle], b = blue[middle];
    if (pool.size() % 2 == 0) {
      r = (r + red[middle - 1]) / 2; g = (g + green[middle - 1]) / 2; b = (b + blue[middle - 1]) / 2;
    }
    return (r << 16) | (g << 8) | b;
  }

  private static void assignVisibilityGroup(BufferedImage mask,
      java.util.List<Map.Entry<String,Integer>> targets,
      java.util.List<BufferedImage> excludedImages, java.util.List<Integer> group,
      java.util.List<Integer> oppositeGroup, int[] assignedPixels) {
    for (int y = 0; y < mask.getHeight(); y++) for (int x = 0; x < mask.getWidth(); x++) {
      if ((mask.getRGB(x, y) & 0xFFFFFF) != 0) continue;
      int bestIndex = -1, bestDelta = -1, secondDelta = -1;
      for (Integer boxedIndex : group) {
        int index = boxedIndex.intValue();
        java.util.List<Integer> referencePool = new java.util.ArrayList<Integer>();
        if (group.size() > 1) {
          for (Integer candidate : group) if (candidate.intValue() != index) referencePool.add(candidate);
        } else {
          referencePool.addAll(oppositeGroup);
        }
        if (referencePool.isEmpty()) continue;
        int reference = medianColor(excludedImages, referencePool, x, y);
        int delta = visibilityPixelDelta(excludedImages.get(index).getRGB(x, y), reference);
        if (delta > bestDelta) {
          secondDelta = bestDelta; bestDelta = delta; bestIndex = index;
        } else if (delta > secondDelta) {
          secondDelta = delta;
        }
      }
      if (bestIndex >= 0 && bestDelta >= 18 && bestDelta - Math.max(0, secondDelta) >= 8) {
        mask.setRGB(x, y, targets.get(bestIndex).getValue().intValue());
        assignedPixels[bestIndex]++;
      }
    }
  }

  private static void writeVisibilityMask(Path basePath, Map<String,Path> targetExcluded,
      LinkedHashMap<String,Integer> labels, java.util.Set<String> movingTargets,
      java.util.Set<String> receiverTargets, Path outputPng, boolean requireVisibleTarget) throws Exception {
    BufferedImage base = ImageIO.read(basePath.toFile());
    if (base == null) throw new IllegalStateException("Cannot read visibility base bitmap: " + basePath);
    BufferedImage mask = new BufferedImage(base.getWidth(), base.getHeight(), BufferedImage.TYPE_INT_RGB);
    java.util.List<Map.Entry<String,Integer>> targets = new java.util.ArrayList<Map.Entry<String,Integer>>(labels.entrySet());
    java.util.List<BufferedImage> excludedImages = new java.util.ArrayList<BufferedImage>();
    java.util.List<Integer> movingGroup = new java.util.ArrayList<Integer>();
    java.util.List<Integer> receiverGroup = new java.util.ArrayList<Integer>();
    int[] assignedPixels = new int[targets.size()];
    for (int index = 0; index < targets.size(); index++) {
      Map.Entry<String,Integer> target = targets.get(index);
      BufferedImage selected = ImageIO.read(targetExcluded.get(target.getKey()).toFile());
      if (selected == null || selected.getWidth() != base.getWidth() || selected.getHeight() != base.getHeight())
        throw new IllegalStateException("Visibility exclusion bitmap is invalid for " + target.getKey());
      excludedImages.add(selected);
      if (movingTargets.contains(target.getKey())) movingGroup.add(Integer.valueOf(index));
      else if (receiverTargets.contains(target.getKey())) receiverGroup.add(Integer.valueOf(index));
      else throw new IllegalStateException("Visibility target has no moving/receiver role: " + target.getKey());
    }
    // Compare each excluded occurrence with frames where that same occurrence
    // is still present.  Moving and receiver groups are assigned separately,
    // which rejects the common simp-rep repaint delta while preserving tiny parts.
    assignVisibilityGroup(mask, targets, excludedImages, movingGroup, receiverGroup, assignedPixels);
    assignVisibilityGroup(mask, targets, excludedImages, receiverGroup, movingGroup, assignedPixels);
    int totalChangedPixels = 0;
    for (int index = 0; index < targets.size(); index++) {
      totalChangedPixels += assignedPixels[index];
      System.err.println("[VISIBILITY] target=" + targets.get(index).getKey()
          + " assigned_pixels=" + assignedPixels[index]);
    }
    if (requireVisibleTarget && totalChangedPixels == 0)
      throw new IllegalStateException("VISIBILITY_RASTER_EMPTY: isolated component exclusion produced zero target pixels");
    Files.createDirectories(outputPng.getParent());
    if (!ImageIO.write(mask, "png", outputPng.toFile())) throw new IllegalStateException("PNG writer is unavailable");
    System.err.println("[VISIBILITY] wrote=" + outputPng + " assigned_pixels=" + totalChangedPixels);
  }

  private static void renderVisibilityScene(Session session, String assemblyFile, Path outputPng,
      String movingPaths, String receiverPaths, double dx, double dy, double dz, String desiredPaths,
      String cameraSpec, String focusPaths, LinkedHashMap<String,Integer> labels,
      boolean requireVisibleTarget) throws Throwable {
    Window window = null; boolean completed = false;
    Path rawRoot = outputPng.resolveSibling(outputPng.getFileName().toString() + ".raw");
    try {
      Files.createDirectories(rawRoot);
      ModelDescriptor descriptor = assemblyDescriptor(assemblyFile);
      window = session.OpenFile(descriptor); window.Activate(); Model model = window.GetModel();
      String requestedBase = new File(assemblyFile).getName().replaceFirst("\\.[0-9]+$", "");
      if (!model.GetFileName().equalsIgnoreCase(requestedBase)) throw new IllegalStateException("Opened unexpected assembly");
      Integer requestedVersion = descriptor.GetFileVersion(), actualVersion = model.GetDescr().GetFileVersion();
      if (requestedVersion != null && !requestedVersion.equals(actualVersion)) throw new IllegalStateException("Opened unexpected assembly version");
      Assembly assembly = (Assembly)model; hideAuxiliaryFeatures(session, model, new java.util.HashSet<String>());
      java.util.Set<String> desired = new java.util.HashSet<String>();
      for (String raw : desiredPaths.split(";")) if (!raw.trim().isEmpty()) desired.add(raw.trim());
      java.util.Set<String> movingTargets = new java.util.HashSet<String>();
      for (String raw : movingPaths.split(";")) if (!raw.trim().isEmpty()) movingTargets.add(raw.trim());
      java.util.Set<String> receiverTargets = new java.util.HashSet<String>();
      for (String raw : receiverPaths.split(";")) if (!raw.trim().isEmpty()) receiverTargets.add(raw.trim());
      for (String target : labels.keySet()) if (!stageRelated(target, desired)) throw new IllegalArgumentException("Visibility target is outside the scene: " + target);
      activateVisibilityRep(session, assembly, desired, "", "AI_SOP_VIS_BASE");
      if (!assembly.GetDynamicPositioning()) assembly.SetDynamicPositioning(true);
      java.util.List<intseq> movingOccurrencePaths = parseOccurrencePaths(movingPaths);
      for (intseq ids : movingOccurrencePaths) translateResolved(session, assembly, ids, dx, dy, dz);
      java.util.List<DynamicPose> movingPoses = captureDynamicPoses(session, assembly, movingOccurrencePaths);
      applyCamera(assembly, cameraSpec);
      Double fit = nativeSelectedFitLevel(cameraSpec);
      if (fit == null) throw new IllegalArgumentException("Visibility audit requires native selected fit");
      applyNativeSelectedFit(session, window, assembly, parseOccurrencePaths(focusPaths), fit);
      ScreenTransform liveScreen = window.GetScreenTransform();
      ScreenTransform fittedScreen = pfcBase.ScreenTransform_Create(
          liveScreen.GetPanX(), liveScreen.GetPanY(), liveScreen.GetZoom());
      Path base = rawRoot.resolve("base.bmp"); exportVisibilityBitmap(window, session, base);
      LinkedHashMap<String,Path> targetExcluded = new LinkedHashMap<String,Path>(); int index = 0;
      for (String occurrence : labels.keySet()) {
        Path target = rawRoot.resolve(String.format(java.util.Locale.ROOT, "target-%03d.bmp", ++index));
        activateVisibilityRep(session, assembly, desired, occurrence,
            String.format(java.util.Locale.ROOT, "AI_SOP_VIS_%03d", index));
        if (!assembly.GetDynamicPositioning()) assembly.SetDynamicPositioning(true);
        restoreDynamicPoses(session, assembly, movingPoses, occurrence);
        applyCamera(assembly, cameraSpec);
        window.Repaint();
        session.FlushCurrentWindow();
        window.SetScreenTransform(fittedScreen);
        exportVisibilityBitmap(window, session, target);
        targetExcluded.put(occurrence, target);
      }
      writeVisibilityMask(base, targetExcluded, labels, movingTargets, receiverTargets,
          outputPng, requireVisibleTarget); completed = true;
    } finally {
      if (window != null) try { window.Close(); } catch (Throwable ignored) {}
      try { session.EraseUndisplayedModels(); } catch (Throwable ignored) {}
      if (completed && Files.isDirectory(rawRoot)) try (java.util.stream.Stream<Path> paths = Files.walk(rawRoot)) {
        paths.sorted(java.util.Comparator.reverseOrder()).forEach(path -> { try { Files.deleteIfExists(path); } catch (Exception ignored) {} });
      }
    }
  }

  static void renderVisibilityEvidenceInSession(Session session, String assemblyFile,
      String outputDirectory, String taskId, String movingPaths, String receiverPaths,
      double dx, double dy, double dz, String visiblePaths, String fixed123Camera,
      String fixed456Camera, String focusPaths, String encodedLabels) throws Throwable {
    LinkedHashMap<String,Integer> labels = parseVisibilityLabels(encodedLabels);
    String isolatedPaths = movingPaths + ";" + receiverPaths;
    for (String cameraId : new String[] { "fixed_123", "fixed_456" }) {
      String camera = cameraId.equals("fixed_123") ? fixed123Camera : fixed456Camera;
      renderVisibilityScene(session, assemblyFile, Path.of(outputDirectory, taskId + "." + cameraId + ".isolated.png"), movingPaths, receiverPaths, dx, dy, dz, isolatedPaths, camera, focusPaths, labels, true);
      renderVisibilityScene(session, assemblyFile, Path.of(outputDirectory, taskId + "." + cameraId + ".staged.png"), movingPaths, receiverPaths, dx, dy, dz, visiblePaths, camera, focusPaths, labels, false);
    }
  }

  /**
   * Renders one formal stage inside an already-started Creo session. The model
   * lifecycle stays deliberately per-job: close its window and erase all
   * undisplayed models in the finally block so a later job cannot inherit a
   * temporary simplified representation, dynamic transform, camera, or arrow.
   */
  /** Formal Agent path with constraint-backed same-CAD-point anchors. */
  static void renderInSession(Session session, String assemblyFile, String outputJpeg,
      String occurrencePaths, double dx, double dy, double dz, String visiblePaths,
      String cameraSpec, String arrowAuditJson, String focusPaths,
      String plannedAnchors, boolean drawNativeArrow) throws Throwable {
    Window window = null;
    DisplayList3D arrowDisplay = null;
    try {
      ModelDescriptor descriptor = assemblyDescriptor(assemblyFile);
      window = session.OpenFile(descriptor);
      window.Activate();
      Model model = window.GetModel();
      String requestedBase = new File(assemblyFile).getName().replaceFirst("\\.[0-9]+$", "");
      if (!model.GetFileName().equalsIgnoreCase(requestedBase)) {
        throw new IllegalStateException("Opened unexpected assembly: requested=" + requestedBase + " actual=" + model.GetFileName());
      }
      Integer requestedVersion = descriptor.GetFileVersion(), actualVersion = model.GetDescr().GetFileVersion();
      if (requestedVersion != null && !requestedVersion.equals(actualVersion)) {
        throw new IllegalStateException("Opened unexpected assembly version: requested=" + requestedVersion + " actual=" + actualVersion);
      }
      System.err.println("[PERSISTENT] authoritative_assembly=" + model.GetFileName() + "." + actualVersion);
      int hiddenAuxiliaryFeatures = hideAuxiliaryFeatures(session, model, new java.util.HashSet<String>());
      System.err.println("[PERSISTENT] native_auxiliary_features_hidden=" + hiddenAuxiliaryFeatures);

      java.util.List<intseq> requestedOccurrences = parseOccurrencePaths(occurrencePaths);
      if (requestedOccurrences.isEmpty()) throw new IllegalArgumentException("No moving occurrence paths supplied");
      java.util.Set<String> desired = new java.util.HashSet<String>();
      for (String rawPath : visiblePaths.split(";")) if (!rawPath.trim().startsWith("!")) desired.add(rawPath.trim());
      java.util.List<intseq> visibleOccurrencePaths = parseOccurrencePaths(String.join(";", desired));
      if (visibleOccurrencePaths.isEmpty()) throw new IllegalArgumentException("No visible occurrence paths supplied");
      java.util.List<intseq> focusOccurrencePaths = parseOccurrencePaths(focusPaths);
      if (focusOccurrencePaths.isEmpty()) throw new IllegalArgumentException("No installation focus occurrence paths supplied");
      Assembly assembly = (Assembly)model;
      CreateNewSimpRepInstructions stage = pfcSimpRep.CreateNewSimpRepInstructions_Create("AI_SOP_STAGE");
      stage.SetIsTemporary(true); stage.SetDefaultAction(SimpRepActionType.SIMPREP_INCLUDE);
      SimpRepItems items = SimpRepItems.create(); addStageExclusions(session, assembly, "", desired, items);
      // The temporary stage is only meaningful when its recursively generated
      // exclusion list is attached to the creation instructions.  Omitting
      // this call silently keeps the complete total assembly visible, which
      // invalidates the forward-stage contract and the calibrated framing.
      stage.SetItems(items);
      assembly.ActivateSimpRep(assembly.CreateSimpRep(stage));
      System.err.println("[PERSISTENT] visible occurrences=" + visiblePaths + " stage_exclusions=" + items.getarraysize());

      if (!assembly.GetDynamicPositioning()) assembly.SetDynamicPositioning(true);
      if (!assembly.GetDynamicPositioning()) throw new IllegalStateException("Creo DynamicPositioning was not enabled");

      java.util.Map<String,double[]> preferredAnchors = parsePlannedAnchors(plannedAnchors);
      java.util.List<ArrowProjection.MovingOccurrence> arrowMoving = new java.util.ArrayList<ArrowProjection.MovingOccurrence>();
      for (intseq ids : requestedOccurrences) {
        String occurrenceId = occurrencePathId(ids);
        double[] preferred = preferredAnchors.get(occurrenceId);
        arrowMoving.add(ArrowProjection.prepare(session, assembly, ids, preferred));
      }
      for (intseq ids : requestedOccurrences) translateResolved(session, assembly, ids, dx, dy, dz);
      double[] arrowTranslation = new double[] { dx, dy, dz };
      System.err.println("[PERSISTENT] translated occurrences=" + occurrencePaths + " vector=" + java.util.Arrays.toString(arrowTranslation));

      applyCamera(assembly, cameraSpec);
      Double selectedFitLevel = nativeSelectedFitLevel(cameraSpec);
      if (selectedFitLevel == null)
        throw new IllegalArgumentException("Formal render requires native selected fit");
      applyNativeSelectedFit(session, window, assembly, focusOccurrencePaths, selectedFitLevel);
      ArrowProjection.Result arrowResult = ArrowProjection.layout(assembly, arrowMoving, arrowTranslation);
      ArrowProjection.writeAudit(arrowResult, arrowAuditJson);
      if (drawNativeArrow) arrowDisplay = ArrowProjection.display(session, arrowResult);
      session.UIClearMessage(); window.Repaint(); session.FlushCurrentWindow();
      JPEGImageExportInstructions instructions = pfcWindow.JPEGImageExportInstructions_Create(9.0, 12.0);
      instructions.SetDotsPerInch(DotsPerInch.RASTERDPI_200);
      instructions.SetImageDepth(RasterDepth.RASTERDEPTH_24);
      window.ExportRasterImage(outputJpeg, instructions);
      System.err.println("[PERSISTENT] wrote=" + outputJpeg + " audit=" + arrowAuditJson);
    }
    finally {
      Throwable arrowCleanupFailure = null;
      if (arrowDisplay != null) {
        try {
          arrowDisplay.Delete();
          if (window != null) {
            window.Repaint();
            session.FlushCurrentWindow();
          }
        } catch (Throwable error) {
          arrowCleanupFailure = error;
          System.err.println("[PERSISTENT] ARROW_DISPLAY_CLEANUP_FAILED " + error);
        }
      }
      if (window != null) try { window.Close(); } catch (Throwable ignored) {}
      // This is the state-isolation boundary for the persistent application.
      // The next job reopens the authoritative assembly from the isolated copy.
      try { session.EraseUndisplayedModels(); } catch (Throwable ignored) {}
      if (arrowCleanupFailure != null)
        throw new IllegalStateException("ARROW_DISPLAY_CLEANUP_FAILED", arrowCleanupFailure);
    }
  }

}
