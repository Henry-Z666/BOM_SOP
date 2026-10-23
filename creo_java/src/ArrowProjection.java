import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcComponentFeat.*;
import com.ptc.pfc.pfcDisplay.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcGeometry.*;
import com.ptc.pfc.pfcModelItem.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcSolid.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Deterministic same-CAD-point install arrows rendered by Creo itself. */
public final class ArrowProjection {
  private ArrowProjection() {}
  private static final AtomicInteger NEXT_DISPLAY_LIST_ID = new AtomicInteger(73101);
  private static ColorRGB priorArrowColor;
  public static void restoreColors(Session session) throws jxthrowable {
    if(priorArrowColor!=null){session.SetStdColorFromRGB(StdColor.COLOR_DRAWING,priorArrowColor);priorArrowColor=null;}
  }

  private static final class Candidate {
    final int surfaceId; final String source; final double[] local; final intseq anchorPath; double[] completeRoot;
    Candidate(int surfaceId, String source, double[] local, intseq anchorPath) { this.surfaceId = surfaceId; this.source=source; this.local = local; this.anchorPath=anchorPath; }
  }
  public static final class MovingOccurrence {
    final String id; final intseq path; final Transform3D completePose; final List<Candidate> candidates;
    MovingOccurrence(String id, intseq path, Transform3D completePose, List<Candidate> candidates) {
      this.id=id; this.path=path; this.completePose=completePose; this.candidates=candidates;
    }
  }
  private static final class Arrow {
    final Candidate anchor; final double[] complete, exploded, complete2, exploded2;
    final List<String> covered = new ArrayList<>();
    Arrow(Candidate anchor, double[] complete, double[] exploded, double[] complete2, double[] exploded2, String id) {
      this.anchor=anchor; this.complete=complete; this.exploded=exploded; this.complete2=complete2; this.exploded2=exploded2; covered.add(id);
    }
  }
  public static final class Result {
    final List<Arrow> arrows; final double[] right, up, back;
    Result(List<Arrow> arrows, double[] right, double[] up, double[] back) { this.arrows=arrows; this.right=right; this.up=up; this.back=back; }
  }

  private static double[] point(Point3D p) throws jxthrowable { return new double[]{p.get(0),p.get(1),p.get(2)}; }
  private static Point3D point(double[] value) throws jxthrowable { Point3D p=Point3D.create(); for(int i=0;i<3;i++) p.set(i,value[i]); return p; }
  private static double dot(double[] a,double[] b){if(a.length!=b.length)throw new IllegalArgumentException("Vector dimensions differ");double value=0;for(int i=0;i<a.length;i++)value+=a[i]*b[i];return value;}
  private static double[] add(double[] a,double[] b){if(a.length!=b.length)throw new IllegalArgumentException("Vector dimensions differ");double[] value=new double[a.length];for(int i=0;i<a.length;i++)value[i]=a[i]+b[i];return value;}
  private static double[] sub(double[] a,double[] b){if(a.length!=b.length)throw new IllegalArgumentException("Vector dimensions differ");double[] value=new double[a.length];for(int i=0;i<a.length;i++)value[i]=a[i]-b[i];return value;}
  private static double[] mul(double[] a,double s){double[] value=new double[a.length];for(int i=0;i<a.length;i++)value[i]=a[i]*s;return value;}
  private static double norm(double[] a){return Math.sqrt(dot(a,a));}
  private static double[] normalize(double[] a){double n=norm(a); if(n<1e-9) throw new IllegalArgumentException("Degenerate arrow projection"); return mul(a,1.0/n);}
  private static double[] cross(double[] a,double[] b){return new double[]{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]};}
  private static String pathId(intseq ids) throws jxthrowable { StringBuilder b=new StringBuilder(); for(int i=0;i<ids.getarraysize();i++){if(i>0)b.append('/');b.append(ids.get(i));}return b.toString();}
  private static double[] transform(Transform3D pose,double[] local) throws jxthrowable{return point(pose.TransformPoint(point(local)));}
  private static double[] surfaceAnchor(Surface surface) throws jxthrowable {
    // A part-level outline centre may be arbitrarily far from an individual
    // face and is rejected by some Creo face evaluators.  Seed the closest
    // point query from this surface's own XYZ extent instead.
    Outline3D extent=surface.GetXYZExtents(); double[] low=point(extent.get(0)),high=point(extent.get(1));
    double[] sample=new double[]{(low[0]+high[0])/2.0,(low[1]+high[1])/2.0,(low[2]+high[2])/2.0};
    return point(surface.EvalClosestPointOnSurface(point(sample)));
  }

  private static boolean pointWithinOutline(Solid solid,double[] local) throws jxthrowable {
    Outline3D outline=solid.GetGeomOutline(); double[] low=point(outline.get(0)),high=point(outline.get(1));
    double diagonal=norm(sub(high,low)),tolerance=Math.max(1.0e-5,diagonal*1.0e-6);
    for(int i=0;i<3;i++) if(local[i]<low[i]-tolerance||local[i]>high[i]+tolerance) return false;
    return true;
  }

  /** Native toroidal faces may reject the XYZ extent-centre projection.
   * Reuse discovery's UV/extremum surface evaluation before declaring that a
   * genuine CAD solid has no physical annotation anchor. */
  private static double[] physicalSurfaceAnchor(Solid solid,Surface surface) throws jxthrowable {
    try { double[] p=surfaceAnchor(surface);if(pointWithinOutline(solid,p))return p; }
    catch(Throwable ignored) { }
    try {
      UVOutline extent=surface.GetUVExtents();UVParams low=extent.get(0),high=extent.get(1),sample=UVParams.create();
      for(int i=0;i<2;i++)sample.set(i,(low.get(i)+high.get(i))/2.0);
      Point3D evaluated=surface.Eval3DData(sample).GetPoint();
      double[] p=point(surface.EvalClosestPointOnSurface(evaluated));
      if(pointWithinOutline(solid,p))return p;
    } catch(Throwable ignored) { }
    for(int axis=0;axis<3;axis++)for(double sign:new double[]{-1.,1.}) {
      try {
        Vector3D direction=Vector3D.create();for(int i=0;i<3;i++)direction.set(i,i==axis?sign:0.);
        double[] p=point(surface.EvalMinimum(direction));if(pointWithinOutline(solid,p))return p;
      } catch(Throwable ignored) { }
    }
    throw new IllegalStateException("No native surface sample within physical solid outline");
  }

  private static intseq appendPath(intseq prefix, int componentId) throws jxthrowable {
    intseq result=intseq.create();
    for(int i=0;i<prefix.getarraysize();i++) result.append(prefix.get(i));
    result.append(componentId); return result;
  }

  /** Adds deterministic anchors from one physical solid occurrence. */
  private static void collectSolidCandidates(Solid leaf,intseq path,String source,List<Candidate> candidates,int[] sampleFailures) throws jxthrowable {
    try {
      SolidBody body=leaf.GetDefaultBody(); Surfaces surfaces=body==null?null:body.ListSurfaces();
      for(int i=0;surfaces!=null&&i<surfaces.getarraysize();i++){
        Surface surface=surfaces.get(i);
        try { candidates.add(new Candidate(surface.GetId(),source,physicalSurfaceAnchor(leaf,surface),path)); }
        catch(Throwable ignored) { sampleFailures[0]++; }
      }
    } catch(Throwable ignored) { sampleFailures[0]++; }
    if(!candidates.isEmpty()) return;
    try {
      ModelItems surfaces=leaf.ListItems(ModelItemType.ITEM_SURFACE);
      for(int i=0;surfaces!=null&&i<surfaces.getarraysize();i++){
        Surface surface=(Surface)surfaces.get(i);
        try { candidates.add(new Candidate(surface.GetId(),source.replace("model_","body_"),physicalSurfaceAnchor(leaf,surface),path)); }
        catch(Throwable ignored) { sampleFailures[0]++; }
      }
    } catch(Throwable ignored) { sampleFailures[0]++; }
  }

  /**
   * A moving occurrence may be a rigid subassembly rather than a solid part.
   * In that case, use the first deterministic descendant solid as its physical
   * same-CAD-point anchor, while the parent occurrence remains the moved group.
   */
  private static boolean collectDescendantAnchor(Session session,Assembly assembly,intseq prefix,List<Candidate> candidates,int[] sampleFailures) throws jxthrowable {
    Features components=assembly.ListFeaturesByType(Boolean.FALSE,FeatureType.FEATTYPE_COMPONENT);
    List<ComponentFeat> ordered=new ArrayList<>();
    for(int i=0;components!=null&&i<components.getarraysize();i++) ordered.add((ComponentFeat)components.get(i));
    ordered.sort(new Comparator<ComponentFeat>() { public int compare(ComponentFeat a,ComponentFeat b) {
      try { return Integer.compare(a.GetId(),b.GetId()); }
      catch(jxthrowable error) { throw new RuntimeException(error); }
    }});
    for(ComponentFeat component:ordered){
      intseq childPath=appendPath(prefix,component.GetId());
      try {
        Model child=session.RetrieveModel(component.GetModelDescr());
        int before=candidates.size();
        if(child instanceof Assembly) {
          if(collectDescendantAnchor(session,(Assembly)child,childPath,candidates,sampleFailures)) return true;
        } else if(child instanceof Solid) {
          collectSolidCandidates((Solid)child,childPath,"descendant_model_surface",candidates,sampleFailures);
          if(candidates.size()>before) return true;
        }
      } catch(Throwable ignored) { sampleFailures[0]++; }
    }
    return false;
  }

  public static MovingOccurrence prepare(Session session,Assembly root, intseq ids) throws jxthrowable {
    return prepare(session, root, ids, null);
  }

  public static MovingOccurrence prepare(Session session,Assembly root, intseq ids,double[] preferredCompleteRoot) throws jxthrowable {
    ComponentPath componentPath=pfcAssembly.CreateComponentPath(root,ids);
    Solid leaf=componentPath.GetLeaf(); Transform3D complete=componentPath.GetTransform(true);
    List<Candidate> candidates=new ArrayList<>();
    int[] sampleFailures=new int[]{0};
    if(preferredCompleteRoot!=null){
      if(preferredCompleteRoot.length!=3) throw new IllegalArgumentException("planned arrow anchor must have three coordinates");
      Transform3D inverse=pfcBase.Transform3D_Create(complete.GetMatrix()); inverse.Invert();
      double[] plannedLocal=transform(inverse,preferredCompleteRoot);
      if(pointWithinOutline(leaf,plannedLocal)) {
        Candidate planned=new Candidate(-2,"planned_constraint_anchor",plannedLocal,ids);
        planned.completeRoot=preferredCompleteRoot.clone(); candidates.add(planned);
      } else {
        System.err.println("[RENDER] planned_arrow_anchor_rejected occurrence="+pathId(ids)+" reason=outside_solid_outline");
        if(leaf instanceof Assembly)
          collectDescendantAnchor(session,(Assembly)leaf,ids,candidates,sampleFailures);
        else collectSolidCandidates(leaf,ids,"model_surface_after_planned_rejection",candidates,sampleFailures);
      }
    } else {
      if(leaf instanceof Assembly)
        collectDescendantAnchor(session,(Assembly)leaf,ids,candidates,sampleFailures);
      else collectSolidCandidates(leaf,ids,"model_surface",candidates,sampleFailures);
    }
    candidates.sort(Comparator.comparingInt(c->c.surfaceId));
    List<Candidate> unique=new ArrayList<>();
    for(Candidate candidate:candidates){boolean duplicate=false;for(Candidate prior:unique)if(norm(sub(candidate.local,prior.local))<1e-5){duplicate=true;break;}if(!duplicate)unique.add(candidate);}
    if(unique.isEmpty()) throw new IllegalStateException("no_physical_arrow_anchor occurrence="+pathId(ids));
    System.err.println("[RENDER] arrow_anchor_candidates occurrence="+pathId(ids)+" count="+unique.size()+" surface_sample_failures="+sampleFailures[0]);
    for(Candidate candidate:unique) if(candidate.completeRoot==null) candidate.completeRoot=transform(pfcAssembly.CreateComponentPath(root,candidate.anchorPath).GetTransform(true),candidate.local);
    return new MovingOccurrence(pathId(ids),ids,complete,unique);
  }

  private static double[] column(Transform3D view,int column) throws jxthrowable { Matrix3D m=view.GetMatrix(); return normalize(new double[]{m.get(0,column),m.get(1,column),m.get(2,column)}); }
  private static double pointSegmentDistance(double[] p,double[] a,double[] b){double[] ab=sub(b,a);double d=dot(ab,ab);double t=d<1e-12?0:Math.max(0,Math.min(1,dot(sub(p,a),ab)/d));return norm(sub(p,add(a,mul(ab,t))));}
  private static double segmentDistance(double[] a,double[] b,double[] c,double[] d){return Math.min(Math.min(pointSegmentDistance(a,c,d),pointSegmentDistance(b,c,d)),Math.min(pointSegmentDistance(c,a,b),pointSegmentDistance(d,a,b)));}
  static double arrowHeadLength(double projectedLength) {
    return Math.min(projectedLength*0.35,Math.max(3.0,Math.min(18.0,projectedLength*0.18)));
  }

  public static Result layout(Assembly root,List<MovingOccurrence> moving,Map<String,double[]> translationsRoot) throws jxthrowable {
    Transform3D view=root.GetCurrentViewTransform(); double[] right=column(view,0),up=column(view,1),back=column(view,2);
    moving.sort(Comparator.comparing(m->m.id)); List<Arrow> selected=new ArrayList<>();
    for(MovingOccurrence occurrence:moving){
      double[] translationRoot=translationsRoot.get(occurrence.id);
      if(translationRoot==null||translationRoot.length!=3)
        throw new IllegalArgumentException("missing occurrence translation="+occurrence.id);
      List<Arrow> choices=new ArrayList<>();
      for(Candidate anchor:occurrence.candidates){
        // Do not manufacture the exploded endpoint from the contract vector.
        // Read the actual post-SetTransform occurrence pose and transform the
        // same local CAD point through both poses.
        Transform3D explodedPose=pfcAssembly.CreateComponentPath(root,anchor.anchorPath).GetTransform(true);
        double[] complete=anchor.completeRoot, exploded=transform(explodedPose,anchor.local);
        if(norm(sub(sub(exploded,complete),translationRoot))>1.0e-5)
          throw new IllegalStateException("arrow_actual_transform_mismatch occurrence="+occurrence.id);
        double[] complete2=new double[]{dot(complete,right),dot(complete,up)}, exploded2=new double[]{dot(exploded,right),dot(exploded,up)};
        if(norm(sub(complete2,exploded2))>1e-6) choices.add(new Arrow(anchor,complete,exploded,complete2,exploded2,occurrence.id));
      }
      choices.sort((a,b)->Double.compare(dot(b.complete,back),dot(a.complete,back)));
      Arrow accepted=null; Arrow conflict=null;
      for(Arrow choice:choices){boolean clear=true;for(Arrow prior:selected){
        // The current thick red head extends 0.60*head on either side.
        // Test those actual footprints, not the obsolete thin-arrow margin.
        // Parallel identical translations may share an explicitly audited
        // arrow; every covered occurrence remains in the coverage record.
        double threshold=Math.max(2.0,.60*(arrowHeadLength(norm(sub(choice.complete2,choice.exploded2)))+arrowHeadLength(norm(sub(prior.complete2,prior.exploded2)))));
        if(segmentDistance(choice.exploded2,choice.complete2,prior.exploded2,prior.complete2)<threshold){clear=false;conflict=prior;break;}}
        if(clear){accepted=choice;break;}}
      if(accepted!=null) selected.add(accepted);
      else if(conflict!=null) {
        double[] conflictVector=sub(conflict.exploded,conflict.complete);
        if(norm(sub(conflictVector,translationRoot))>1.0e-5)
          throw new IllegalStateException("arrow_layout_conflict_has_different_translation occurrence="+occurrence.id);
        conflict.covered.add(occurrence.id);
      }
      else throw new IllegalStateException("arrow_layout_failed occurrence="+occurrence.id);
    }
    return new Result(selected,right,up,back);
  }

  /** A zero-length projection reference for camera calibration, never displayed. */
  public static Result completedProjection(Assembly root,double[] cadPoint) throws jxthrowable {
    Transform3D view=root.GetCurrentViewTransform();double[] right=column(view,0),up=column(view,1),back=column(view,2);
    double[] plane={dot(cadPoint,right),dot(cadPoint,up)};
    List<Arrow> refs=new ArrayList<>();refs.add(new Arrow(null,cadPoint,cadPoint,plane,plane,"camera-reference"));
    return new Result(refs,right,up,back);
  }

  private static final class Listener extends DefaultDisplayListener {
    private final Result result; Listener(Result result){this.result=result;}
    @Override public void OnDisplay(Display display) throws jxthrowable {
      display.SetCurrentGraphicsMode(GraphicsMode.DRAW_GRAPHICS_NORMAL);
      display.SetCurrentGraphicsColor(StdColor.COLOR_DRAWING);
      for(Arrow arrow:result.arrows){
        // Endpoints are the real same-CAD-point positions.  Any visual offset
        // would make the arrow appear detached from the moving occurrence.
        double[] start=arrow.exploded, end=arrow.complete;
        double[] projected=sub(sub(start,end),mul(result.back,dot(sub(start,end),result.back)));
        double projectedLength=norm(projected); double head=arrowHeadLength(projectedLength);
        double[] axis=normalize(projected),perp=normalize(cross(result.back,axis));double[] base=add(end,mul(axis,head));
        // Native display strokes fill the shaft and triangular head. Keep the
        // centreline anchored to the same CAD points; no raster annotation.
        double halfWidth=head*0.32;
        for(int stroke=0;stroke<=256;stroke++){
          double offset=halfWidth*(2.0*stroke/256.0-1.0);
          display.SetPenPosition(point(add(start,mul(perp,offset))));
          display.DrawLine(point(add(base,mul(perp,offset))));
          double headOffset=head*0.60*(2.0*stroke/256.0-1.0);
          display.SetPenPosition(point(end));
          display.DrawLine(point(add(base,mul(perp,headOffset))));
        }
      }
    }
  }

  public static DisplayList3D display(Session session,Result result) throws jxthrowable {
    priorArrowColor=session.GetRGBFromStdColor(StdColor.COLOR_DRAWING);
    session.SetStdColorFromRGB(StdColor.COLOR_DRAWING,pfcBase.ColorRGB_Create(0.90,0.04,0.04));
    DisplayList3D list=session.CreateDisplayList3D(NEXT_DISPLAY_LIST_ID.getAndIncrement(),new Listener(result));
    Matrix3D m=Matrix3D.create();for(int r=0;r<4;r++)for(int c=0;c<4;c++)m.set(r,c,r==c?1.0:0.0);
    list.Display(pfcBase.Transform3D_Create(m)); return list;
  }
  public static String interfaceSelections(Result result) throws jxthrowable {
    java.util.List<String> selections=new java.util.ArrayList<>();
    for(Arrow arrow:result.arrows)selections.add(pathId(arrow.anchor.anchorPath)+"#"+arrow.anchor.surfaceId);
    return String.join(";",new java.util.LinkedHashSet<>(selections));
  }
  /** Select physical faces near the locked CAD anchor, even for synthetic
   * constraint anchors (-2). The arrow itself keeps its original CAD point. */
  private static final class FaceChoice {String selection;double score=Double.POSITIVE_INFINITY;}
  private static void nearestFace(Session session,Assembly root,intseq ids,double[] anchorRoot,FaceChoice choice) throws jxthrowable {
      ComponentPath path=pfcAssembly.CreateComponentPath(root,ids);Solid leaf=path.GetLeaf();
      if(leaf instanceof Assembly){
        Features children=((Assembly)leaf).ListFeaturesByType(Boolean.FALSE,FeatureType.FEATTYPE_COMPONENT);
        for(int i=0;children!=null&&i<children.getarraysize();i++){
          ComponentFeat child=(ComponentFeat)children.get(i);
          try{session.RetrieveModel(child.GetModelDescr());nearestFace(session,root,appendPath(ids,child.GetId()),anchorRoot,choice);}
          catch(jxthrowable unavailableChild){}
        }
        return;
      }
      Transform3D pose=path.GetTransform(true);
      Transform3D inverse=pfcBase.Transform3D_Create(pose.GetMatrix());inverse.Invert();
      double[] local=transform(inverse,anchorRoot);
      ModelItems items=leaf.ListItems(ModelItemType.ITEM_SURFACE);
      for(int i=0;items!=null&&i<items.getarraysize();i++){
        try{
          Surface face=(Surface)items.get(i);Outline3D extent=face.GetXYZExtents();
          if(extent==null||extent.get(0)==null||extent.get(1)==null)continue;
          double[] low=point(extent.get(0)),high=point(extent.get(1));
          Point3D nearest=face.EvalClosestPointOnSurface(point(local));double[] sample=new double[3];
          for(int axis=0;axis<3;axis++)sample[axis]=Math.max(low[axis],Math.min(high[axis],local[axis]));
          double score=norm(sub(nearest==null?sample:point(nearest),local))+norm(sub(high,low))*.015;
          if(score<choice.score){choice.score=score;choice.selection=pathId(ids)+"#"+face.GetId();}
        }catch(jxthrowable unsupportedFace){}
      }
  }
  public static String nearestInterfaceSelections(Session session,Assembly root,List<MovingOccurrence> moving) throws jxthrowable {
    List<String> selected=new ArrayList<>();
    for(MovingOccurrence occurrence:moving){
      Candidate lockedAnchor=occurrence.candidates.get(0);FaceChoice choice=new FaceChoice();
      nearestFace(session,root,occurrence.path,transform(pfcAssembly.CreateComponentPath(root,lockedAnchor.anchorPath).GetTransform(true),lockedAnchor.local),choice);
      if(choice.selection==null)throw new IllegalStateException("Native installation face unavailable: "+occurrence.id);
      selected.add(choice.selection);
    }
    return String.join(";",new LinkedHashSet<>(selected));
  }
  public static Map<String,double[]> completeAnnotationTargets(List<MovingOccurrence> moving) {
    Map<String,double[]> result=new LinkedHashMap<>();for(MovingOccurrence m:moving){double[] p=m.candidates.get(0).completeRoot;if(p==null)throw new IllegalStateException("Missing complete CAD anchor");result.put(m.id,p.clone());}return result;
  }
  public static Map<String,double[]> annotationTargets(Assembly root,List<MovingOccurrence> moving,double[] back) throws jxthrowable {
    Map<String,double[]> result=new LinkedHashMap<>();
    for(MovingOccurrence occurrence:moving){
      double[] best=null;double front=Double.NEGATIVE_INFINITY;
      for(Candidate candidate:occurrence.candidates){
        double[] actual=transform(pfcAssembly.CreateComponentPath(root,candidate.anchorPath).GetTransform(true),candidate.local);
        double depth=dot(actual,back);if(depth>front){best=actual;front=depth;}
      }
      if(best==null)throw new IllegalStateException("TORQUE_CAD_ANCHOR_MISSING");
      result.put(occurrence.id,best);
    }
    return result;
  }
  public static List<double[]> annotationProtectedSegments(Result result) {
    List<double[]> segments=new ArrayList<>();
    for(Arrow arrow:result.arrows)segments.add(new double[]{arrow.complete2[0],arrow.complete2[1],arrow.exploded2[0],arrow.exploded2[1]});
    return segments;
  }
  public static double annotationFeatureSpan(Result result,List<double[]> bounds,int movingCount) {
    if(result.arrows.isEmpty()||bounds.size()<movingCount)throw new IllegalArgumentException("Missing annotation feature geometry");
    double[] axis=normalize(sub(result.arrows.get(0).exploded,result.arrows.get(0).complete));double body=0,thickness=0;
    for(int i=0;i<movingCount;i++){double[] b=bounds.get(i);double t=0;for(int a=0;a<3;a++){double d=b[a+3]-b[a];body=Math.max(body,d);t+=Math.abs(axis[a])*d;}thickness=Math.max(thickness,t);}
    return Math.min(body,2*thickness);
  }
  /** Keep compact components whole; long members can still use local detail. */
  public static double[] compactMovingCameraBounds(Assembly root,Result result,double featureSpan) throws jxthrowable {
    double[] roi={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};boolean found=false;
    for(Arrow arrow:result.arrows)if(arrow.anchor!=null)for(String id:arrow.covered){
      intseq ids=intseq.create();for(String token:id.split("/"))ids.append(Integer.parseInt(token));
      ComponentPath path=pfcAssembly.CreateComponentPath(root,ids);Outline3D outline=path.GetLeaf().GetGeomOutline();
      double span=0;for(int axis=0;axis<3;axis++)span=Math.max(span,outline.get(1).get(axis)-outline.get(0).get(axis));
      if(span>Math.max(40.,4*featureSpan))continue;
      Transform3D pose=path.GetTransform(true);
      for(int mask=0;mask<8;mask++){double[] local=new double[3];for(int axis=0;axis<3;axis++)local[axis]=outline.get((mask&(1<<axis))==0?0:1).get(axis);double[] point=transform(pose,local);
        double x=dot(point,result.right),y=dot(point,result.up);roi[0]=Math.min(roi[0],x);roi[1]=Math.min(roi[1],y);roi[2]=Math.max(roi[2],x);roi[3]=Math.max(roi[3],y);found=true;
      }
    }
    return found?roi:null;
  }
  private static String number(double value){return String.format(Locale.ROOT,"%.9f",value);}
  private static String vector(double[] value){return "["+number(value[0])+","+number(value[1])+","+number(value[2])+"]";}
  private static String vector2(double[] value){return "["+number(value[0])+","+number(value[1])+"]";}
  private static String quote(String value){return "\""+value.replace("\\","\\\\").replace("\"","\\\"")+"\"";}
  public static void writeAudit(Result result,String output) throws Exception {
    StringBuilder json=new StringBuilder("{\n  \"schema_version\": \"arrow-projection/v1\",\n  \"policy\": \"same_cad_point/v1\",\n  \"arrows\": [\n");
    for(int i=0;i<result.arrows.size();i++){Arrow a=result.arrows.get(i);if(i>0)json.append(",\n");json.append("    {\"covered_occurrences\":[");for(int j=0;j<a.covered.size();j++){if(j>0)json.append(',');json.append(quote(a.covered.get(j)));}json.append("],\"anchor_occurrence\":").append(quote(pathId(a.anchor.anchorPath))).append(",\"anchor_local\":").append(vector(a.anchor.local)).append(",\"anchor_source\":").append(quote(a.anchor.source)).append(",\"anchor_surface_id\":").append(a.anchor.surfaceId).append(",\"complete_root\":").append(vector(a.complete)).append(",\"exploded_root\":").append(vector(a.exploded)).append(",\"complete_camera_plane\":").append(vector2(a.complete2)).append(",\"exploded_camera_plane\":").append(vector2(a.exploded2)).append(",\"merged\":").append(a.covered.size()>1).append('}');}
    json.append("\n  ],\n  \"status\": \"passed\"\n}\n");
    Files.writeString(Path.of(output),json.toString(),StandardCharsets.UTF_8);
  }
}
