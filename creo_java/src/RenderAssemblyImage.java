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
import java.awt.Dimension;
import java.awt.Graphics2D;
import java.awt.Toolkit;
import java.awt.image.BufferedImage;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Iterator;
import java.util.Map;
import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;
import javax.imageio.ImageWriter;
import javax.imageio.stream.FileImageOutputStream;

/** Creates a native Creo JPEG from an isolated model copy through J-Link async. */
public final class RenderAssemblyImage {
  private static Session retainedSession;
  private static Window retainedWindow;
  private static Model retainedModel;
  private static String retainedAssemblyFile;
  private static final java.util.Set<String> retainedAuxiliaryModels = new java.util.HashSet<String>();
  private static final java.util.Set<String> retainedVisibilityModels = new java.util.HashSet<String>();
  private static Session structureSession;
  private static Assembly structureRoot;
  private static final java.util.Map<String, NativeModelNode> structureModels = new java.util.HashMap<String, NativeModelNode>();
  private static NativeModelNode structureRootNode;

  /** Only immutable model/feature identities are cached, never visibility or poses. */
  private static final class NativeModelNode {
    final Model model; final String name;
    java.util.List<NativeComponentNode> components;
    NativeModelNode(Model model,String name){this.model=model;this.name=name;}
  }
  private static final class NativeComponentNode {
    final int id; final ModelDescriptor descriptor;
    NativeModelNode child;
    NativeComponentNode(int id,ModelDescriptor descriptor){this.id=id;this.descriptor=descriptor;}
  }
  private static void clearStructureCache() {
    structureSession=null;structureRoot=null;structureRootNode=null;structureModels.clear();
  }
  private static NativeModelNode structureNode(Model model) throws jxthrowable {
    String name=model.GetFileName();NativeModelNode node=structureModels.get(name);
    if(node==null){node=new NativeModelNode(model,name);structureModels.put(name,node);}return node;
  }
  private static NativeModelNode structureRoot(Session session,Assembly root) throws jxthrowable {
    if(structureSession!=session||structureRoot!=root){clearStructureCache();structureSession=session;structureRoot=root;structureRootNode=structureNode(root);}
    return structureRootNode;
  }
  private static java.util.List<NativeComponentNode> structureChildren(NativeModelNode node) throws jxthrowable {
    if(node.components==null){
      java.util.List<NativeComponentNode> children=new java.util.ArrayList<NativeComponentNode>();
      if(node.model instanceof Assembly){
        Features features=((Assembly)node.model).ListFeaturesByType(Boolean.FALSE,FeatureType.FEATTYPE_COMPONENT);
        for(int i=0;i<features.getarraysize();i++){
          ComponentFeat component=(ComponentFeat)features.get(i);
          if(!isSourceSuppressed(component))children.add(new NativeComponentNode(component.GetId(),component.GetModelDescr()));
        }
      }
      node.components=children;
    }
    return node.components;
  }
  private static NativeModelNode structureChild(Session session,NativeComponentNode component,String path) throws jxthrowable {
    if(component.child==null){
      Model child;
      try{child=session.RetrieveModel(component.descriptor);}
      catch(com.ptc.pfc.pfcExceptions.XToolkitNotFound missing){throw new IllegalStateException("CREO_VISIBLE_MODEL_MISSING: occurrence="+path+" model="+component.descriptor.GetFileName(),missing);}
      if(child==null)throw new IllegalStateException("CREO_VISIBLE_MODEL_MISSING: occurrence="+path+" model="+component.descriptor.GetFileName());
      component.child=structureNode(child);
    }
    return component.child;
  }

  private static void discardRetainedAssembly(Session session) throws jxthrowable {
    Window prior = retainedWindow;
    retainedWindow=null;retainedModel=null;retainedSession=null;retainedAssemblyFile=null;
    retainedAuxiliaryModels.clear();retainedVisibilityModels.clear();
    clearStructureCache();
    if(prior!=null)prior.Close();
    session.EraseUndisplayedModels();
  }

  /** Discard assembly-owned display overrides while retaining immutable part geometry. */
  private static java.util.List<String> discardAssemblyDisplayModels(Session session) throws jxthrowable {
    Window prior=retainedWindow;retainedWindow=null;retainedModel=null;retainedSession=null;retainedAssemblyFile=null;clearStructureCache();
    if(prior!=null)prior.Close();
    Models models=session.ListModelsByType(ModelType.MDL_ASSEMBLY);
    java.util.Map<String,Model> remaining=new java.util.LinkedHashMap<>();
    java.util.Map<String,java.util.Set<String>> children=new java.util.HashMap<>();
    for(int i=0;models!=null&&i<models.getarraysize();i++)remaining.put(models.get(i).GetFileName(),models.get(i));
    for(java.util.Map.Entry<String,Model> entry:remaining.entrySet()){
      java.util.Set<String> refs=new java.util.HashSet<>();Features features=((Assembly)entry.getValue()).ListFeaturesByType(Boolean.FALSE,FeatureType.FEATTYPE_COMPONENT);
      for(int i=0;features!=null&&i<features.getarraysize();i++){
        Model child=session.GetModelFromDescr(((ComponentFeat)features.get(i)).GetModelDescr());
        if(child instanceof Assembly)refs.add(child.GetFileName());
      }
      children.put(entry.getKey(),refs);
      Window own=session.GetModelWindow(entry.getValue());if(own!=null)own.Close();
    }
    Models parts=session.ListModelsByType(ModelType.MDL_PART);
    Window anchorWindow=null;
    if(parts!=null&&parts.getarraysize()>0){
      Model anchor=parts.get(0);anchorWindow=session.GetModelWindow(anchor);
      if(anchorWindow==null)anchorWindow=session.CreateModelWindow(anchor);
      session.SetCurrentWindow(anchorWindow);anchorWindow.Activate();anchor.Display();
    }
    java.util.List<String> erased=new java.util.ArrayList<>();
    while(!remaining.isEmpty()){
      java.util.Set<String> referenced=new java.util.HashSet<>();for(String parent:remaining.keySet())for(String child:children.get(parent))if(remaining.containsKey(child))referenced.add(child);
      java.util.List<String> roots=new java.util.ArrayList<>();for(String name:remaining.keySet())if(!referenced.contains(name))roots.add(name);
      if(roots.isEmpty())throw new IllegalStateException("Assembly display graph has a cycle");
      boolean progress=false;
      for(String name:roots){
        try{remaining.get(name).Erase();remaining.remove(name);erased.add(name);retainedAuxiliaryModels.remove(name);retainedVisibilityModels.remove(name);progress=true;}
        catch(com.ptc.pfc.pfcExceptions.XToolkitInUse dependencyNotExposedByComponents){}
      }
      if(!progress){
        erased.addAll(remaining.keySet());session.EraseUndisplayedModels();remaining.clear();
        java.util.Set<String> loaded=new java.util.HashSet<>();Models retained=session.ListModels();
        for(int i=0;retained!=null&&i<retained.getarraysize();i++)loaded.add(retained.get(i).GetFileName());
        retainedAuxiliaryModels.retainAll(loaded);retainedVisibilityModels.retainAll(loaded);
      }
    }
    Models after=session.ListModelsByType(ModelType.MDL_ASSEMBLY);
    if(after!=null&&after.getarraysize()!=0)throw new IllegalStateException("Transient assembly display models remain loaded");
    if(anchorWindow!=null)anchorWindow.Close();
    System.err.println("[PERSISTENT] assembly_display_models_discarded="+erased.size()+" retained_part_geometry=true");
    return erased;
  }

  private static Transform3D copyPose(Transform3D pose) throws jxthrowable {
    double[][] values=matrix(pose);Matrix3D copy=Matrix3D.create();
    for(int row=0;row<4;row++)for(int col=0;col<4;col++)copy.set(row,col,values[row][col]);
    return pfcBase.Transform3D_Create(copy);
  }
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
      else if (!term.regionMatches(true, 0, "FIT_SELECTED:", 0, 13) && !term.startsWith("FIT_SCOPE:") && !term.startsWith("NOTE_KIND:") && !term.startsWith("ARROW_EVIDENCE:") && !term.startsWith("ROI_SPAN:") && !term.startsWith("ROI_ROLE:") && !term.startsWith("ROI_FRACTION:") && !term.startsWith("MOVING_EVIDENCE:") && !term.startsWith("NOTE_LAYOUT:") && !term.startsWith("STAGE_BOUNDS:") && !term.startsWith("GHOST_RECEIVERS:") && !term.equals("PHYSICAL_DISPLAY:explicit_include"))
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
    return hideAuxiliaryFeatures(session, model, visited, "", null);
  }

  private static int hideAuxiliaryFeatures(Session session, Model model,
      java.util.Set<String> visited, String prefix, java.util.Set<String> desired) throws jxthrowable {
    if (desired != null) {
      // Resolve the complete visible subtree before any expensive layer edits.
      // A late missing child must fail early, not after minutes of cleanup.
      long started = System.nanoTime();
      java.util.Map<String, Model> required = new java.util.LinkedHashMap<String, Model>();
      collectAuxiliaryModels(session, model, prefix, desired, required);
      System.err.println("[PERSISTENT] auxiliary_preflight_seconds="
          + ((System.nanoTime() - started) / 1.0e9) + " models=" + required.size());
      int hidden = 0;
      for (java.util.Map.Entry<String, Model> entry : required.entrySet()) {
        if (visited.add(entry.getKey())) hidden += hideModelAuxiliaryFeatures(entry.getValue());
      }
      return hidden;
    }
    String key = model.GetFileName();
    if (!visited.add(key)) return 0;
    int hidden = hideModelAuxiliaryFeatures(model);
    if (model instanceof Assembly) {
      Features components = ((Assembly)model).ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
      for (int i = 0; i < components.getarraysize(); i++) {
        ComponentFeat component = (ComponentFeat)components.get(i);
        try {
          Model child = session.RetrieveModel(component.GetModelDescr());
          hidden += hideAuxiliaryFeatures(session, child, visited);
        } catch (Throwable unavailableChild) {
          System.err.println("[RENDER] auxiliary_scan_skipped_component=" + component.GetId());
        }
      }
    }
    return hidden;
  }

  private static int hideModelAuxiliaryFeatures(Model model) throws jxthrowable {
    int hidden = 0;
    if (model instanceof Solid) {
      Solid solid = (Solid)model;
      Layer auxiliaryLayer = null;
      java.util.Set<Integer> physicalOwners=new java.util.HashSet<>();
      boolean ownershipLoaded=false;
      for (FeatureType type : AUXILIARY_FEATURE_TYPES) {
        Features features = solid.ListFeaturesByType(Boolean.FALSE, type);
        boolean surfaceFeature=type==FeatureType.FEATTYPE_DATUM_SURFACE||type==FeatureType.FEATTYPE_DATUM_QUILT;
        if(surfaceFeature&&features!=null&&features.getarraysize()>0&&!ownershipLoaded) {
          ownershipLoaded=true;
          try {
            ModelItems bodies=solid.ListItems(ModelItemType.ITEM_BODY);
            java.util.List<SolidBody> allBodies=new java.util.ArrayList<>();
            for(int b=0;bodies!=null&&b<bodies.getarraysize();b++)allBodies.add((SolidBody)bodies.get(b));
            if(allBodies.isEmpty()&&solid.GetDefaultBody()!=null)allBodies.add(solid.GetDefaultBody());
            for(SolidBody body:allBodies)if(body.GetBodyState().getValue()==SolidBodyState.BODY_STATE_ACTIVE.getValue()) {
              Features owners=body.GetFeatures();for(int f=0;owners!=null&&f<owners.getarraysize();f++)physicalOwners.add(owners.get(f).GetId());
            }
          } catch(Throwable unavailableOwnership) {
            System.err.println("[DEBUG-physical-ownership] unavailable model="+model.GetFileName());
          }
        }
        for (int i = 0; features != null && i < features.getarraysize(); i++) {
          if(surfaceFeature&&physicalOwners.contains(features.get(i).GetId())) {
            System.err.println("[DEBUG-physical-ownership] preserved model="+model.GetFileName()+" feature="+features.get(i).GetId()+" type="+type.getValue());
            continue;
          }
          if (auxiliaryLayer == null) auxiliaryLayer = model.CreateLayer("AI_SOP_AUXILIARY");
          auxiliaryLayer.AddItem(features.get(i));
          hidden++;
        }
      }
      if (auxiliaryLayer != null) auxiliaryLayer.SetStatus(DisplayStatus.LAYER_BLANK);
    }
    return hidden;
  }

  private static boolean activePartGeometryAlreadyVisible(Model model) throws jxthrowable {
    if(model instanceof Assembly || !(model instanceof Solid))return false;
    Solid solid=(Solid)model;ModelItems items=solid.ListItems(ModelItemType.ITEM_BODY);
    java.util.List<SolidBody> bodies=new java.util.ArrayList<>();
    for(int i=0;items!=null&&i<items.getarraysize();i++)bodies.add((SolidBody)items.get(i));
    if(bodies.isEmpty()&&solid.GetDefaultBody()!=null)bodies.add(solid.GetDefaultBody());
    int checked=0;
    for(SolidBody body:bodies)if(body.GetBodyState().getValue()==SolidBodyState.BODY_STATE_ACTIVE.getValue()){
      Features features=body.GetFeatures();
      for(int i=0;features!=null&&i<features.getarraysize();i++){
        checked++;if(!features.get(i).GetIsVisible())return false;
      }
    }
    return checked>0;
  }

  private static void normalizeSourceVisibility(Session session,Window rootWindow,Model root,java.util.Set<String> desired) throws jxthrowable {
    java.util.Map<String,Model> models=new java.util.LinkedHashMap<String,Model>();
    collectAuxiliaryModels(session,root,"",desired,models);int reset=0;
    try {
      for(java.util.Map.Entry<String,Model> entry:models.entrySet()) {
        if(retainedVisibilityModels.contains(entry.getKey()))continue;
        Model model=entry.getValue();
        if(activePartGeometryAlreadyVisible(model)){
          retainedVisibilityModels.add(entry.getKey());
          System.err.println("[PERSISTENT] native_part_body_features_already_visible="+entry.getKey());
          continue;
        }
        Window own=model==root?rootWindow:session.GetModelWindow(model);boolean created=own==null;
        if(created)own=session.CreateModelWindow(model);
        try {
          session.SetCurrentWindow(own);own.Activate();model.Display();
          session.RunMacro("~ Command `ProCmdViewShowAll`");own.Repaint();session.FlushCurrentWindow();
          retainedVisibilityModels.add(entry.getKey());reset++;
        }finally{if(created)own.Close();}
      }
    }finally{session.SetCurrentWindow(rootWindow);rootWindow.Activate();root.Display();rootWindow.Repaint();session.FlushCurrentWindow();}
    System.err.println("[PERSISTENT] source_visibility_models_normalized="+reset+" cached="+retainedVisibilityModels.size());
  }

  private static boolean isSourceSuppressed(ComponentFeat component) throws jxthrowable {
    int status = component.GetStatus().getValue();
    // Simplified-representation suppression (3) belongs to the previous
    // render stage and must be reconsidered for the next visibility set.
    return status == 2 || status == 4 || status == 5;
  }

  private static void collectAuxiliaryModels(Session session, Model model, String prefix,
      java.util.Set<String> desired, java.util.Map<String, Model> required) throws jxthrowable {
    NativeModelNode root=prefix.isEmpty()&&model instanceof Assembly?structureRoot(session,(Assembly)model):structureNode(model);
    collectAuxiliaryModelsCached(session,root,prefix,desired,required);
  }
  private static void collectAuxiliaryModelsCached(Session session,NativeModelNode node,String prefix,
      java.util.Set<String> desired,java.util.Map<String,Model> required) throws jxthrowable {
    required.putIfAbsent(node.name,node.model);
    for(NativeComponentNode component:structureChildren(node)){
      String path=prefix.isEmpty()?""+component.id:prefix+"/"+component.id;
      if(stageRelated(path,desired))collectAuxiliaryModelsCached(session,structureChild(session,component,path),path,desired,required);
    }
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
      String trimmed = rawPath.trim(); if (trimmed.isEmpty()||trimmed.startsWith("!")) continue;
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

  private static final class ReceiverInterfaceSelection {
    final intseq occurrencePath;
    final int surfaceId;

    ReceiverInterfaceSelection(intseq occurrencePath, int surfaceId) {
      this.occurrencePath = occurrencePath;
      this.surfaceId = surfaceId;
    }
  }

  /** Parses stable occurrence-path/surface-ID pairs: "5025/47#8027;5025/47#8056". */
  private static java.util.List<ReceiverInterfaceSelection> parseReceiverInterfaceSelections(
      String encoded) throws jxthrowable {
    java.util.List<ReceiverInterfaceSelection> result =
        new java.util.ArrayList<ReceiverInterfaceSelection>();
    if (encoded == null || encoded.trim().isEmpty()) return result;
    java.util.Set<String> seen = new java.util.HashSet<String>();
    for (String raw : encoded.split(";")) {
      int separator = raw.lastIndexOf('#');
      if (separator <= 0 || separator == raw.length() - 1)
        throw new IllegalArgumentException("Malformed receiver interface selection");
      String pathText = raw.substring(0, separator).trim();
      int surfaceId = Integer.parseInt(raw.substring(separator + 1).trim());
      if (surfaceId <= 0 || !seen.add(pathText + "#" + surfaceId))
        throw new IllegalArgumentException("Invalid receiver surface selection=" + raw);
      java.util.List<intseq> paths = parseOccurrencePaths(pathText);
      if (paths.size() != 1)
        throw new IllegalArgumentException("Receiver selection must contain one occurrence path");
      result.add(new ReceiverInterfaceSelection(paths.get(0), surfaceId));
    }
    return result;
  }

  /** Uses Creo's selected-object bounding-box fit; no absolute PAN/ZOOM. */
  private static void applyNativeSelectedFit(Session session, Window window, Assembly assembly,
      java.util.List<intseq> occurrencePaths, double level) throws jxthrowable {
    applyNativeSelectedFit(session, window, assembly, occurrencePaths,
        java.util.Collections.<ReceiverInterfaceSelection>emptyList(), level);
  }

  /** Uses Creo's native fit for moving components and exact constraint receiver surfaces. */
  private static void applyNativeSelectedFit(Session session, Window window, Assembly assembly,
      java.util.List<intseq> occurrencePaths,
      java.util.List<ReceiverInterfaceSelection> receiverInterfaces,
      double level) throws jxthrowable {
    if (session.UIGetCommand("ProCmdZoomIntoOutline") == null)
      throw new IllegalStateException("Creo command is unavailable: ProCmdZoomIntoOutline");
    SelectionBuffer buffer = null;
    for(int attempt=0;attempt<3;attempt++){
      try { window.Activate();window.Repaint();session.FlushCurrentWindow();buffer=session.GetCurrentSelectionBuffer();break; }
      catch(com.ptc.pfc.pfcExceptions.XToolkitCantAccess unavailable){
        if(attempt==2)throw unavailable;
        try{Thread.sleep(500);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();throw new IllegalStateException("Native selected-fit retry interrupted",interrupted);}
      }
    }
    String previousLevel = session.GetConfigOption("zoom_to_selected_level");
    try {
      session.SetConfigOption("zoom_to_selected_level", String.format(java.util.Locale.ROOT, "%.6f", level));
      buffer.Clear();
      for (intseq ids : occurrencePaths) {
        ComponentPath path = pfcAssembly.CreateComponentPath(assembly, ids);
        buffer.AddSelection(pfcSelect.CreateComponentSelection(path));
      }
      for (ReceiverInterfaceSelection receiver : receiverInterfaces) {
        ComponentPath path = pfcAssembly.CreateComponentPath(
            assembly, receiver.occurrencePath);
        ModelItem surface = path.GetLeaf().GetItemById(
            ModelItemType.ITEM_SURFACE, receiver.surfaceId);
        if (surface == null)
          throw new IllegalStateException("Receiver surface unavailable: occurrence="
              + occurrencePathId(receiver.occurrencePath) + " surface=" + receiver.surfaceId);
        buffer.AddSelection(pfcSelect.CreateModelItemSelection(surface, path));
      }
      Selections selected = buffer.GetContents();
      int count = selected == null ? 0 : selected.getarraysize();
      int expected = occurrencePaths.size() + receiverInterfaces.size();
      if (count != expected)
        throw new IllegalStateException("Selection buffer mismatch: expected=" + expected + " actual=" + count);
      window.Repaint();
      session.FlushCurrentWindow();
      session.RunMacro("~ Command `ProCmdZoomIntoOutline`");
      window.Repaint();
      session.FlushCurrentWindow();
      ScreenTransform screen = window.GetScreenTransform();
      System.err.println("[PERSISTENT] native_selected_fit=ProCmdZoomIntoOutline selected=" + count
          + " moving=" + occurrencePaths.size() + " receiver_surfaces=" + receiverInterfaces.size()
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
  private static void projectOutline(Outline3D outline,Transform3D pose,double[][] view,double[] box) throws jxthrowable {
    for(int mask=0;mask<8;mask++){
      Point3D local=Point3D.create();
      for(int axis=0;axis<3;axis++)local.set(axis,outline.get((mask&(1<<axis))==0?0:1).get(axis));
      Point3D root=pose.TransformPoint(local);double x=0,y=0;
      for(int axis=0;axis<3;axis++){x+=root.get(axis)*view[axis][0];y+=root.get(axis)*view[axis][1];}
      box[0]=Math.min(box[0],x);box[1]=Math.min(box[1],y);box[2]=Math.max(box[2],x);box[3]=Math.max(box[3],y);
    }
  }
  private static double balancedFaceMargin(Assembly root,java.util.List<intseq> moving,java.util.List<ReceiverInterfaceSelection> faces,ArrowProjection.Result arrows) throws jxthrowable {
    double[][] view=matrix(root.GetCurrentViewTransform());
    // Creo view matrices contain display scale. Compare CAD extents and arrow
    // anchors in the same physical camera-plane units, not scaled view units.
    for(int col=0;col<2;col++){
      double norm=0;for(int axis=0;axis<3;axis++)norm+=view[axis][col]*view[axis][col];
      norm=Math.sqrt(norm);if(!(norm>0))throw new IllegalStateException("Invalid view basis");
      for(int axis=0;axis<3;axis++)view[axis][col]/=norm;
    }
    double[] faceBox={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
    double[] bodyBox=faceBox.clone();
    for(ReceiverInterfaceSelection face:faces){
      ComponentPath path=pfcAssembly.CreateComponentPath(root,face.occurrencePath);
      com.ptc.pfc.pfcGeometry.Surface surface=(com.ptc.pfc.pfcGeometry.Surface)path.GetLeaf().GetItemById(ModelItemType.ITEM_SURFACE,face.surfaceId);
      projectOutline(surface.GetXYZExtents(),path.GetTransform(true),view,faceBox);
    }
    for(intseq ids:moving){ComponentPath path=pfcAssembly.CreateComponentPath(root,ids);projectOutline(path.GetLeaf().GetGeomOutline(),path.GetTransform(true),view,bodyBox);}
    if(faces.isEmpty())faceBox=bodyBox.clone();
    double faceSpan=Math.max(faceBox[2]-faceBox[0],faceBox[3]-faceBox[1]);
    double bodySpan=Math.max(bodyBox[2]-bodyBox[0],bodyBox[3]-bodyBox[1]);
    if(!Double.isFinite(faceSpan)||!Double.isFinite(bodySpan)||faceSpan<=0)throw new IllegalStateException("Invalid CAD projection for balanced framing");
    double margin=Math.max(1.20,Math.min(1.80,Math.sqrt(Math.max(1.,bodySpan/faceSpan))));
    double cx=(faceBox[0]+faceBox[2])/2,cy=(faceBox[1]+faceBox[3])/2;
    double requiredRadius=faceSpan/2;
    for(double[] segment:arrows==null?java.util.Collections.<double[]>emptyList():ArrowProjection.annotationProtectedSegments(arrows)){
      double headPadding=Math.hypot(segment[2]-segment[0],segment[3]-segment[1])*.22;
      requiredRadius=Math.max(requiredRadius,Math.max(Math.max(Math.abs(segment[0]-cx),Math.abs(segment[2]-cx)),Math.max(Math.abs(segment[1]-cy),Math.abs(segment[3]-cy)))+headPadding);
    }
    margin=Math.max(margin,2*requiredRadius/faceSpan*1.15);
    System.err.println("[PERSISTENT] balanced_frame face_span="+faceSpan+" body_span="+bodySpan+" margin="+margin);
    return margin;
  }

  private static void applyCadRoiFit(Session session,Window window,Assembly root,
      ArrowProjection.Result arrows,double featureSpan,String output,double[] extraBounds) throws Exception,jxthrowable {
    applyCadRoiFit(session,window,root,arrows,featureSpan,output,extraBounds,.65);
  }

  private static double nativeDetailFrameFraction(String cameraSpec) {
    double fraction=.65;
    for(String term:cameraSpec.split(","))if(term.startsWith("ROI_FRACTION:"))fraction=Double.parseDouble(term.substring(13));
    if(!Double.isFinite(fraction)||fraction<.5||fraction>.9)throw new IllegalArgumentException("Invalid detail ROI fraction");
    return fraction;
  }
  private static void applyCadRoiFit(Session session,Window window,Assembly root,
      ArrowProjection.Result arrows,double featureSpan,String output,double[] extraBounds,double frameFraction) throws Exception,jxthrowable {
    if(!Double.isFinite(frameFraction)||frameFraction<.5||frameFraction>.9)throw new IllegalArgumentException("Invalid CAD frame fraction");
    double[][] view=matrix(root.GetCurrentViewTransform());
    double sx=0,sy=0;for(int a=0;a<3;a++){sx+=view[a][0]*view[a][0];sy+=view[a][1]*view[a][1];}sx=Math.sqrt(sx);sy=Math.sqrt(sy);
    double[] roi={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
    for(double[] segment:ArrowProjection.annotationProtectedSegments(arrows)){
      roi[0]=Math.min(roi[0],Math.min(segment[0],segment[2]));roi[1]=Math.min(roi[1],Math.min(segment[1],segment[3]));
      roi[2]=Math.max(roi[2],Math.max(segment[0],segment[2]));roi[3]=Math.max(roi[3],Math.max(segment[1],segment[3]));}
    if(extraBounds!=null){roi[0]=Math.min(roi[0],extraBounds[0]);roi[1]=Math.min(roi[1],extraBounds[1]);roi[2]=Math.max(roi[2],extraBounds[2]);roi[3]=Math.max(roi[3],extraBounds[3]);}
    double[] compact=ArrowProjection.compactMovingCameraBounds(root,arrows,featureSpan);
    if(compact!=null){roi[0]=Math.min(roi[0],compact[0]);roi[1]=Math.min(roi[1],compact[1]);roi[2]=Math.max(roi[2],compact[2]);roi[3]=Math.max(roi[3],compact[3]);}
    double arrowSpan=0;for(double[] segment:ArrowProjection.annotationProtectedSegments(arrows))arrowSpan=Math.max(arrowSpan,Math.hypot(segment[2]-segment[0],segment[3]-segment[1]));
    double pad=Math.max(.75*featureSpan,.22*arrowSpan);if(extraBounds!=null)pad=Math.max(pad,.05*Math.max(roi[2]-roi[0],roi[3]-roi[1]));roi[0]-=pad;roi[1]-=pad;roi[2]+=pad;roi[3]+=pad;
    // Centre the model-space camera on the CAD ROI before high screen zoom.
    // This avoids amplifying subpixel calibration noise at view coordinates
    // several hundred units away from the origin.
    // Creo accepts only its native model-view pose here; centre through the
    // calibrated screen transform below, without changing that pose.
    double[] profile=calibrateViewport(session,window,root,arrows,output,view,sx,sy);
    double desiredX=(roi[0]+roi[2])/2*sx+view[3][0],desiredY=(roi[1]+roi[3])/2*sy+view[3][1];
    double zoom=frameFraction*Math.min(profile[6],profile[7])/Math.max((roi[2]-roi[0])*sx*Math.abs(profile[0]),(roi[3]-roi[1])*sy*Math.abs(profile[1]));
    double panX=(profile[6]/2-profile[4]-zoom*(desiredX*profile[0]+profile[8]))/profile[2];
    double panY=(profile[7]/2-profile[5]-zoom*(desiredY*profile[1]+profile[9]))/profile[3];
    if(!Double.isFinite(zoom)||!Double.isFinite(panX)||!Double.isFinite(panY)||zoom<=0)throw new IllegalStateException("Invalid native CAD ROI transform");
    window.SetScreenTransform(pfcBase.ScreenTransform_Create(panX,panY,zoom));window.Repaint();session.FlushCurrentWindow();
    double pixelRatio=1600/Math.min(profile[6],profile[7]);
    double[] pixelMap={pixelRatio*zoom*sx*profile[0],pixelRatio*zoom*sy*profile[1],800+pixelRatio*(profile[4]+zoom*(view[3][0]*profile[0]+profile[8])+panX*profile[2]-profile[6]/2),800+pixelRatio*(profile[5]+zoom*(view[3][1]*profile[1]+profile[9])+panY*profile[3]-profile[7]/2)};
    Files.writeString(Path.of(output+".roi.json"),"{\"schema_version\":\"cad-roi-screen-transform/v1\",\"feature_span\":"+featureSpan+",\"frame_fraction\":"+frameFraction+",\"roi_camera_plane\":"+java.util.Arrays.toString(roi)+",\"zoom\":"+zoom+",\"pan\":["+panX+","+panY+"],\"source\":\"native_constraint_anchors_and_measured_bounds\",\"temporary_markers_removed\":true,\"camera_plane_to_final_pixels\":"+java.util.Arrays.toString(pixelMap)+",\"calibration_sha256\":\""+fileHash(output+".calibration.json")+"\"}",java.nio.charset.StandardCharsets.UTF_8);
    System.err.println("[PERSISTENT] cad_roi zoom="+zoom+" pan=["+panX+","+panY+"] roi="+java.util.Arrays.toString(roi));
  }

  private static double[] viewportCalibration;
  private static String viewportCalibrationKey,viewportCalibrationEvidence;
  private static String jsonString(String s){return "\""+s.replace("\\","\\\\").replace("\"","\\\"")+"\"";}
  private static String fileHash(String file) throws Exception {return java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(Path.of(file))));}

  private static int calibrationListId=94101;
  private static Point3D calibrationPoint(double[] xyz) throws jxthrowable {Point3D p=Point3D.create();for(int i=0;i<3;i++)p.set(i,xyz[i]);return p;}
  private static final class CalibrationListener extends DefaultDisplayListener {
    final double[][] points;final double[] right,up;final double size;
    CalibrationListener(double[][] p,double[] r,double[] u,double s){points=p;right=r;up=u;size=s;}
    @Override public void OnDisplay(Display d) throws jxthrowable {
      d.SetCurrentGraphicsMode(GraphicsMode.DRAW_GRAPHICS_NORMAL);d.SetCurrentGraphicsColor(StdColor.COLOR_ERROR);
      for(double[] center:points)for(int line=0;line<=100;line++){
        double[] a=new double[3],b=new double[3];for(int k=0;k<3;k++){a[k]=center[k]+up[k]*size*(line/50.0-1)-right[k]*size;b[k]=a[k]+2*right[k]*size;}
        d.SetPenPosition(calibrationPoint(a));d.DrawLine(calibrationPoint(b));
      }
    }
  }
  private static double[][] calibrationSnapshot(Session session,Window window,String file) throws Exception,jxthrowable {
    window.Repaint();session.FlushCurrentWindow();
    JPEGImageExportInstructions i=squareCropSourceExportInstructions(window);i.SetDotsPerInch(DotsPerInch.RASTERDPI_100);i.SetImageDepth(RasterDepth.RASTERDEPTH_24);
    window.ExportRasterImage(file,i);BufferedImage img=ImageIO.read(new File(file));int w=img.getWidth(),h=img.getHeight();
    boolean[] seen=new boolean[w*h];java.util.List<double[]> found=new java.util.ArrayList<>();
    for(int y=0;y<h;y++)for(int x=0;x<w;x++){int idx=y*w+x,c=img.getRGB(x,y);if(seen[idx]||((c>>16)&255)<170||((c>>8)&255)>90||(c&255)<170)continue;
      java.util.ArrayDeque<Integer> todo=new java.util.ArrayDeque<>();todo.add(idx);seen[idx]=true;int n=0,minX=w,minY=h,maxX=-1,maxY=-1;double ax=0,ay=0;
      while(!todo.isEmpty()){int q=todo.removeFirst(),qx=q%w,qy=q/w;n++;ax+=qx;ay+=qy;minX=Math.min(minX,qx);maxX=Math.max(maxX,qx);minY=Math.min(minY,qy);maxY=Math.max(maxY,qy);
        for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++){int nx=qx+dx,ny=qy+dy;if(nx<0||ny<0||nx>=w||ny>=h)continue;int ni=ny*w+nx,nc=img.getRGB(nx,ny);if(!seen[ni]&&((nc>>16)&255)>170&&((nc>>8)&255)<90&&(nc&255)>170){seen[ni]=true;todo.add(ni);}}}
      if(n>=6){if(minX<2||minY<2||maxX>=w-2||maxY>=h-2)throw new IllegalStateException("Viewport calibration marker clipped");found.add(new double[]{ax/n,ay/n,n});}
    }
    if(found.size()!=3)throw new IllegalStateException("Viewport calibration requires exactly three temporary markers; found="+found.size());
    double[] right=java.util.Collections.max(found,java.util.Comparator.comparingDouble(v->v[0])),up=java.util.Collections.min(found,java.util.Comparator.comparingDouble(v->v[1]));
    double[] origin=null;for(double[] v:found)if(v!=right&&v!=up)origin=v;
    if(origin==null)throw new IllegalStateException("Viewport marker layout ambiguous");
    return new double[][]{origin,right,up,new double[]{w,h}};
  }
  private static double[] calibrateViewport(Session session,Window window,Assembly root,ArrowProjection.Result arrows,String output,double[][] view,double sx,double sy) throws Exception,jxthrowable {
    Dimension screen=Toolkit.getDefaultToolkit().getScreenSize();
    String key=screen.width+"x"+screen.height+":"+window.GetGraphicsAreaWidth()+":"+window.GetGraphicsAreaHeight();
    if(viewportCalibration!=null&&key.equals(viewportCalibrationKey)){
      Files.writeString(Path.of(output+".calibration.json"),viewportCalibrationEvidence);return viewportCalibration;
    }

    ScreenTransform saved=window.GetScreenTransform();double z=saved.GetZoom(),px=saved.GetPanX(),py=saved.GetPanY();
    // Shared calibration must not point at an SOP image that may be retried.
    // Keep native probe bytes immutable for the lifetime of all dependent views.
    Path evidenceDirectory=Path.of(output).toAbsolutePath().getParent().resolve("native-calibration-evidence").resolve(java.util.UUID.randomUUID().toString());
    Files.createDirectories(evidenceDirectory);
    String calibrationOutput=evidenceDirectory.resolve("viewport").toString();
    // Calibrate at a neutral viewport center, independent of an installation
    // anchor that may be outside the initial selected-fit view of a large ASM.
    // The saved screen transform is restored before applying the audited ROI.
    double probeZoom=1.0,probePanX=0.5,probePanY=0.5;
    window.SetScreenTransform(pfcBase.ScreenTransform_Create(probePanX,probePanY,probeZoom));
    // View-coordinate zero is on the raster's bottom border, not its centre.
    // A half-clipped marker biases the inferred Y scale at high zoom.
    double delta=160.0/(sx*probeZoom),cx=-view[3][0]/sx,cy=-view[3][1]/sy;
    Outline3D rootOutline=root.GetGeomOutline();double markerDepth=Double.NEGATIVE_INFINITY;
    for(int mask=0;mask<8;mask++){double d=0;for(int axis=0;axis<3;axis++)d+=rootOutline.get((mask&(1<<axis))==0?0:1).get(axis)*arrows.back[axis];markerDepth=Math.max(markerDepth,d);}
    markerDepth+=100.;
    double[][] points=new double[3][3];for(int n=0;n<3;n++)for(int k=0;k<3;k++)points[n][k]=(cx+(n==1?delta:0))*arrows.right[k]+(cy+(n==2?delta:0))*arrows.up[k]+markerDepth*arrows.back[k];
    ColorRGB color=session.GetRGBFromStdColor(StdColor.COLOR_ERROR);DisplayList3D list=null;
    try{
      session.SetStdColorFromRGB(StdColor.COLOR_ERROR,pfcBase.ColorRGB_Create(1.0,0.0,1.0));
      list=session.CreateDisplayList3D(calibrationListId++,new CalibrationListener(points,arrows.right,arrows.up,delta*.06));
      Matrix3D identity=Matrix3D.create();for(int r=0;r<4;r++)for(int c=0;c<4;c++)identity.set(r,c,r==c?1:0);list.Display(pfcBase.Transform3D_Create(identity));
      double[][] a=calibrationSnapshot(session,window,calibrationOutput+".calibration-a.jpg");
      window.SetScreenTransform(pfcBase.ScreenTransform_Create(probePanX+.1,probePanY+.1,probeZoom));
      double[][] c=calibrationSnapshot(session,window,calibrationOutput+".calibration-pan.jpg");

      double kx=(c[0][0]-a[0][0])/.1,ky=(c[0][1]-a[0][1])/.1;
      double scaleX=(a[1][0]-a[0][0])/(delta*sx*probeZoom),scaleY=(a[2][1]-a[0][1])/(delta*sy*probeZoom);

      // Use the native model transform of the diagnostic marker to remove any
      // viewport origin ambiguity; no real component pixels determine framing.
      double vx=view[3][0],vy=view[3][1];for(int k=0;k<3;k++){vx+=points[0][k]*view[k][0];vy+=points[0][k]*view[k][1];}
      // Native zoom can have an aspect-dependent origin. A pan-only probe
      // cannot distinguish that term from the constant viewport offset.
      double secondZoom=2.0;
      window.SetScreenTransform(pfcBase.ScreenTransform_Create(probePanX,probePanY,secondZoom));
      double[][] zoomProbe=calibrationSnapshot(session,window,calibrationOutput+".calibration-zoom.jpg");
      double zoomOriginX=(zoomProbe[0][0]-a[0][0])/(secondZoom-probeZoom)-vx*scaleX;
      double zoomOriginY=(zoomProbe[0][1]-a[0][1])/(secondZoom-probeZoom)-vy*scaleY;
      double ox=a[0][0]-probeZoom*(vx*scaleX+zoomOriginX)-kx*probePanX,oy=a[0][1]-probeZoom*(vy*scaleY+zoomOriginY)-ky*probePanY;
      double residualX=a[2][0]-a[0][0],residualY=a[1][1]-a[0][1];
      if(!Double.isFinite(ox)||Math.abs(kx)<1||Math.abs(ky)<1||Math.abs(residualX)>8||Math.abs(residualY)>8)throw new IllegalStateException("Viewport calibration residual="+residualX+","+residualY);
      double[] result={scaleX,scaleY,kx,ky,ox,oy,a[3][0],a[3][1],zoomOriginX,zoomOriginY};
      StringBuilder snapshots=new StringBuilder("[");
      for(String suffix:new String[]{".calibration-a.jpg",".calibration-pan.jpg",".calibration-zoom.jpg"}){
        String file=Path.of(calibrationOutput+suffix).toAbsolutePath().toString();if(snapshots.length()>1)snapshots.append(',');
        snapshots.append("{\"file\":").append(jsonString(file)).append(",\"sha256\":").append(jsonString(fileHash(file))).append('}');
      }snapshots.append(']');
      viewportCalibrationEvidence="{\"schema_version\":\"native-viewport-calibration/v2\",\"fingerprint\":"+jsonString(key)+",\"profile\":"+java.util.Arrays.toString(result)+",\"residual\":["+residualX+","+residualY+"],\"zoom_probe\":"+java.util.Arrays.deepToString(zoomProbe)+",\"world_markers\":"+java.util.Arrays.deepToString(points)+",\"pixel_markers\":"+java.util.Arrays.deepToString(a)+",\"snapshots\":"+snapshots+"}";
      Files.writeString(Path.of(output+".calibration.json"),viewportCalibrationEvidence);
      viewportCalibrationKey=key;
      viewportCalibration=result;return result;
    }finally{
      try{if(list!=null)list.Delete();}
      finally{try{session.SetStdColorFromRGB(StdColor.COLOR_ERROR,color);}
        finally{window.SetScreenTransform(pfcBase.ScreenTransform_Create(px,py,z));window.Repaint();session.FlushCurrentWindow();}}
    }
  }

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

  /** Parses one pure root translation for every moving occurrence. */
  private static java.util.Map<String,double[]> parseOccurrenceTranslations(String encoded) {
    java.util.Map<String,double[]> result = new java.util.LinkedHashMap<String,double[]>();
    if (encoded == null || encoded.trim().isEmpty()) return result;
    for (String raw : encoded.split(";")) {
      int separator = raw.indexOf('=');
      if (separator <= 0 || separator == raw.length() - 1)
        throw new IllegalArgumentException("Malformed occurrence translation");
      String occurrence = raw.substring(0, separator).trim();
      String[] coordinates = raw.substring(separator + 1).split(":", -1);
      if (coordinates.length != 3 || result.containsKey(occurrence))
        throw new IllegalArgumentException("Invalid occurrence translation=" + occurrence);
      double[] vector = new double[3];
      for (int index = 0; index < 3; index++) {
        vector[index] = Double.parseDouble(coordinates[index]);
        if (!Double.isFinite(vector[index]))
          throw new IllegalArgumentException("Non-finite occurrence translation=" + occurrence);
      }
      result.put(occurrence, vector);
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
    if(dx==0.0&&dy==0.0&&dz==0.0)return;
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
        result.add(new DynamicPose(occurrenceId, ids, copyPose(pose), true));
        continue;
      } catch (jxthrowable unsupportedPathTransform) {
      }
      result.add(new DynamicPose(occurrenceId, ids,
          copyPose(resolveComponent(session, root, ids).feature.GetPosition()), false));
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
    for(String keep:desired)if(keep.startsWith("!")&&(path.equals(keep.substring(1))||path.startsWith(keep.substring(1)+"/")))return false;
    for (String keep : desired) if (keep.equals(path) || keep.startsWith(path + "/") || path.startsWith(keep + "/")) return true;
    return false;
  }
  private static void addStageExclusions(Session session, Assembly assembly, String prefix, java.util.Set<String> desired, SimpRepItems items) throws jxthrowable {
    addCachedVisibilityExclusions(session,prefix.isEmpty()?structureRoot(session,assembly):structureNode(assembly),prefix,desired,"",items);
  }
  private static void addCachedVisibilityExclusions(Session session,NativeModelNode node,String prefix,
      java.util.Set<String> desired,String excludedTarget,SimpRepItems items) throws jxthrowable {
    for(NativeComponentNode component:structureChildren(node)){
      String path=prefix.isEmpty()?""+component.id:prefix+"/"+component.id;
      if(path.equals(excludedTarget)||!stageRelated(path,desired)){
        intseq ids = parseOccurrencePaths(path).get(0);
        SimpRepItem item = pfcSimpRep.SimpRepItem_Create(pfcSimpRep.SimpRepCompItemPath_Create(ids));
        item.SetAction(pfcSimpRep.SimpRepExclude_Create()); items.append(item); continue;
      }
      try{NativeModelNode child=structureChild(session,component,path);if(child.model instanceof Assembly)addCachedVisibilityExclusions(session,child,path,desired,excludedTarget,items);}catch(Throwable ignored){}
    }
  }

  private static void addVisibilityExclusions(Session session, Assembly assembly, String prefix,
      java.util.Set<String> desired, String excludedTarget, SimpRepItems items) throws jxthrowable {
    addCachedVisibilityExclusions(session,prefix.isEmpty()?structureRoot(session,assembly):structureNode(assembly),prefix,desired,excludedTarget,items);
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

  private static void alignDiagnosticView(Assembly root,Window window,Transform3D desired,double panX,double panY,double zoom,double[] profile) throws jxthrowable {
    double[][] a=matrix(desired),b=matrix(root.GetCurrentViewTransform());double sa=0,sb=0;for(int k=0;k<3;k++){sa+=a[k][0]*a[k][0];sb+=b[k][0]*b[k][0];}sa=Math.sqrt(sa);sb=Math.sqrt(sb);
    double ratio=sa/sb;
    for(int r=0;r<3;r++)for(int c=0;c<3;c++)if(Math.abs(a[r][c]-b[r][c]*ratio)>1.e-7)throw new IllegalStateException("Visibility diagnostic changed camera orientation");
    double newZoom=zoom*ratio,newPanX=panX+((zoom*a[3][0]-newZoom*b[3][0])*profile[0]+(zoom-newZoom)*profile[8])/profile[2],newPanY=panY+((zoom*a[3][1]-newZoom*b[3][1])*profile[1]+(zoom-newZoom)*profile[9])/profile[3];
    window.SetScreenTransform(pfcBase.ScreenTransform_Create(newPanX,newPanY,newZoom));
  }
  private static int pathDepth(intseq ids){try{return ids.getarraysize();}catch(jxthrowable error){throw new IllegalStateException("Cannot read occurrence path",error);}}
  private static int visibilityEvidenceId=1;
  private static void writeMovingVisibilityEvidence(Session session,Window window,Assembly root,SimpRep stage,SimpRepItems stageItems,
      java.util.List<intseq> moving,java.util.List<intseq> visible,ArrowProjection.Result arrows,String output) throws Exception,jxthrowable {
    long started=System.nanoTime();
    java.util.List<intseq> ordered=new java.util.ArrayList<>(visible);ordered.sort(java.util.Comparator.comparingInt(RenderAssemblyImage::pathDepth));
    java.util.List<DynamicPose> poses=captureDynamicPoses(session,root,ordered);
    Transform3D view=copyPose(root.GetCurrentViewTransform());double[][] modelView=matrix(view);double sx=0,sy=0;for(int k=0;k<3;k++){sx+=modelView[k][0]*modelView[k][0];sy+=modelView[k][1]*modelView[k][1];}sx=Math.sqrt(sx);sy=Math.sqrt(sy);
    double[] profile=calibrateViewport(session,window,root,arrows,output,modelView,sx,sy);
    ScreenTransform screen=window.GetScreenTransform();
    double px=screen.GetPanX(),py=screen.GetPanY(),zoom=screen.GetZoom();StringBuilder entries=new StringBuilder("[");
    int index=0;
    for(intseq target:moving){
      String id=occurrencePathId(target);SimpRep hidden=null;
      try{
        SimpRepItems items=SimpRepItems.create();for(int i=0;i<stageItems.getarraysize();i++)items.append(stageItems.get(i));
        SimpRepItem exclude=pfcSimpRep.SimpRepItem_Create(pfcSimpRep.SimpRepCompItemPath_Create(target));exclude.SetAction(pfcSimpRep.SimpRepExclude_Create());items.append(exclude);
        CreateNewSimpRepInstructions instruction=pfcSimpRep.CreateNewSimpRepInstructions_Create("AI_VIS_PROOF_"+(visibilityEvidenceId++));instruction.SetIsTemporary(true);instruction.SetDefaultAction(SimpRepActionType.SIMPREP_INCLUDE);instruction.SetItems(items);
        hidden=root.CreateSimpRep(instruction);root.ActivateSimpRep(hidden);
        if(!root.GetDynamicPositioning()){System.err.println("[PERSISTENT] visibility_rep_reset_dynamic_positioning=true");root.SetDynamicPositioning(true);}
        restoreDynamicPoses(session,root,poses,id);
        alignDiagnosticView(root,window,view,px,py,zoom,profile);window.Repaint();session.FlushCurrentWindow();
        JPEGImageExportInstructions export=squareCropSourceExportInstructions(window);export.SetDotsPerInch(DotsPerInch.RASTERDPI_200);export.SetImageDepth(RasterDepth.RASTERDEPTH_24);
        String file=output+".without-moving-"+(++index)+".jpg";Path source=Path.of(file+".source.jpg");window.ExportRasterImage(source.toString(),export);cropCenterSquare(source,Path.of(file));
        if(entries.length()>1)entries.append(',');entries.append("{\"occurrence_id\":").append(jsonString(id)).append(",\"file\":").append(jsonString(Path.of(file).toAbsolutePath().toString())).append(",\"sha256\":").append(jsonString(fileHash(file))).append('}');
      }finally{
        root.ActivateSimpRep(stage);if(!root.GetDynamicPositioning())root.SetDynamicPositioning(true);restoreDynamicPoses(session,root,poses,"");alignDiagnosticView(root,window,view,px,py,zoom,profile);window.Repaint();session.FlushCurrentWindow();
        if(hidden!=null)try{root.DeleteSimpRep(hidden);}catch(com.ptc.pfc.pfcExceptions.XToolkitNotFound removed){}
      }
    }
    for(DynamicPose p:poses){Transform3D current=p.componentPathApi?pfcAssembly.CreateComponentPath(root,p.ids).GetTransform(true):resolveComponent(session,root,p.ids).feature.GetPosition();double[][] before=matrix(p.pose),after=matrix(current);for(int r=0;r<4;r++)for(int c=0;c<4;c++)if(Math.abs(before[r][c]-after[r][c])>1.e-6)throw new IllegalStateException("Visibility proof changed occurrence pose="+p.occurrenceId);}
    entries.append(']');
    double[][] finalView=matrix(root.GetCurrentViewTransform());ScreenTransform finalScreen=window.GetScreenTransform();
    String alignment="{\"source_model_view\":"+java.util.Arrays.deepToString(matrix(view))+",\"final_model_view\":"+java.util.Arrays.deepToString(finalView)+",\"target_pan_zoom\":["+px+","+py+","+zoom+"],\"final_pan_zoom\":["+finalScreen.GetPanX()+","+finalScreen.GetPanY()+","+finalScreen.GetZoom()+"],\"physical_to_pixel_preserved\":true}";
    double seconds=(System.nanoTime()-started)/1e9;
    Files.writeString(Path.of(output+".moving-visibility.json"),"{\"schema_version\":\"native-occurrence-difference/v1\",\"camera_and_other_occurrences_unchanged\":true,\"poses_restored\":true,\"view_alignment\":"+alignment+",\"reference_sha256\":"+jsonString(fileHash(output+".background.jpg"))+",\"entries\":"+entries+",\"seconds\":"+seconds+"}");
    System.err.println("[PERSISTENT] moving_visibility_seconds="+seconds+" occurrences="+moving.size());
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

  private static JPEGImageExportInstructions squareCropSourceExportInstructions(
      Window window) throws jxthrowable {
    double graphicsWidth = window.GetGraphicsAreaWidth();
    double graphicsHeight = window.GetGraphicsAreaHeight();
    if (!(graphicsWidth > 0.0) || !(graphicsHeight > 0.0))
      throw new IllegalStateException("Creo graphics viewport dimensions are unavailable");
    // J-Link normalizes width by the screen width and height by the screen
    // height.  Their raw ratio is therefore not the viewport's pixel aspect
    // ratio.  Restore both axes to the same pixel coordinate system first.
    Dimension screenSize = Toolkit.getDefaultToolkit().getScreenSize();
    double graphicsPixelWidth = graphicsWidth * screenSize.getWidth();
    double graphicsPixelHeight = graphicsHeight * screenSize.getHeight();
    // Keep the native viewport geometry undistorted, but overscan until the
    // short edge is 1600 px.  A deterministic centre crop can then produce a
    // 1600x1600 publication frame without padding, stretching or upscaling.
    double widthInches;
    double heightInches;
    if (graphicsPixelWidth >= graphicsPixelHeight) {
      heightInches = 8.0;
      widthInches = 8.0 * graphicsPixelWidth / graphicsPixelHeight;
    } else {
      widthInches = 8.0;
      heightInches = 8.0 * graphicsPixelHeight / graphicsPixelWidth;
    }
    JPEGImageExportInstructions instructions =
        pfcWindow.JPEGImageExportInstructions_Create(widthInches, heightInches);
    System.err.println("[PERSISTENT] square_crop_source viewport_normalized=" + graphicsWidth + "x" + graphicsHeight
        + " screen=" + screenSize.width + "x" + screenSize.height
        + " viewport_pixels=" + graphicsPixelWidth + "x" + graphicsPixelHeight
        + " export_inches=" + widthInches + "x" + heightInches);
    return instructions;
  }

  private static double squareCropSafeFitLevel(Window window, double requested)
      throws jxthrowable {
    double graphicsWidth = window.GetGraphicsAreaWidth();
    double graphicsHeight = window.GetGraphicsAreaHeight();
    if (!(graphicsWidth > 0.0) || !(graphicsHeight > 0.0))
      throw new IllegalStateException("Creo graphics viewport dimensions are unavailable");
    Dimension screenSize = Toolkit.getDefaultToolkit().getScreenSize();
    double pixelWidth = graphicsWidth * screenSize.getWidth();
    double pixelHeight = graphicsHeight * screenSize.getHeight();
    double aspect = Math.max(pixelWidth, pixelHeight) / Math.min(pixelWidth, pixelHeight);
    double effective = Math.max(0.1, Math.min(0.95, requested / aspect));
    System.err.println("[PERSISTENT] square_crop_fit requested=" + requested
        + " aspect=" + aspect + " effective=" + effective);
    return effective;
  }

  private static void cropCenterSquare(Path source, Path output) throws Exception {
    BufferedImage image = ImageIO.read(source.toFile());
    if (image == null) throw new IllegalStateException("Creo JPEG cannot be decoded: " + source);
    int side = Math.min(image.getWidth(), image.getHeight());
    if (side != 1600)
      throw new IllegalStateException("Square crop source short edge must be 1600 px: "
          + image.getWidth() + "x" + image.getHeight());
    int x = (image.getWidth() - side) / 2;
    int y = (image.getHeight() - side) / 2;
    BufferedImage square = new BufferedImage(side, side, BufferedImage.TYPE_INT_RGB);
    Graphics2D graphics = square.createGraphics();
    try {
      graphics.drawImage(image, 0, 0, side, side, x, y, x + side, y + side, null);
    } finally {
      graphics.dispose();
    }
    Iterator<ImageWriter> writers = ImageIO.getImageWritersByFormatName("jpg");
    if (!writers.hasNext())
      throw new IllegalStateException("No JPEG writer available for square publication frame");
    ImageWriter writer = writers.next();
    Files.deleteIfExists(output);
    try (FileImageOutputStream stream = new FileImageOutputStream(output.toFile())) {
      ImageWriteParam parameters = writer.getDefaultWriteParam();
      if (parameters.canWriteCompressed()) {
        parameters.setCompressionMode(ImageWriteParam.MODE_EXPLICIT);
        parameters.setCompressionQuality(0.95f);
      }
      writer.setOutput(stream);
      writer.write(null, new IIOImage(square, null, null), parameters);
    } finally {
      writer.dispose();
    }
    Files.deleteIfExists(source);
    System.err.println("[PERSISTENT] square_crop source=" + image.getWidth() + "x"
        + image.getHeight() + " crop_origin=" + x + "," + y
        + " output=" + side + "x" + side);
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
      String movingPaths, String receiverPaths, String encodedTranslations, String desiredPaths,
      String cameraSpec, String focusPaths, LinkedHashMap<String,Integer> labels,
      boolean requireVisibleTarget) throws Throwable {
    // Visibility scenes close/erase their models independently; never leave
    // a retained formal-render window pointing at an erased model.
    if(retainedWindow!=null)discardRetainedAssembly(session);
    Window window = null; boolean completed = false;
    Path rawRoot = outputPng.resolveSibling(outputPng.getFileName().toString() + ".raw");
    try {
      Files.createDirectories(rawRoot);
      ModelDescriptor descriptor = assemblyDescriptor(assemblyFile);
      NativeModelBindings.apply(session);
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
      boolean inheritedExploded=assembly.GetIsExploded();
      System.err.println("[PERSISTENT] inherited_exploded_display="+inheritedExploded);
      if(inheritedExploded)assembly.UnExplode();
      if (!assembly.GetDynamicPositioning()) assembly.SetDynamicPositioning(true);
      java.util.List<intseq> movingOccurrencePaths = parseOccurrencePaths(movingPaths);
      java.util.Map<String,double[]> translations = parseOccurrenceTranslations(encodedTranslations);
      for (intseq ids : movingOccurrencePaths) {
        String occurrenceId = occurrencePathId(ids);
        double[] vector = translations.get(occurrenceId);
        if (vector == null) throw new IllegalArgumentException("Missing visibility translation=" + occurrenceId);
        translateResolved(session, assembly, ids, vector[0], vector[1], vector[2]);
      }
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
      String fixed456Camera, String focusPaths, String encodedLabels,
      String encodedTranslations) throws Throwable {
    LinkedHashMap<String,Integer> labels = parseVisibilityLabels(encodedLabels);
    String isolatedPaths = movingPaths + ";" + receiverPaths;
    for (String cameraId : new String[] { "fixed_123", "fixed_456" }) {
      String camera = cameraId.equals("fixed_123") ? fixed123Camera : fixed456Camera;
      renderVisibilityScene(session, assemblyFile, Path.of(outputDirectory, taskId + "." + cameraId + ".isolated.png"), movingPaths, receiverPaths, encodedTranslations, isolatedPaths, camera, focusPaths, labels, true);
      renderVisibilityScene(session, assemblyFile, Path.of(outputDirectory, taskId + "." + cameraId + ".staged.png"), movingPaths, receiverPaths, encodedTranslations, visiblePaths, camera, focusPaths, labels, false);
    }
  }

  /**
   * Renders one formal stage inside an already-started Creo session. The model
   * model stays loaded between jobs. Restore and verify every moved pose,
   * remove each temporary representation/annotation, and reapply the next
   * job's exact visibility and camera. Discard the model on any failure.
   */
  /** Formal Agent path with constraint-backed same-CAD-point anchors. */
  static void renderInSession(Session session, String assemblyFile, String outputJpeg,
      String occurrencePaths, double dx, double dy, double dz, String visiblePaths,
      String cameraSpec, String arrowAuditJson, String focusPaths,
      String plannedAnchors, String receiverSelections, String encodedTranslations,
      boolean drawNativeArrow, String torqueLabels, String torqueBounds, String torqueContractHash) throws Throwable {
    renderInSession(session,assemblyFile,outputJpeg,occurrencePaths,dx,dy,dz,visiblePaths,
        cameraSpec,arrowAuditJson,focusPaths,plannedAnchors,receiverSelections,encodedTranslations,
        drawNativeArrow,torqueLabels,torqueBounds,torqueContractHash,false);
  }
  static void renderInSession(Session session, String assemblyFile, String outputJpeg,
      String occurrencePaths, double dx, double dy, double dz, String visiblePaths,
      String cameraSpec, String arrowAuditJson, String focusPaths,
      String plannedAnchors, String receiverSelections, String encodedTranslations,
      boolean drawNativeArrow, String torqueLabels, String torqueBounds, String torqueContractHash,
      boolean completedStageOnly) throws Throwable {
    Window window = null;
    DisplayList3D arrowDisplay = null;
    DisplayList3D torqueDisplay = null;
    Assembly stageAssembly=null;
    SimpRep previousRep=null,jobRep=null;
    java.util.List<DynamicPose> savedPoses=null;
    NativeReceiverAppearance receiverAppearance=new NativeReceiverAppearance();
    String ghostReceivers="";
    for(String term:cameraSpec.split(","))if(term.startsWith("GHOST_RECEIVERS:"))ghostReceivers=term.substring(16);
    String ghostSourceHash=ghostReceivers.isEmpty()?null:fileHash(assemblyFile);
    java.util.List<String> discardedDisplayModels=null;
    boolean completed=false;
    try {
      long openStarted = System.nanoTime();
      ModelDescriptor descriptor = assemblyDescriptor(assemblyFile);
      boolean reuse=retainedSession==session && assemblyFile.equals(retainedAssemblyFile) && retainedWindow!=null;
      if(!reuse){
        clearStructureCache();
        if(retainedWindow!=null)discardRetainedAssembly(session);
        NativeModelBindings.apply(session);
        window=session.OpenFile(descriptor);
        retainedSession=session;retainedWindow=window;retainedModel=window.GetModel();retainedAssemblyFile=assemblyFile;
      }else window=retainedWindow;
      window.Activate();
      Model model = retainedModel;
      System.err.println("[PERSISTENT] assembly_reused="+reuse);
      System.err.println("[PERSISTENT] model_open_seconds=" + ((System.nanoTime() - openStarted) / 1.0e9));
      String requestedBase = new File(assemblyFile).getName().replaceFirst("\\.[0-9]+$", "");
      if (!model.GetFileName().equalsIgnoreCase(requestedBase)) {
        throw new IllegalStateException("Opened unexpected assembly: requested=" + requestedBase + " actual=" + model.GetFileName());
      }
      Integer requestedVersion = descriptor.GetFileVersion(), actualVersion = model.GetDescr().GetFileVersion();
      if (requestedVersion != null && !requestedVersion.equals(actualVersion)) {
        throw new IllegalStateException("Opened unexpected assembly version: requested=" + requestedVersion + " actual=" + actualVersion);
      }
      System.err.println("[PERSISTENT] authoritative_assembly=" + model.GetFileName() + "." + actualVersion);

      java.util.List<intseq> requestedOccurrences = parseOccurrencePaths(occurrencePaths);
      if (requestedOccurrences.isEmpty()) throw new IllegalArgumentException("No moving occurrence paths supplied");
      java.util.Set<String> desired = new java.util.HashSet<String>();
      for (String rawPath : visiblePaths.split(";")) if(!rawPath.trim().isEmpty())desired.add(rawPath.trim());
      java.util.List<intseq> visibleOccurrencePaths = parseOccurrencePaths(String.join(";", desired));
      if (visibleOccurrencePaths.isEmpty()) throw new IllegalArgumentException("No visible occurrence paths supplied");
      if (parseOccurrencePaths(focusPaths).isEmpty())
        throw new IllegalArgumentException("No installation focus occurrence paths supplied");
      Assembly assembly = (Assembly)model;
      stageAssembly=assembly;
      previousRep=assembly.GetActiveSimpRep();
      long stageStarted = System.nanoTime();
      CreateNewSimpRepInstructions stage = pfcSimpRep.CreateNewSimpRepInstructions_Create("AI_SOP_STAGE");
      stage.SetIsTemporary(!cameraSpec.contains("MOVING_EVIDENCE:occurrence_difference")); stage.SetDefaultAction(SimpRepActionType.SIMPREP_INCLUDE);
      SimpRepItems items = SimpRepItems.create(); addStageExclusions(session, assembly, "", desired, items);
      // The temporary stage is only meaningful when its recursively generated
      // exclusion list is attached to the creation instructions.  Omitting
      // this call silently keeps the complete total assembly visible, which
      // invalidates the forward-stage contract and the calibrated framing.
      stage.SetItems(items);
      jobRep=assembly.CreateSimpRep(stage);
      assembly.ActivateSimpRep(jobRep);
      for(intseq requested:requestedOccurrences) {
        ComponentPath occurrence=pfcAssembly.CreateComponentPath(assembly,requested);
        System.err.println("[DEBUG-physical-stage] path="+occurrencePathId(requested)+" visible="+occurrence.GetIsVisible(jobRep));
        try {
          intseq parent=intseq.create();for(int a=0;a<requested.getarraysize()-1;a++)parent.append(requested.get(a));
          Assembly owner=requested.getarraysize()==1?assembly:(Assembly)pfcAssembly.CreateComponentPath(assembly,parent).GetLeaf();
          ComponentFeat feature=(ComponentFeat)owner.GetFeatureById(requested.get(requested.getarraysize()-1));
          System.err.println("[DEBUG-physical-stage] placed="+feature.GetIsPlaced()+" frozen="+feature.GetIsFrozen()+" bulk="+feature.GetIsBulkitem()+" status="+feature.GetStatus().getValue()+" feature_visible="+feature.GetIsVisible()+" component_type="+feature.GetCompType().getValue()+" substitute="+feature.GetIsSubstitute());
          MassProperty memberMass=assembly.GetMassPropertyByCompPath(occurrence,null,null);
          System.err.println("[DEBUG-physical-stage] assembly_context_volume="+memberMass.GetVolume()+" mass="+memberMass.GetMass()+" gravity="+pointText(memberMass.GetGravityCenter()));
        } catch(Throwable unavailableFlags) { }
        try {
          Solid physical=occurrence.GetLeaf();SolidBody body=physical.GetDefaultBody();
          if(body!=null)System.err.println("[DEBUG-physical-stage] model="+physical.GetFileName()+" body="+body.GetId()+" state="+body.GetBodyState().getValue()+" construction="+body.IsConstruction());
        }catch(jxthrowable unavailableBody) { }
      }
      System.err.println("[PERSISTENT] stage_visibility_seconds=" + ((System.nanoTime() - stageStarted) / 1.0e9));
      System.err.println("[PERSISTENT] visible occurrences=" + visiblePaths + " stage_exclusions=" + items.getarraysize());

      // Future branches are already excluded by this exact same stage predicate.
      // Clean every displayed model plus its ancestors, not the entire root ASM.
      // Auxiliary content is static in the immutable model copy. Resolve all
      // visible models every job, but blank each model only once per session.
      long auxiliaryStarted = System.nanoTime();
      java.util.Set<String> auxiliaryModels = retainedAuxiliaryModels;
      int hiddenAuxiliaryFeatures = hideAuxiliaryFeatures(session, model, auxiliaryModels, "", desired);
      System.err.println("[PERSISTENT] native_auxiliary_features_hidden=" + hiddenAuxiliaryFeatures
          + " models=" + auxiliaryModels.size()
          + " seconds=" + ((System.nanoTime() - auxiliaryStarted) / 1.0e9));
      System.err.println("[PERSISTENT] inherited_exploded_display="+assembly.GetIsExploded());
      try {
        com.ptc.pfc.pfcXSection.XSections sections=assembly.ListCrossSections();
        System.err.println("[DEBUG-physical-stage] root_cross_sections="+(sections==null?0:sections.getarraysize()));
        for(int s=0;sections!=null&&s<sections.getarraysize();s++)System.err.println("[DEBUG-physical-stage] cross_section="+sections.get(s).GetName());
      }catch(Throwable unavailableSections) { System.err.println("[DEBUG-physical-stage] cross_sections_status="+unavailableSections.getClass().getSimpleName()); }
      if (!assembly.GetDynamicPositioning()) assembly.SetDynamicPositioning(true);
      if (!assembly.GetDynamicPositioning()) throw new IllegalStateException("Creo DynamicPositioning was not enabled");
      {
        session.SetCurrentWindow(window);window.Activate();
        if(session.UIGetCommand("ProCmdViewShowAll")==null)throw new IllegalStateException("Native unhide-all command unavailable");
        normalizeSourceVisibility(session,window,assembly,desired);
        session.RunMacro("~ Command `ProCmdViewShowAll`");window.Repaint();session.FlushCurrentWindow();
        System.err.println("[PERSISTENT] manually_hidden_non_layer_items_shown=true stage_exclusions="+items.getarraysize());
      }
      savedPoses=captureDynamicPoses(session,assembly,requestedOccurrences);

      java.util.Map<String,double[]> preferredAnchors = parsePlannedAnchors(plannedAnchors);
      java.util.List<ArrowProjection.MovingOccurrence> arrowMoving = new java.util.ArrayList<ArrowProjection.MovingOccurrence>();
      if(!completedStageOnly)for (intseq ids : requestedOccurrences) {
        String occurrenceId = occurrencePathId(ids);
        double[] preferred = preferredAnchors.get(occurrenceId);
        arrowMoving.add(ArrowProjection.prepare(session, assembly, ids, preferred));
      }
      java.util.Map<String,double[]> occurrenceTranslations =
          parseOccurrenceTranslations(encodedTranslations);
      if(completedStageOnly){
        if(!torqueLabels.isEmpty())throw new IllegalArgumentException("Completed overview cannot carry installation torque annotations");
        for(double[] vector:occurrenceTranslations.values())for(double value:vector)
          if(value!=0)throw new IllegalArgumentException("Completed overview requires unchanged assembly positions");
      }
      for (intseq ids : requestedOccurrences) {
        String occurrenceId = occurrencePathId(ids);
        double[] vector = occurrenceTranslations.get(occurrenceId);
        if (vector == null) throw new IllegalArgumentException("Missing occurrence translation=" + occurrenceId);
        translateResolved(session, assembly, ids, vector[0], vector[1], vector[2]);
      }
      System.err.println("[PERSISTENT] translated occurrences=" + occurrencePaths
          + " vectors=" + encodedTranslations);

      applyCamera(assembly, cameraSpec);
      Double selectedFitLevel = nativeSelectedFitLevel(cameraSpec);
      if (selectedFitLevel == null)
        throw new IllegalArgumentException("Formal render requires native selected fit");
      java.util.List<ReceiverInterfaceSelection> receiverInterfaces =
          parseReceiverInterfaceSelections(receiverSelections);
      if (receiverInterfaces.isEmpty()&&!completedStageOnly)
        throw new IllegalArgumentException("Formal render requires receiver surface selections");
      double squareSafeFitLevel = squareCropSafeFitLevel(window, selectedFitLevel);
      ArrowProjection.Result arrowResult = completedStageOnly ? null : ArrowProjection.layout(
          assembly, arrowMoving, occurrenceTranslations);
      if(!completedStageOnly && (cameraSpec.contains("FIT_SCOPE:anchor_surfaces")||cameraSpec.contains("FIT_SCOPE:balanced_surfaces")||cameraSpec.contains("FIT_SCOPE:protected_surfaces")||cameraSpec.contains("FIT_SCOPE:protected_groups"))) {
        String nativeFaces=null;
        try{nativeFaces=ArrowProjection.nearestInterfaceSelections(session,assembly,arrowMoving);}
        catch(IllegalStateException unavailableFaces){
          if(!unavailableFaces.getMessage().startsWith("Native installation face unavailable:"))throw unavailableFaces;
          System.err.println("[PERSISTENT] installation_focus_fallback=moving_component reason=native_face_unavailable");
        }
        if(nativeFaces!=null){
          java.util.List<ReceiverInterfaceSelection> faceSelections=parseReceiverInterfaceSelections(nativeFaces);
          boolean groupFit=cameraSpec.contains("FIT_SCOPE:protected_groups");
          boolean protectedFit=groupFit||cameraSpec.contains("FIT_SCOPE:protected_surfaces");
          double margin=(cameraSpec.contains("FIT_SCOPE:balanced_surfaces")||protectedFit)&&torqueLabels.isEmpty()?balancedFaceMargin(assembly,requestedOccurrences,groupFit?java.util.Collections.<ReceiverInterfaceSelection>emptyList():faceSelections,protectedFit?arrowResult:null):1.;
          applyNativeSelectedFit(session,window,assembly,groupFit?requestedOccurrences:java.util.Collections.<intseq>emptyList(),
              groupFit?java.util.Collections.<ReceiverInterfaceSelection>emptyList():faceSelections,Math.max(.001,squareSafeFitLevel/margin));
          if(cameraSpec.contains("ROI_SPAN:")&&!cameraSpec.contains("ROI_ROLE:location")){
            double span=0;for(String term:cameraSpec.split(","))if(term.startsWith("ROI_SPAN:"))span=Double.parseDouble(term.substring(9));
            applyCadRoiFit(session,window,assembly,arrowResult,span,outputJpeg,null,nativeDetailFrameFraction(cameraSpec));
          }
        }else{
          applyNativeSelectedFit(session,window,assembly,requestedOccurrences,
              java.util.Collections.<ReceiverInterfaceSelection>emptyList(),squareCropSafeFitLevel(window,1.35));
        }
        Files.writeString(Path.of(outputJpeg+".focus.json"),"{\"schema_version\":\"native-installation-focus/v1\",\"strategy\":\""+(nativeFaces==null?"component_fallback":"nearest_anchor_faces")+"\",\"selections\":\""+(nativeFaces==null?"":nativeFaces)+"\"}",java.nio.charset.StandardCharsets.UTF_8);
      } else if(!completedStageOnly && cameraSpec.contains("FIT_SCOPE:moving_interfaces")) {
        applyNativeSelectedFit(session,window,assembly,java.util.Collections.<intseq>emptyList(),
            parseReceiverInterfaceSelections(ArrowProjection.interfaceSelections(arrowResult)),squareSafeFitLevel);
      } else if(!completedStageOnly && cameraSpec.contains("FIT_SCOPE:moving_only")) {
        applyNativeSelectedFit(session,window,assembly,requestedOccurrences,
            java.util.Collections.<ReceiverInterfaceSelection>emptyList(),squareSafeFitLevel);
      } else {
        applyNativeSelectedFit(session, window, assembly, completedStageOnly ? visibleOccurrencePaths : requestedOccurrences,
            receiverInterfaces, squareSafeFitLevel);
      }
      if(completedStageOnly&&cameraSpec.contains("STAGE_BOUNDS:")){
        java.util.List<double[]> boxes=new java.util.ArrayList<>();
        for(String term:cameraSpec.split(","))if(term.startsWith("STAGE_BOUNDS:"))for(String encoded:term.substring(13).split(";")){
          String[] values=encoded.split(":");if(values.length!=6)throw new IllegalArgumentException("Invalid completed stage bounds");
          double[] b=new double[6];for(int a=0;a<6;a++)b[a]=Double.parseDouble(values[a]);boxes.add(b);
        }
        double[] center=new double[3];for(double[] b:boxes)for(int a=0;a<3;a++)center[a]+=(b[a]+b[a+3])*.5/boxes.size();
        ArrowProjection.Result projection=ArrowProjection.completedProjection(assembly,center);
        double[] plane={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
        for(double[] b:boxes)for(int mask=0;mask<8;mask++){
          double x=0,y=0;for(int a=0;a<3;a++){double v=b[(mask&(1<<a))==0?a:a+3];x+=v*projection.right[a];y+=v*projection.up[a];}
          plane[0]=Math.min(plane[0],x);plane[1]=Math.min(plane[1],y);plane[2]=Math.max(plane[2],x);plane[3]=Math.max(plane[3],y);
        }
        double feature=.02*Math.max(plane[2]-plane[0],plane[3]-plane[1]);
        applyCadRoiFit(session,window,assembly,projection,feature,outputJpeg,plane,.82);
      }
      // Freeze native annotation anchors before diagnostic visibility toggles.
      // Fit and drawing must use the same solid-surface selections; re-querying
      // after simplified-representation regeneration can select another point.
      java.util.Map<String,double[]> frozenAnnotationTargets=null;
      if(!completedStageOnly&&!torqueLabels.isEmpty())frozenAnnotationTargets=
          cameraSpec.contains("NOTE_LAYOUT:spatial_perimeter")?ArrowProjection.completeAnnotationTargets(arrowMoving):ArrowProjection.annotationTargets(assembly,arrowMoving,arrowResult.back);
      java.util.List<double[]> frozenAnnotationBounds=new java.util.ArrayList<>();
      NativeTorqueAnnotations.AnnotationLayout frozenAnnotationLayout=null;
      if(frozenAnnotationTargets!=null){
        for(String term:torqueBounds.split(";")){String[] values=term.split(":");if(values.length!=6)throw new IllegalArgumentException("Invalid annotation bounds");double[] box=new double[6];for(int i=0;i<6;i++){box[i]=Double.parseDouble(values[i]);if(!Double.isFinite(box[i]))throw new IllegalArgumentException("Nonfinite annotation bounds");}frozenAnnotationBounds.add(box);}
        frozenAnnotationLayout=NativeTorqueAnnotations.prepareLayout(arrowResult,frozenAnnotationTargets,frozenAnnotationBounds,torqueLabels,cameraSpec.contains("NOTE_LAYOUT:spatial_perimeter"));
      }
      if(!completedStageOnly&&cameraSpec.contains("ROI_ROLE:location")){
        double feature=0;for(String term:cameraSpec.split(","))if(term.startsWith("ROI_SPAN:"))feature=Double.parseDouble(term.substring(9));
        java.util.List<double[]> annotationBounds=new java.util.ArrayList<>();
        for(String term:torqueBounds.split(";")){String[] v=term.split(":");if(v.length!=6)throw new IllegalArgumentException("Invalid locator protected bounds");double[] box=new double[6];for(int i=0;i<6;i++)box[i]=Double.parseDouble(v[i]);annotationBounds.add(box);}
        double[] plane=NativeTorqueAnnotations.protectedCameraBounds(frozenAnnotationLayout,frozenAnnotationBounds);
        applyCadRoiFit(session,window,assembly,arrowResult,feature,outputJpeg,plane,nativeDetailFrameFraction(cameraSpec));
      }
      if(!completedStageOnly&&!torqueLabels.isEmpty()&&cameraSpec.contains("ROI_SPAN:")&&!cameraSpec.contains("ROI_ROLE:location")){
        double feature=0;for(String term:cameraSpec.split(","))if(term.startsWith("ROI_SPAN:"))feature=Double.parseDouble(term.substring(9));
        java.util.List<double[]> annotationBounds=new java.util.ArrayList<>();
        for(String term:torqueBounds.split(";")){String[] v=term.split(":");if(v.length!=6)throw new IllegalArgumentException("Invalid label bounds");double[] box=new double[6];for(int i=0;i<6;i++)box[i]=Double.parseDouble(v[i]);annotationBounds.add(box);}
        double[] plane=NativeTorqueAnnotations.labelCameraBounds(frozenAnnotationLayout);
        applyCadRoiFit(session,window,assembly,arrowResult,feature,outputJpeg,plane,nativeDetailFrameFraction(cameraSpec));
      }
      if(!ghostReceivers.isEmpty()){
        if(completedStageOnly)throw new IllegalArgumentException("Completed locator must remain opaque");
        receiverAppearance.apply(session,assembly,ghostReceivers);
        window.Repaint();session.FlushCurrentWindow();
      }
      if(!completedStageOnly)ArrowProjection.writeAudit(arrowResult, arrowAuditJson);
      else {
        java.util.List<String> paths=new java.util.ArrayList<>();for(String p:desired)if(!p.startsWith("!"))paths.add(p);java.util.Collections.sort(paths);
        String visibleJson=paths.stream().map(p->"\""+p+"\"").collect(java.util.stream.Collectors.joining(","));
        Files.writeString(Path.of(arrowAuditJson),"{\"schema_version\":\"creo-completed-stage/v1\",\"position_state\":\"authoritative_complete\",\"rotation_unchanged\":true,\"installation_arrows\":false,\"visible_occurrences\":["+visibleJson+"]}",java.nio.charset.StandardCharsets.UTF_8);
      }
      if(cameraSpec.contains("ARROW_EVIDENCE:background_difference")){
        session.UIClearMessage();window.Repaint();session.FlushCurrentWindow();
        JPEGImageExportInstructions backgroundInstructions=squareCropSourceExportInstructions(window);
        backgroundInstructions.SetDotsPerInch(DotsPerInch.RASTERDPI_200);backgroundInstructions.SetImageDepth(RasterDepth.RASTERDEPTH_24);
        Path backgroundSource=Path.of(outputJpeg+".background-source.jpg");window.ExportRasterImage(backgroundSource.toString(),backgroundInstructions);
        cropCenterSquare(backgroundSource,Path.of(outputJpeg+".background.jpg"));
      }
      if(cameraSpec.contains("MOVING_EVIDENCE:occurrence_difference"))
        writeMovingVisibilityEvidence(session,window,assembly,jobRep,items,requestedOccurrences,visibleOccurrencePaths,arrowResult,outputJpeg);
      if (drawNativeArrow) arrowDisplay = ArrowProjection.display(session, arrowResult);
      if (!torqueLabels.isEmpty()) {
        java.util.List<double[]> bounds = new java.util.ArrayList<>();
        for (String term : torqueBounds.split(";")) {
          String[] values = term.split(":");
          if (values.length != 6) throw new IllegalArgumentException("Invalid torque protected bounds");
          double[] box = new double[6];
          for (int i=0;i<6;i++) { box[i]=Double.parseDouble(values[i]); if(!Double.isFinite(box[i]))throw new IllegalArgumentException("Nonfinite torque bounds"); }
          bounds.add(box);
        }
        torqueDisplay = NativeTorqueAnnotations.display(session,frozenAnnotationLayout,
            torqueContractHash, outputJpeg + (cameraSpec.contains("NOTE_KIND:installation") ? ".note.json" : ".torque.json"),cameraSpec.contains("NOTE_LAYOUT:spatial_perimeter"));
      }
      session.UIClearMessage(); window.Repaint(); session.FlushCurrentWindow();
      JPEGImageExportInstructions instructions = squareCropSourceExportInstructions(window);
      instructions.SetDotsPerInch(DotsPerInch.RASTERDPI_200);
      instructions.SetImageDepth(RasterDepth.RASTERDEPTH_24);
      Path squareCropSource = Path.of(outputJpeg + ".native-source.jpg");
      Files.deleteIfExists(squareCropSource);
      window.ExportRasterImage(squareCropSource.toString(), instructions);
      cropCenterSquare(squareCropSource, Path.of(outputJpeg));
      Files.writeString(Path.of(outputJpeg+".visibility-reset.json"),"{\"schema_version\":\"native-source-visibility-reset/v1\",\"command\":\"ProCmdViewShowAll\",\"stage_exclusion_count\":"+items.getarraysize()+",\"auxiliary_layer_retained\":true,\"image_sha256\":"+jsonString(fileHash(outputJpeg))+"}");
      if(cameraSpec.contains("ARROW_EVIDENCE:background_difference")){
        String backgroundHash=java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(Path.of(outputJpeg+".background.jpg"))));
        String imageHash=java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(Path.of(outputJpeg))));
        Files.writeString(Path.of(outputJpeg+".raster-evidence.json"),"{\"schema_version\":\"native-background-difference/v1\",\"camera_and_stage_unchanged\":true,\"background_sha256\":\""+backgroundHash+"\",\"image_sha256\":\""+imageHash+"\"}",java.nio.charset.StandardCharsets.UTF_8);
      }
      System.err.println("[PERSISTENT] wrote=" + outputJpeg + " audit=" + arrowAuditJson);
      completed=true;
    }
    finally {
      Throwable arrowCleanupFailure = null;
      if (torqueDisplay != null) {
        try { torqueDisplay.Delete(); NativeTorqueAnnotations.restoreColors(session); } catch (Throwable error) { arrowCleanupFailure=error; }
      }
      if (arrowDisplay != null) {
        try {
          arrowDisplay.Delete();
          ArrowProjection.restoreColors(session);
          if (window != null) {
            window.Repaint();
            session.FlushCurrentWindow();
          }
        } catch (Throwable error) {
          arrowCleanupFailure = error;
          System.err.println("[PERSISTENT] ARROW_DISPLAY_CLEANUP_FAILED " + error);
        }
      }
      try {
        if(stageAssembly!=null && savedPoses!=null){
          restoreDynamicPoses(session,stageAssembly,savedPoses,"!none");
          for(DynamicPose item:savedPoses){
            Transform3D actual=item.componentPathApi
                ?pfcAssembly.CreateComponentPath(stageAssembly,item.ids).GetTransform(true)
                :resolveComponent(session,stageAssembly,item.ids).feature.GetPosition();
            double[][] expected=matrix(item.pose),restored=matrix(actual);
            for(int r=0;r<4;r++)for(int c=0;c<4;c++)
              if(Math.abs(expected[r][c]-restored[r][c])>1e-6)
                throw new IllegalStateException("POSE_RESTORE_MISMATCH: "+item.occurrenceId);
          }
          System.err.println("[PERSISTENT] poses_restored="+savedPoses.size());
        }
        if(stageAssembly!=null && jobRep!=null){
          stageAssembly.ActivateSimpRep(previousRep!=null?previousRep:stageAssembly.GetMasterRep());
          try{stageAssembly.DeleteSimpRep(jobRep);}
          catch(com.ptc.pfc.pfcExceptions.XToolkitNotFound alreadyRemoved){
            // Creo automatically removes a temporary rep when another is
            // activated. A missing rep here is the desired cleanup state.
            System.err.println("[PERSISTENT] temporary_stage_auto_removed=true");
          }
        }
        if(completed&&!ghostReceivers.isEmpty()){
          discardedDisplayModels=discardAssemblyDisplayModels(session);
          if(!ghostSourceHash.equals(fileHash(assemblyFile)))throw new IllegalStateException("Source assembly changed during disposable display rendering");
        }
      }catch(Throwable error){arrowCleanupFailure=error;}
      if(!completed || arrowCleanupFailure!=null || cameraSpec.contains("PHYSICAL_DISPLAY:explicit_include")){
        try{discardRetainedAssembly(session);}catch(Throwable error){if(arrowCleanupFailure==null)arrowCleanupFailure=error;}
      }
      if (arrowCleanupFailure != null)
        throw new IllegalStateException("ARROW_DISPLAY_CLEANUP_FAILED", arrowCleanupFailure);
      if(completed&&!ghostReceivers.isEmpty()){
        String pathsJson=java.util.Arrays.stream(ghostReceivers.split(";")).map(s->jsonString(s)).collect(java.util.stream.Collectors.joining(","));
        String erasedJson=discardedDisplayModels.stream().map(s->jsonString(s)).collect(java.util.stream.Collectors.joining(","));
        Files.writeString(Path.of(outputJpeg+".ghost.json"),"{\"schema_version\":\"native-receiver-ghost/v1\",\"occurrence_ids\":["+pathsJson+"],\"command\":\"ProCmdViewTranspShaded\",\"reset_strategy\":\"discarded_assembly_display_models/v1\",\"display_state_discarded\":true,\"assembly_count_after\":0,\"discarded_models\":["+erasedJson+"],\"poses_restored\":true,\"source_assembly_file\":"+jsonString(assemblyFile)+",\"source_assembly_sha256_before\":"+jsonString(ghostSourceHash)+",\"source_assembly_sha256_after\":"+jsonString(fileHash(assemblyFile))+",\"image_sha256\":"+jsonString(fileHash(outputJpeg))+"}");
      }
    }
  }

}
