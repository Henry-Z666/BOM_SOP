import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcComponentFeat.*;
import com.ptc.pfc.pfcDisplay.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcModelItem.*;
import com.ptc.pfc.pfcLayer.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcSolid.*;
import com.ptc.pfc.pfcSimpRep.*;
import com.ptc.pfc.pfcView.*;
import com.ptc.pfc.pfcWindow.*;

import java.io.File;
import java.util.concurrent.atomic.AtomicInteger;

/** Creates a native Creo JPEG from an isolated model copy through J-Link async. */
public final class RenderAssemblyImage {
  private static final AtomicInteger FOCUS_REFIT_IDS = new AtomicInteger();
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

  /** Applies a saved view, an absolute root-ASM camera, or a legacy relative rotation. */
  private static void applyCamera(Assembly model, Session session, String cameraSpec, double[] lookAtRoot) throws jxthrowable {
    if (cameraSpec.regionMatches(true, 0, "CMD:", 0, 4)) {
      String command = cameraSpec.substring(4);
      session.RunMacro("~ Command `" + command + "`");
      System.err.println("[RENDER] camera_command=" + command);
      return;
    }
    if (cameraSpec.regionMatches(true, 0, "VIEW:", 0, 5)) {
      String viewName = cameraSpec.substring(5);
      Views available = model.ListViews();
      for (int i = 0; i < available.getarraysize(); i++) {
        System.err.println("[RENDER] available_view=" + available.get(i).GetName());
      }
      View saved = model.RetrieveView(viewName);
      // Saved views include view scale.  Creo accepts the orientation only
      // when the transform is normalized to a rigid, orthonormal matrix.
      Transform3D rigid = pfcBase.MakeMatrixOrthonormal(saved.GetTransform(), 1.0e-8);
      Matrix3D m = rigid.GetMatrix();
      Matrix3D currentMatrix = model.GetCurrentViewTransform().GetMatrix();
      StringBuilder matrixLog = new StringBuilder();
      StringBuilder currentLog = new StringBuilder();
      for (int row = 0; row < 3; row++) {
        for (int col = 0; col < 3; col++) {
          matrixLog.append(String.format(" %.6f", m.get(row, col)));
          currentLog.append(String.format(" %.6f", currentMatrix.get(row, col)));
        }
      }
      System.err.println("[RENDER] camera_view_matrix=" + matrixLog);
      System.err.println("[RENDER] camera_current_matrix=" + currentLog);
      model.SetCurrentViewTransform(rigid);
      System.err.println("[RENDER] camera_view=" + viewName + " normalized=true");
      return;
    }
    double[] absolute = null, upReference = new double[] { 0.0, 0.0, 1.0 };
    boolean hasUp = false, hasRelative = false;
    for (String rawTerm : cameraSpec.split(",")) {
      String term = rawTerm.trim();
      if (term.regionMatches(true, 0, "ABS:", 0, 4)) absolute = parseVectorTerm(term, "ABS:");
      else if (term.regionMatches(true, 0, "UP:", 0, 3)) { upReference = parseVectorTerm(term, "UP:"); hasUp = true; }
      else if (term.matches("(?i)[XYZ]:.*")) hasRelative = true;
    }
    if (absolute != null || hasUp) {
      if (absolute == null) throw new IllegalArgumentException("UP requires ABS");
      if (hasRelative) throw new IllegalArgumentException("ABS cannot be mixed with relative X/Y/Z rotations");
      Transform3D transform = absoluteCameraTransform(absolute, upReference, lookAtRoot);
      model.SetCurrentViewTransform(transform);
      System.err.println("[RENDER] camera_absolute_position_direction=" + java.util.Arrays.toString(normalize(absolute, "ABS")));
      System.err.println("[RENDER] camera_absolute_up_reference=" + java.util.Arrays.toString(normalize(upReference, "UP")));
      System.err.println("[RENDER] camera_absolute_matrix=" + rotationLog(transform));
      if (lookAtRoot != null) System.err.println("[RENDER] camera_look_at_root=" + java.util.Arrays.toString(lookAtRoot));
      return;
    }
    for (String term : cameraSpec.split(",")) {
      if (term.regionMatches(true, 0, "ZOOM:", 0, 5) || term.regionMatches(true, 0, "PAN:", 0, 4)
          || term.regionMatches(true, 0, "FRAME:", 0, 6) || term.equalsIgnoreCase("CENTER")) continue;
      String[] camera = term.split(":");
      if (camera.length != 2 || !(camera[0].equalsIgnoreCase("X") || camera[0].equalsIgnoreCase("Y") || camera[0].equalsIgnoreCase("Z"))) {
        throw new IllegalArgumentException("Unknown camera term: " + term);
      }
      CoordAxis axis = camera[0].equalsIgnoreCase("X") ? CoordAxis.COORD_AXIS_X : camera[0].equalsIgnoreCase("Y") ? CoordAxis.COORD_AXIS_Y : CoordAxis.COORD_AXIS_Z;
      model.CurrentViewRotate(axis, Double.parseDouble(camera[1]));
    }
    System.err.println("[RENDER] legacy_relative_camera=" + cameraSpec);
  }

  private static void applyCamera(Assembly model, Session session, String cameraSpec) throws jxthrowable {
    applyCamera(model, session, cameraSpec, null);
  }

  /**
   * Applies a native graphics-window zoom after Creo has refit the staged
   * simplified representation.  This changes the Creo camera only; it is not
   * an image crop or a simulated mouse/keyboard action.
   */
  private static void applyZoom(Window window, String cameraSpec) throws jxthrowable {
    double appliedMultiplier = 1.0;
    for (String term : cameraSpec.split(",")) {
      if (!term.regionMatches(true, 0, "ZOOM:", 0, 5)) continue;
      double multiplier = Double.parseDouble(term.substring(5));
      if (multiplier <= 0.0) throw new IllegalArgumentException("ZOOM must be positive");
      ScreenTransform screen = window.GetScreenTransform();
      double before = screen.GetZoom();
      double panX = screen.GetPanX(), panY = screen.GetPanY();
      screen.SetZoom(before * multiplier);
      window.SetScreenTransform(screen);
      appliedMultiplier *= multiplier;
      System.err.println("[RENDER] camera_zoom=" + before + "*" + multiplier + "=" + screen.GetZoom()
          + " pan=[" + panX + "," + panY + "]");
    }
    // CENTER used to apply one hard-coded pan calibrated against the complete
    // water-tank assembly.  That value is invalid for a forward stage containing
    // only a small subset of occurrences and can move the subject out of frame.
    // Stage framing is therefore expressed only by an audited ZOOM/PAN pair in
    // the camera contract.  CENTER now means "keep Creo's native Refit centre".
    for (String term : cameraSpec.split(",")) {
      if (!term.equalsIgnoreCase("CENTER")) continue;
      ScreenTransform screen = window.GetScreenTransform();
      System.err.println("[RENDER] camera_center=creo_refit pan=[" + screen.GetPanX() + "," + screen.GetPanY() + "]");
    }
    for (String term : cameraSpec.split(",")) {
      if (!term.regionMatches(true, 0, "PAN:", 0, 4)) continue;
      String[] values = term.substring(4).split(":");
      if (values.length != 2) throw new IllegalArgumentException("PAN requires x:y");
      ScreenTransform screen = window.GetScreenTransform();
      screen.SetPanX(Double.parseDouble(values[0]));
      screen.SetPanY(Double.parseDouble(values[1]));
      window.SetScreenTransform(screen);
      System.err.println("[RENDER] camera_pan=[" + screen.GetPanX() + "," + screen.GetPanY() + "]");
    }
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
  /** Mean root origin of the exact staged occurrence set, after explosion. */
  private static double[] stageOccurrenceCenter(Session session, Assembly root, java.util.List<intseq> paths) throws jxthrowable {
    if (paths == null || paths.isEmpty()) return null;
    double[] total = new double[] { 0.0, 0.0, 0.0 }; int count = 0;
    for (intseq ids : paths) {
      // ComponentPath.GetTransform can block indefinitely for a nested path
      // after a temporary simplified representation is active in Creo 13.4
      // Student. The recursive resolver is deterministic and uses the same
      // occurrence transforms, so use it directly for framing calculations.
      ResolvedComponent resolved = resolveComponent(session, root, ids);
      double[][] rootPose = multiply(matrix(resolved.feature.GetPosition()), resolved.parentToRoot);
      double[] origin = new double[] { rootPose[3][0], rootPose[3][1], rootPose[3][2] };
      for (int axis = 0; axis < 3; axis++) total[axis] += origin[axis];
      count++;
    }
    for (int axis = 0; axis < 3; axis++) total[axis] /= count;
    System.err.println("[RENDER] staged_occurrence_center=" + java.util.Arrays.toString(total) + " count=" + count);
    return total;
  }
  /** Removes descendants when an already selected ancestor moves the same rigid stage. */
  private static java.util.List<intseq> minimalOccurrenceRoots(java.util.List<intseq> paths) throws jxthrowable {
    java.util.List<intseq> ordered = new java.util.ArrayList<intseq>(paths);
    java.util.Collections.sort(ordered, new java.util.Comparator<intseq>() {
      public int compare(intseq left, intseq right) {
        try {
          int length = Integer.compare(left.getarraysize(), right.getarraysize());
          return length != 0 ? length : occurrencePathId(left).compareTo(occurrencePathId(right));
        } catch (jxthrowable error) { throw new RuntimeException(error); }
      }
    });
    java.util.List<intseq> result = new java.util.ArrayList<intseq>();
    for (intseq candidate : ordered) {
      boolean covered = false;
      for (intseq ancestor : result) {
        if (ancestor.getarraysize() > candidate.getarraysize()) continue;
        boolean prefix = true;
        for (int i = 0; i < ancestor.getarraysize(); i++) if (ancestor.get(i) != candidate.get(i)) { prefix = false; break; }
        if (prefix) { covered = true; break; }
      }
      if (!covered) result.add(candidate);
    }
    return result;
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
  private static boolean stageRelated(String path, java.util.Set<String> desired) {
    for (String keep : desired) if (keep.equals(path) || keep.startsWith(path + "/") || path.startsWith(keep + "/")) return true;
    return false;
  }
  /** Hide non-stage component features recursively; unlike SimpRep this keeps leaf graphics in nested ASMs. */
  private static int hideNonStageComponents(Session session, Assembly assembly, String prefix, java.util.Set<String> desired) throws jxthrowable {
    Layer hidden = null; int count = 0; Features features = assembly.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
    for (int i = 0; i < features.getarraysize(); i++) {
      ComponentFeat component = (ComponentFeat)features.get(i); String path = prefix.isEmpty() ? "" + component.GetId() : prefix + "/" + component.GetId();
      if (!stageRelated(path, desired)) { if (hidden == null) hidden = assembly.CreateLayer("AI_SOP_STAGE_HIDDEN"); hidden.AddItem(component); count++; continue; }
      try { Model child = session.RetrieveModel(component.GetModelDescr()); if (child instanceof Assembly) count += hideNonStageComponents(session, (Assembly)child, path, desired); } catch (Throwable ignored) {}
    }
    if (hidden != null) hidden.SetStatus(DisplayStatus.LAYER_BLANK); return count;
  }
  /**
   * Temporarily blanks visible context that is unrelated to the installation
   * focus.  Refit can then measure the actual moving/receiver geometry rather
   * than the total stage.  Restoring these layers without a second refit keeps
   * the activity-sized view while putting the surrounding assembly back into
   * the formal raster.
   */
  private static int blankNonFocusComponents(Session session, Assembly assembly, String prefix,
      java.util.Set<String> focus, String layerName, java.util.List<Layer> changedLayers) throws jxthrowable {
    Layer hidden = null; int count = 0;
    Features features = assembly.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
    for (int i = 0; i < features.getarraysize(); i++) {
      ComponentFeat component = (ComponentFeat)features.get(i);
      String path = prefix.isEmpty() ? "" + component.GetId() : prefix + "/" + component.GetId();
      if (!stageRelated(path, focus)) {
        if (hidden == null) hidden = assembly.CreateLayer(layerName);
        hidden.AddItem(component); count++; continue;
      }
      try {
        Model child = session.RetrieveModel(component.GetModelDescr());
        if (child instanceof Assembly)
          count += blankNonFocusComponents(session, (Assembly)child, path, focus, layerName, changedLayers);
      } catch (Throwable ignored) {}
    }
    if (hidden != null) {
      hidden.SetStatus(DisplayStatus.LAYER_BLANK);
      changedLayers.add(hidden);
    }
    return count;
  }

  private static void restoreFocusContext(java.util.List<Layer> changedLayers) throws jxthrowable {
    for (int i = changedLayers.size() - 1; i >= 0; i--)
      changedLayers.get(i).SetStatus(DisplayStatus.LAYER_DISPLAY);
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

  /**
   * Renders one formal stage inside an already-started Creo session. The model
   * lifecycle stays deliberately per-job: close its window and erase all
   * undisplayed models in the finally block so a later job cannot inherit a
   * temporary simplified representation, dynamic transform, camera, or arrow.
   */
  static void renderInSession(Session session, String assemblyFile, String outputJpeg,
      String occurrencePaths, double dx, double dy, double dz, String visiblePaths,
      String cameraSpec, String arrowAuditJson) throws Throwable {
    renderInSession(session, assemblyFile, outputJpeg, occurrencePaths, dx, dy, dz,
        visiblePaths, cameraSpec, arrowAuditJson, visiblePaths, true);
  }
  /** V3 may retain the exact same geometric audit while exporting an unannotated base raster. */
  static void renderInSession(Session session, String assemblyFile, String outputJpeg,
      String occurrencePaths, double dx, double dy, double dz, String visiblePaths,
      String cameraSpec, String arrowAuditJson, boolean drawNativeArrow) throws Throwable {
    renderInSession(session, assemblyFile, outputJpeg, occurrencePaths, dx, dy, dz,
        visiblePaths, cameraSpec, arrowAuditJson, visiblePaths, drawNativeArrow);
  }
  /** Formal Agent path: zoom is anchored to moving + receiver occurrences. */
  static void renderInSession(Session session, String assemblyFile, String outputJpeg,
      String occurrencePaths, double dx, double dy, double dz, String visiblePaths,
      String cameraSpec, String arrowAuditJson, String focusPaths,
      boolean drawNativeArrow) throws Throwable {
    renderInSession(session, assemblyFile, outputJpeg, occurrencePaths, dx, dy, dz,
        visiblePaths, cameraSpec, arrowAuditJson, focusPaths, "", drawNativeArrow);
  }
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
      java.util.Set<String> focus = new java.util.HashSet<String>();
      // The receiver can be a cabinet-sized plate while the installation
      // activity is a handful of fasteners.  Including that whole receiver in
      // Refit recreates the exact scale failure this probe is meant to solve.
      for (String rawPath : occurrencePaths.split(";")) if (!rawPath.trim().startsWith("!")) focus.add(rawPath.trim());

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
      boolean useActivityLookAt = cameraSpec.toUpperCase(java.util.Locale.ROOT).contains("LOOKAT_ACTIVITY");
      double[] framingTranslation = new double[] {0.0, 0.0, 0.0};
      if (useActivityLookAt) {
        // The receiver may be much larger and have a distant component
        // origin. Center the complete-to-exploded activity midpoint, not only
        // the complete occurrence origin, so the same-CAD-point arrow starts
        // close to the native view centre before any PAN probe is needed.
        double[] stageCenter = stageOccurrenceCenter(session, assembly, requestedOccurrences);
        framingTranslation = new double[] {
          -stageCenter[0] - dx / 2.0,
          -stageCenter[1] - dy / 2.0,
          -stageCenter[2] - dz / 2.0
        };
        for (intseq ids : minimalOccurrenceRoots(visibleOccurrencePaths))
          translateResolved(session, assembly, ids, framingTranslation[0], framingTranslation[1], framingTranslation[2]);
        System.err.println("[PERSISTENT] framing_activity_center=" + java.util.Arrays.toString(stageCenter)
          + " translation=" + java.util.Arrays.toString(framingTranslation));
      }

      java.util.Map<String,double[]> preferredAnchors = parsePlannedAnchors(plannedAnchors);
      java.util.List<ArrowProjection.MovingOccurrence> arrowMoving = new java.util.ArrayList<ArrowProjection.MovingOccurrence>();
      for (intseq ids : requestedOccurrences) {
        String occurrenceId = occurrencePathId(ids);
        double[] preferred = preferredAnchors.get(occurrenceId);
        if (preferred != null) preferred = new double[] {
          preferred[0] + framingTranslation[0],
          preferred[1] + framingTranslation[1],
          preferred[2] + framingTranslation[2]
        };
        arrowMoving.add(ArrowProjection.prepare(session, assembly, ids, preferred));
      }
      for (intseq ids : requestedOccurrences) translateResolved(session, assembly, ids, dx, dy, dz);
      double[] arrowTranslation = new double[] { dx, dy, dz };
      System.err.println("[PERSISTENT] translated occurrences=" + occurrencePaths + " vector=" + java.util.Arrays.toString(arrowTranslation));

      applyCamera(assembly, session, cameraSpec);
      java.util.List<Layer> focusContextLayers = new java.util.ArrayList<Layer>();
      String focusLayerName = "AI_SOP_FOCUS_" + FOCUS_REFIT_IDS.incrementAndGet();
      int focusContextHidden = blankNonFocusComponents(session, assembly, "", focus, focusLayerName, focusContextLayers);
      try {
        window.Repaint(); session.FlushCurrentWindow();
        session.RunMacro("~ Command `ProCmdViewRefit`");
        window.Repaint(); session.FlushCurrentWindow();
      } finally {
        restoreFocusContext(focusContextLayers);
      }
      System.err.println("[PERSISTENT] native_focus_refit=moving_only hidden_context_components=" + focusContextHidden);
      window.Repaint(); session.FlushCurrentWindow(); applyZoom(window, cameraSpec);
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

  public static void main(String[] args) {
    if (args.length != 3 && args.length != 5 && args.length != 7 && args.length != 8 && args.length != 9 && args.length != 10) {
      System.err.println("Usage: RenderAssemblyImage <creo-start-command> <assembly-file> <output.jpg> [second-output.jpg camera] | [occurrence-paths dx dy dz [visible-paths [camera [arrow-audit.json]]]]");
      System.exit(2);
    }
    AsyncConnection connection = null;
    try {
      System.loadLibrary("pfcasyncmt");
      connection = pfcAsyncConnection.AsyncConnection_Start(args[0], null);
      Session session = connection.GetSession();
      ModelDescriptor descriptor = assemblyDescriptor(args[1]);
      // OpenFile establishes a displayable current model; CreateModelWindow on
      // a merely retrieved model leaves Creo with no current graphics object.
      Window window = session.OpenFile(descriptor);
      window.Activate();
      Model model = window.GetModel();
      String requestedBase = new File(args[1]).getName().replaceFirst("\\.[0-9]+$", "");
      if (!model.GetFileName().equalsIgnoreCase(requestedBase)) {
        throw new IllegalStateException("Opened unexpected assembly: requested=" + requestedBase + " actual=" + model.GetFileName());
      }
      Integer requestedVersion = descriptor.GetFileVersion(), actualVersion = model.GetDescr().GetFileVersion();
      if (requestedVersion != null && !requestedVersion.equals(actualVersion)) {
        throw new IllegalStateException("Opened unexpected assembly version: requested=" + requestedVersion + " actual=" + actualVersion);
      }
      System.err.println("[RENDER] authoritative_assembly=" + model.GetFileName() + "." + actualVersion);
      int hiddenAuxiliaryFeatures = hideAuxiliaryFeatures(session, model, new java.util.HashSet<String>());
      System.err.println("[RENDER] native_auxiliary_features_hidden=" + hiddenAuxiliaryFeatures);
      java.util.List<ArrowProjection.MovingOccurrence> arrowMoving = new java.util.ArrayList<ArrowProjection.MovingOccurrence>();
      java.util.List<intseq> requestedOccurrences = new java.util.ArrayList<intseq>();
      java.util.List<intseq> visibleOccurrencePaths = new java.util.ArrayList<intseq>();
      double[] arrowTranslation = null;
      if (args.length >= 7) {
        requestedOccurrences = parseOccurrencePaths(args[3]);
        if (requestedOccurrences.isEmpty()) throw new IllegalArgumentException("No moving occurrence paths supplied");
        double dx = Double.parseDouble(args[4]), dy = Double.parseDouble(args[5]), dz = Double.parseDouble(args[6]);
        arrowTranslation = new double[] { dx, dy, dz };
        Assembly assembly = (Assembly)model;
      }
      if (args.length >= 8) {
        java.util.Set<String> desired = new java.util.HashSet<String>();
        for (String rawPath : args[7].split(";")) if (!rawPath.trim().startsWith("!")) desired.add(rawPath.trim());
        visibleOccurrencePaths = parseOccurrencePaths(String.join(";", desired));
        CreateNewSimpRepInstructions stage = pfcSimpRep.CreateNewSimpRepInstructions_Create("AI_SOP_STAGE");
        stage.SetIsTemporary(true); stage.SetDefaultAction(SimpRepActionType.SIMPREP_INCLUDE);
        SimpRepItems items = SimpRepItems.create(); addStageExclusions(session, (Assembly)model, "", desired, items);
        stage.SetItems(items); ((Assembly)model).ActivateSimpRep(((Assembly)model).CreateSimpRep(stage));
        System.err.println("[RENDER] visible occurrences=" + args[7] + " stage_exclusions=" + items.getarraysize());
      }
      // Activating a simplified representation can restore displayed component
      // placements.  Apply the audited explosion only after the stage rep is active.
      boolean useStageLookAt = args.length >= 9
        && args[8].toUpperCase(java.util.Locale.ROOT).contains("LOOKAT_STAGE");
      if (args.length >= 7) {
        ((Assembly)model).SetDynamicPositioning(true);
        if (!((Assembly)model).GetDynamicPositioning()) throw new IllegalStateException("Creo DynamicPositioning was not enabled");
        System.err.println("[RENDER] dynamic_positioning=true");
        if (useStageLookAt) {
          double[] stageCenter = stageOccurrenceCenter(session, (Assembly)model, visibleOccurrencePaths);
          for (intseq ids : minimalOccurrenceRoots(visibleOccurrencePaths))
            translateResolved(session, (Assembly)model, ids, -stageCenter[0], -stageCenter[1], -stageCenter[2]);
          System.err.println("[RENDER] framing_stage_translation=" + java.util.Arrays.toString(new double[] {-stageCenter[0], -stageCenter[1], -stageCenter[2]}));
        }
        if (args.length == 10) for (intseq ids : requestedOccurrences)
          arrowMoving.add(ArrowProjection.prepare(session, (Assembly)model, ids));
        for (intseq ids : requestedOccurrences) translateResolved(session, (Assembly)model, ids, arrowTranslation[0], arrowTranslation[1], arrowTranslation[2]);
        System.err.println("[RENDER] translated occurrences=" + args[3] + " vector=" + java.util.Arrays.toString(arrowTranslation));
      }
      // The temporary simplified representation above controls nested part
      // visibility.  Auxiliary feature types are blanked recursively before
      // this point; a top-level "parts only" layer would incorrectly discard
      // nested occurrences and is intentionally not used.
      if (args.length >= 9) {
        // Creo 13.4 accepts only the rigid orientation in
        // SetCurrentViewTransform; framing remains a ScreenTransform concern.
        applyCamera((Assembly)model, session, args[8]);
      }
      // Refit is a named Creo command executed through the J-Link Session API;
      // it is not UI-coordinate automation and works in a separate process.
      // For a close-up, all staged occurrence geometry has already been moved
      // as one rigid group around the root origin. Refit now establishes a
      // valid view scale/depth for that stage; it does not change orientation.
      session.RunMacro("~ Command `ProCmdViewRefit`");
      // RunMacro queues the command. Reading ScreenTransform immediately can
      // therefore reuse the previous assembly's pan. Flush before ZOOM reads
      // and modifies the transform.
      window.Repaint();
      session.FlushCurrentWindow();
      if (args.length == 9) applyZoom(window, args[8]);
      if (args.length == 10) applyZoom(window, args[8]);
      DisplayList3D arrowDisplay = null;
      if (args.length == 10) {
        ArrowProjection.Result arrowResult = ArrowProjection.layout((Assembly)model, arrowMoving, arrowTranslation);
        ArrowProjection.writeAudit(arrowResult, args[9]);
        arrowDisplay = ArrowProjection.display(session, arrowResult);
        System.err.println("[RENDER] arrow_projection=same_cad_point/v1 audit=" + args[9]);
      }
      session.UIClearMessage();
      window.Repaint();
      session.FlushCurrentWindow();
      JPEGImageExportInstructions instructions = pfcWindow.JPEGImageExportInstructions_Create(9.0, 12.0);
      instructions.SetDotsPerInch(DotsPerInch.RASTERDPI_200);
      instructions.SetImageDepth(RasterDepth.RASTERDEPTH_24);
      window.ExportRasterImage(args[2], instructions);
      System.err.println("[RENDER] wrote=" + args[2]);
      if (arrowDisplay != null) arrowDisplay.Delete();
      if (args.length == 5) {
        applyCamera((Assembly)model, session, args[4]);
        session.RunMacro("~ Command `ProCmdViewRefit`");
        window.Repaint();
        session.FlushCurrentWindow();
        applyZoom(window, args[4]);
        session.UIClearMessage();
        window.Repaint(); session.FlushCurrentWindow();
        window.ExportRasterImage(args[3], instructions);
        System.err.println("[RENDER] wrote_second=" + args[3] + " camera_rotate=" + args[4]);
      }
      window.Close();
      connection.End();
    } catch (Throwable error) {
      error.printStackTrace();
      try { if (connection != null) connection.End(); } catch (Throwable ignored) {}
      System.exit(1);
    }
  }
}
