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

/** Deterministic same-CAD-point install arrows rendered by Creo itself. */
public final class ArrowProjection {
  private ArrowProjection() {}

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
    double[] probe=new double[]{(low[0]+high[0])/2.0,(low[1]+high[1])/2.0,(low[2]+high[2])/2.0};
    return point(surface.EvalClosestPointOnSurface(point(probe)));
  }

  private static intseq appendPath(intseq prefix, int componentId) throws jxthrowable {
    intseq result=intseq.create();
    for(int i=0;i<prefix.getarraysize();i++) result.append(prefix.get(i));
    result.append(componentId); return result;
  }

  /** Adds deterministic anchors from one physical solid occurrence. */
  private static void collectSolidCandidates(Solid leaf,intseq path,String source,List<Candidate> candidates,int[] probeFailures) throws jxthrowable {
    try {
      ModelItems surfaces=leaf.ListItems(ModelItemType.ITEM_SURFACE);
      for(int i=0;surfaces!=null&&i<surfaces.getarraysize();i++){
        Surface surface=(Surface)surfaces.get(i);
        try { candidates.add(new Candidate(surface.GetId(),source,surfaceAnchor(surface),path)); }
        catch(Throwable ignored) { probeFailures[0]++; }
      }
    } catch(Throwable ignored) { probeFailures[0]++; }
    if(!candidates.isEmpty()) return;
    try {
      SolidBody body=leaf.GetDefaultBody(); Surfaces surfaces=body==null?null:body.ListSurfaces();
      for(int i=0;surfaces!=null&&i<surfaces.getarraysize();i++){
        Surface surface=surfaces.get(i);
        try { candidates.add(new Candidate(surface.GetId(),source.replace("model_","body_"),surfaceAnchor(surface),path)); }
        catch(Throwable ignored) { probeFailures[0]++; }
      }
    } catch(Throwable ignored) { probeFailures[0]++; }
  }

  /**
   * A moving occurrence may be a rigid subassembly rather than a solid part.
   * In that case, use the first deterministic descendant solid as its physical
   * same-CAD-point anchor, while the parent occurrence remains the moved group.
   */
  private static boolean collectDescendantAnchor(Session session,Assembly assembly,intseq prefix,List<Candidate> candidates,int[] probeFailures) throws jxthrowable {
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
          if(collectDescendantAnchor(session,(Assembly)child,childPath,candidates,probeFailures)) return true;
        } else if(child instanceof Solid) {
          collectSolidCandidates((Solid)child,childPath,"descendant_model_surface",candidates,probeFailures);
          if(candidates.size()>before) return true;
        }
      } catch(Throwable ignored) { probeFailures[0]++; }
    }
    return false;
  }

  public static MovingOccurrence prepare(Session session,Assembly root, intseq ids) throws jxthrowable {
    ComponentPath componentPath=pfcAssembly.CreateComponentPath(root,ids);
    Solid leaf=componentPath.GetLeaf(); Transform3D complete=componentPath.GetTransform(true);
    List<Candidate> candidates=new ArrayList<>();
    int[] probeFailures=new int[]{0};
    collectSolidCandidates(leaf,ids,"model_surface",candidates,probeFailures);
    if(candidates.isEmpty() && leaf instanceof Assembly)
      collectDescendantAnchor(session,(Assembly)leaf,ids,candidates,probeFailures);
    candidates.sort(Comparator.comparingInt(c->c.surfaceId));
    List<Candidate> unique=new ArrayList<>();
    for(Candidate candidate:candidates){boolean duplicate=false;for(Candidate prior:unique)if(norm(sub(candidate.local,prior.local))<1e-5){duplicate=true;break;}if(!duplicate)unique.add(candidate);}
    if(unique.isEmpty()) throw new IllegalStateException("arrow_anchor_failed occurrence="+pathId(ids)+" surface_probe_failures="+probeFailures[0]);
    System.err.println("[RENDER] arrow_anchor_candidates occurrence="+pathId(ids)+" count="+unique.size()+" surface_probe_failures="+probeFailures[0]);
    for(Candidate candidate:unique) candidate.completeRoot=transform(pfcAssembly.CreateComponentPath(root,candidate.anchorPath).GetTransform(true),candidate.local);
    return new MovingOccurrence(pathId(ids),ids,complete,unique);
  }

  private static double[] column(Transform3D view,int column) throws jxthrowable { Matrix3D m=view.GetMatrix(); return normalize(new double[]{m.get(0,column),m.get(1,column),m.get(2,column)}); }
  private static double pointSegmentDistance(double[] p,double[] a,double[] b){double[] ab=sub(b,a);double d=dot(ab,ab);double t=d<1e-12?0:Math.max(0,Math.min(1,dot(sub(p,a),ab)/d));return norm(sub(p,add(a,mul(ab,t))));}
  private static double segmentDistance(double[] a,double[] b,double[] c,double[] d){return Math.min(Math.min(pointSegmentDistance(a,c,d),pointSegmentDistance(b,c,d)),Math.min(pointSegmentDistance(c,a,b),pointSegmentDistance(d,a,b)));}

  public static Result layout(Assembly root,List<MovingOccurrence> moving,double[] translationRoot) throws jxthrowable {
    Transform3D view=root.GetCurrentViewTransform(); double[] right=column(view,0),up=column(view,1),back=column(view,2);
    moving.sort(Comparator.comparing(m->m.id)); List<Arrow> selected=new ArrayList<>();
    for(MovingOccurrence occurrence:moving){
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
      for(Arrow choice:choices){boolean clear=true;for(Arrow prior:selected){double threshold=Math.max(2.0,Math.min(norm(sub(choice.complete2,choice.exploded2)),norm(sub(prior.complete2,prior.exploded2)))*0.06);if(segmentDistance(choice.exploded2,choice.complete2,prior.exploded2,prior.complete2)<threshold){clear=false;conflict=prior;break;}}if(clear){accepted=choice;break;}}
      if(accepted!=null) selected.add(accepted);
      else if(conflict!=null) conflict.covered.add(occurrence.id);
      else throw new IllegalStateException("arrow_layout_failed occurrence="+occurrence.id);
    }
    return new Result(selected,right,up,back);
  }

  private static final class Listener extends DefaultDisplayListener {
    private final Result result; Listener(Result result){this.result=result;}
    @Override public void OnDisplay(Display display) throws jxthrowable {
      display.SetCurrentGraphicsMode(GraphicsMode.DRAW_GRAPHICS_NORMAL);
      display.SetCurrentGraphicsColor(StdColor.COLOR_SELECTED);
      for(Arrow arrow:result.arrows){
        // Endpoints are the real same-CAD-point positions.  Any visual offset
        // would make the arrow appear detached from the moving occurrence.
        double[] start=arrow.exploded, end=arrow.complete;
        display.SetPenPosition(point(start)); display.DrawLine(point(end));
        double[] projected=sub(sub(start,end),mul(result.back,dot(sub(start,end),result.back)));
        double projectedLength=norm(projected); double head=Math.max(2.0,Math.min(12.0,projectedLength*0.12));
        double[] axis=normalize(projected),perp=normalize(cross(result.back,axis));double[] base=add(end,mul(axis,head));
        display.SetPenPosition(point(end));display.DrawLine(point(add(base,mul(perp,head*0.42))));
        display.SetPenPosition(point(end));display.DrawLine(point(add(base,mul(perp,-head*0.42))));
      }
    }
  }

  public static DisplayList3D display(Session session,Result result) throws jxthrowable {
    DisplayList3D list=session.CreateDisplayList3D(73101,new Listener(result));
    Matrix3D m=Matrix3D.create();for(int r=0;r<4;r++)for(int c=0;c<4;c++)m.set(r,c,r==c?1.0:0.0);
    list.Display(pfcBase.Transform3D_Create(m)); return list;
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
