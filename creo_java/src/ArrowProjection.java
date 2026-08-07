import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcDisplay.*;
import com.ptc.pfc.pfcGeometry.*;
import com.ptc.pfc.pfcModelItem.*;
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
    final int surfaceId; final String source; final double[] local; double[] completeRoot;
    Candidate(int surfaceId, String source, double[] local) { this.surfaceId = surfaceId; this.source=source; this.local = local; }
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

  public static MovingOccurrence prepare(Assembly root, intseq ids) throws jxthrowable {
    ComponentPath componentPath=pfcAssembly.CreateComponentPath(root,ids);
    Solid leaf=componentPath.GetLeaf(); Transform3D complete=componentPath.GetTransform(true);
    Outline3D outline=leaf.GetGeomOutline(); double[] low=point(outline.get(0)), high=point(outline.get(1));
    double[] center=new double[]{(low[0]+high[0])/2,(low[1]+high[1])/2,(low[2]+high[2])/2};
    List<Candidate> candidates=new ArrayList<>();
    try {
      ModelItems surfaces=leaf.ListItems(ModelItemType.ITEM_SURFACE);
      for(int i=0;surfaces!=null&&i<surfaces.getarraysize();i++){
        Surface surface=(Surface)surfaces.get(i);
        try { candidates.add(new Candidate(surface.GetId(),"model_surface",point(surface.EvalClosestPointOnSurface(point(center))))); }
        catch(Throwable ignored) {}
      }
    } catch(Throwable ignored) {}
    if(candidates.isEmpty()) try {
      SolidBody body=leaf.GetDefaultBody(); Surfaces surfaces=body==null?null:body.ListSurfaces();
      for(int i=0;surfaces!=null&&i<surfaces.getarraysize();i++){
        Surface surface=surfaces.get(i);
        try { candidates.add(new Candidate(surface.GetId(),"body_surface",point(surface.EvalClosestPointOnSurface(point(center))))); }
        catch(Throwable ignored) {}
      }
    } catch(Throwable ignored) {}
    candidates.sort(Comparator.comparingInt(c->c.surfaceId));
    List<Candidate> unique=new ArrayList<>();
    for(Candidate candidate:candidates){boolean duplicate=false;for(Candidate prior:unique)if(norm(sub(candidate.local,prior.local))<1e-5){duplicate=true;break;}if(!duplicate)unique.add(candidate);}
    if(unique.isEmpty()) unique.add(new Candidate(-1,"geom_outline_center",center));
    System.err.println("[RENDER] arrow_anchor_candidates occurrence="+pathId(ids)+" count="+unique.size()+" fallback="+(unique.get(0).surfaceId<0));
    for(Candidate candidate:unique) candidate.completeRoot=transform(complete,candidate.local);
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
        double[] complete=anchor.completeRoot, exploded=add(complete,translationRoot);
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
        double[] towardCamera=mul(result.back,0.5); double[] start=add(arrow.exploded,towardCamera), end=add(arrow.complete,towardCamera);
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
    for(int i=0;i<result.arrows.size();i++){Arrow a=result.arrows.get(i);if(i>0)json.append(",\n");json.append("    {\"covered_occurrences\":[");for(int j=0;j<a.covered.size();j++){if(j>0)json.append(',');json.append(quote(a.covered.get(j)));}json.append("],\"anchor_local\":").append(vector(a.anchor.local)).append(",\"anchor_source\":").append(quote(a.anchor.source)).append(",\"anchor_surface_id\":").append(a.anchor.surfaceId).append(",\"complete_root\":").append(vector(a.complete)).append(",\"exploded_root\":").append(vector(a.exploded)).append(",\"complete_screen_plane\":").append(vector2(a.complete2)).append(",\"exploded_screen_plane\":").append(vector2(a.exploded2)).append(",\"merged\":").append(a.covered.size()>1).append('}');}
    json.append("\n  ],\n  \"status\": \"passed\"\n}\n");
    Files.writeString(Path.of(output),json.toString(),StandardCharsets.UTF_8);
  }
}
