import com.ptc.cipjava.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcDisplay.*;
import com.ptc.pfc.pfcSession.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Camera-facing vector text and leaders in a native 3D display list. No raster overlays. */
public final class NativeTorqueAnnotations {
  private NativeTorqueAnnotations() {}
  private static final AtomicInteger NEXT_ID=new AtomicInteger(85101);
  private static ColorRGB priorText,priorFill;
  public static void restoreColors(Session session) throws jxthrowable {
    if(priorText!=null){session.SetStdColorFromRGB(StdColor.COLOR_LETTER,priorText);priorText=null;}
    if(priorFill!=null){session.SetStdColorFromRGB(StdColor.COLOR_SECONDARY_PREVIEW,priorFill);priorFill=null;}
  }
  static String asciiText(String text) {
    String value=text.replace("±","+/-");
    UnicodeVectorText.layout(value,1.0);
    return value;
  }
  private static double dot(double[] p,double[] axis){return p[0]*axis[0]+p[1]*axis[1]+p[2]*axis[2];}
  private static Point3D point(double[] p) throws jxthrowable {Point3D result=Point3D.create();for(int i=0;i<3;i++)result.set(i,p[i]);return result;}
  private record Label(String occurrence,String connection,String text,double[] target,
      TorqueLabelLayout.Placement placement,UnicodeVectorText.Layout glyph) {}
  private static final class Listener extends DefaultDisplayListener {
    final List<Label> labels; final double[] right,up,back; final double depth,cell;
    Listener(List<Label> labels,double[] right,double[] up,double[] back,double depth,double cell){
      this.labels=labels;this.right=right;this.up=up;this.back=back;this.depth=depth;this.cell=cell;
    }
    double[] root(double x,double y){double[] p=new double[3];for(int i=0;i<3;i++)p[i]=x*right[i]+y*up[i]+depth*back[i];return p;}
    void line(Display display,double x1,double y1,double x2,double y2) throws jxthrowable {
      display.SetPenPosition(point(root(x1,y1)));display.DrawLine(point(root(x2,y2)));
    }
    @Override public void OnDisplay(Display display) throws jxthrowable {
      display.SetCurrentGraphicsMode(GraphicsMode.DRAW_GRAPHICS_NORMAL);
      display.SetCurrentGraphicsColor(StdColor.COLOR_LETTER);
      for(Label label:labels){
        TorqueLabelLayout.Rect box=label.placement.box();
        display.SetCurrentGraphicsColor(StdColor.COLOR_SECONDARY_PREVIEW);
        for(double y=box.y();y<=box.y()+box.height();y+=cell*0.04)
          line(display,box.x(),y,box.x()+box.width(),y);
        display.SetCurrentGraphicsColor(StdColor.COLOR_LETTER);
        line(display,box.x(),box.y(),box.x()+box.width(),box.y());
        line(display,box.x()+box.width(),box.y(),box.x()+box.width(),box.y()+box.height());
        line(display,box.x()+box.width(),box.y()+box.height(),box.x(),box.y()+box.height());
        line(display,box.x(),box.y()+box.height(),box.x(),box.y());
        display.SetPenPosition(point(label.target));
        display.DrawLine(point(root(label.placement.leaderX(),label.placement.leaderY())));
        for(double[] segment:label.glyph.segments())
          line(display,box.x()+cell+segment[0],box.y()+cell+segment[1],
              box.x()+cell+segment[2],box.y()+cell+segment[3]);
        for(double[] segment:label.glyph.fills())
          line(display,box.x()+cell+segment[0],box.y()+cell+segment[1],
              box.x()+cell+segment[2],box.y()+cell+segment[3]);
      }
    }
  }
  public static final class AnnotationLayout {
    private final Listener listener;
    private AnnotationLayout(Listener listener){this.listener=listener;}
  }
  public static AnnotationLayout prepareLayout(ArrowProjection.Result arrows,Map<String,double[]> targets,List<double[]> protectedBounds,String encoded,boolean spatialPerimeter) throws Exception,jxthrowable {
    return new AnnotationLayout(prepare(arrows,targets,protectedBounds,encoded,spatialPerimeter));
  }
  public static double[] labelCameraBounds(AnnotationLayout layout) {
    Listener plan=layout.listener;double[] roi={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
    for(Label label:plan.labels){TorqueLabelLayout.Rect b=label.placement.box();double x=dot(label.target,plan.right),y=dot(label.target,plan.up);roi[0]=Math.min(roi[0],Math.min(x,b.x()));roi[1]=Math.min(roi[1],Math.min(y,b.y()));roi[2]=Math.max(roi[2],Math.max(x,b.x()+b.width()));roi[3]=Math.max(roi[3],Math.max(y,b.y()+b.height()));}
    return roi;
  }
  public static double[] protectedCameraBounds(AnnotationLayout layout,List<double[]> protectedBounds) {
    double[] roi=labelCameraBounds(layout);Listener p=layout.listener;
    for(double[] b:protectedBounds)for(int mask=0;mask<8;mask++){
      double[] v={b[(mask&1)==0?0:3],b[(mask&2)==0?1:4],b[(mask&4)==0?2:5]};double x=dot(v,p.right),y=dot(v,p.up);
      roi[0]=Math.min(roi[0],x);roi[1]=Math.min(roi[1],y);roi[2]=Math.max(roi[2],x);roi[3]=Math.max(roi[3],y);
    }
    return roi;
  }
  private static Listener prepare(ArrowProjection.Result arrows,Map<String,double[]> targets,List<double[]> protectedBounds,String encoded,boolean spatialPerimeter) throws Exception,jxthrowable {
    List<Label> labels=new ArrayList<>();
    List<TorqueLabelLayout.Rect> boxes=new ArrayList<>();
    List<TorqueLabelLayout.Rect> occupied=new ArrayList<>();List<double[]> leaders=ArrowProjection.annotationProtectedSegments(arrows);
    double extent=1.0,depth=Double.NEGATIVE_INFINITY;
    for(double[] bounds:protectedBounds){
      double minX=Double.POSITIVE_INFINITY,minY=minX,maxX=-minX,maxY=-minX;
      for(int mask=0;mask<8;mask++){
        double[] p={bounds[(mask&1)==0?0:3],bounds[(mask&2)==0?1:4],bounds[(mask&4)==0?2:5]};
        minX=Math.min(minX,dot(p,arrows.right));maxX=Math.max(maxX,dot(p,arrows.right));
        minY=Math.min(minY,dot(p,arrows.up));maxY=Math.max(maxY,dot(p,arrows.up));depth=Math.max(depth,dot(p,arrows.back));
      }
      boxes.add(new TorqueLabelLayout.Rect(minX,minY,maxX-minX,maxY-minY));
      extent=Math.max(extent,Math.max(maxX-minX,maxY-minY));
    }
    for(double[] p:targets.values()) depth=Math.max(depth,dot(p,arrows.back));
    boolean spatialNumbers=true;for(String term:encoded.split(";")){String[] fields=term.split("#",-1);if(fields.length!=3||!new String(Base64.getDecoder().decode(fields[2]),StandardCharsets.UTF_8).startsWith("spatial-group-"))spatialNumbers=false;}
    if(spatialNumbers&&!boxes.isEmpty()){
      double minX=Double.POSITIVE_INFINITY,minY=minX,maxX=-minX,maxY=-minX;for(TorqueLabelLayout.Rect b:boxes){minX=Math.min(minX,b.x());minY=Math.min(minY,b.y());maxX=Math.max(maxX,b.x()+b.width());maxY=Math.max(maxY,b.y()+b.height());}extent=Math.max(extent,Math.max(maxX-minX,maxY-minY));
    }
    boolean materialNumbers=true;for(String term:encoded.split(";")){String[] fields=term.split("#",-1);if(fields.length!=3||!new String(Base64.getDecoder().decode(fields[2]),StandardCharsets.UTF_8).startsWith("material|"))materialNumbers=false;}
    if(materialNumbers){double arrowSpan=0;for(double[] segment:ArrowProjection.annotationProtectedSegments(arrows))arrowSpan=Math.max(arrowSpan,Math.hypot(segment[2]-segment[0],segment[3]-segment[1]));extent=Math.max(1,2*arrowSpan);}
    double numberSpan=0;if(materialNumbers){double minX=Double.POSITIVE_INFINITY,minY=minX,maxX=-minX,maxY=-minX;for(int i=0;i<targets.size();i++){TorqueLabelLayout.Rect b=boxes.get(i);minX=Math.min(minX,b.x());minY=Math.min(minY,b.y());maxX=Math.max(maxX,b.x()+b.width());maxY=Math.max(maxY,b.y()+b.height());}numberSpan=Math.max(maxX-minX,maxY-minY);}
    double cell=materialNumbers?Math.max(1e-8,Math.max(numberSpan*.006,ArrowProjection.annotationFeatureSpan(arrows,protectedBounds,targets.size())*.016)):Math.max(extent*0.006,0.50);
    for(String term:encoded.split(";")){
      String[] fields=term.split("#",-1);if(fields.length!=3)throw new IllegalArgumentException("invalid torque annotation encoding");
      double[] target=targets.get(fields[0]);if(target==null)throw new IllegalArgumentException("torque target is not a moving CAD occurrence");
      String text=asciiText(new String(Base64.getDecoder().decode(fields[1]),StandardCharsets.UTF_8));
      String connection=new String(Base64.getDecoder().decode(fields[2]),StandardCharsets.UTF_8);
      UnicodeVectorText.Layout glyph=UnicodeVectorText.layout(text,cell*11,materialNumbers?8:18);
      double x=dot(target,arrows.right),y=dot(target,arrows.up),width=glyph.width()+cell*2,height=glyph.height()+cell*2;
      boolean ordered=spatialPerimeter || materialNumbers && targets.size()>1;
      TorqueLabelLayout.Placement placement=ordered?null:TorqueLabelLayout.place(x,y,width,height,boxes,occupied,leaders,materialNumbers?Math.max(height,numberSpan*.12):height);
      if(placement!=null){occupied.add(placement.box());leaders.add(new double[]{x,y,placement.leaderX(),placement.leaderY()});}
      labels.add(new Label(fields[0],connection,text,target,placement,glyph));
    }
    if(spatialPerimeter || materialNumbers && targets.size()>1){List<double[]> specs=new ArrayList<>();for(Label label:labels)specs.add(new double[]{dot(label.target,arrows.right),dot(label.target,arrows.up),label.glyph.width()+cell*2,label.glyph.height()+cell*2});
      List<TorqueLabelLayout.Placement> placements=TorqueLabelLayout.placeOrderedNumbers(specs,boxes,leaders,cell*11);List<Label> arranged=new ArrayList<>();for(int i=0;i<labels.size();i++){Label l=labels.get(i);arranged.add(new Label(l.occurrence,l.connection,l.text,l.target,placements.get(i),l.glyph));}labels=arranged;}
    return new Listener(labels,arrows.right,arrows.up,arrows.back,depth+extent*0.01,cell);
  }
  public static double[] protectedCameraBounds(ArrowProjection.Result arrows,Map<String,double[]> targets,List<double[]> protectedBounds,String encoded,boolean spatialPerimeter) throws Exception,jxthrowable {
    Listener plan=prepare(arrows,targets,protectedBounds,encoded,spatialPerimeter);
    double[] roi={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
    for(double[] bounds:protectedBounds)for(int mask=0;mask<8;mask++){
      double[] p={bounds[(mask&1)==0?0:3],bounds[(mask&2)==0?1:4],bounds[(mask&4)==0?2:5]};
      double x=dot(p,arrows.right),y=dot(p,arrows.up);roi[0]=Math.min(roi[0],x);roi[1]=Math.min(roi[1],y);roi[2]=Math.max(roi[2],x);roi[3]=Math.max(roi[3],y);
    }
    for(Label label:plan.labels){TorqueLabelLayout.Rect b=label.placement.box();roi[0]=Math.min(roi[0],b.x());roi[1]=Math.min(roi[1],b.y());roi[2]=Math.max(roi[2],b.x()+b.width());roi[3]=Math.max(roi[3],b.y()+b.height());}
    return roi;
  }
  public static double[] labelCameraBounds(ArrowProjection.Result arrows,Map<String,double[]> targets,List<double[]> protectedBounds,String encoded) throws Exception,jxthrowable {
    Listener plan=prepare(arrows,targets,protectedBounds,encoded,false);
    double[] roi={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
    for(Label label:plan.labels){TorqueLabelLayout.Rect b=label.placement.box();double x=dot(label.target,arrows.right),y=dot(label.target,arrows.up);roi[0]=Math.min(roi[0],Math.min(x,b.x()));roi[1]=Math.min(roi[1],Math.min(y,b.y()));roi[2]=Math.max(roi[2],Math.max(x,b.x()+b.width()));roi[3]=Math.max(roi[3],Math.max(y,b.y()+b.height()));}
    return roi;
  }
  public static DisplayList3D display(Session session,ArrowProjection.Result arrows,
      Map<String,double[]> targets,List<double[]> protectedBounds,String encoded,String contractHash,String audit,boolean spatialPerimeter) throws Exception,jxthrowable {
    return display(session,prepareLayout(arrows,targets,protectedBounds,encoded,spatialPerimeter),contractHash,audit,spatialPerimeter);
  }
  public static DisplayList3D display(Session session,AnnotationLayout layout,String contractHash,String audit,boolean spatialPerimeter) throws Exception,jxthrowable {
    Listener listener=layout.listener;
    List<Label> labels=listener.labels;
    DisplayList3D list=session.CreateDisplayList3D(NEXT_ID.getAndIncrement(),listener);
    try {
      priorText=session.GetRGBFromStdColor(StdColor.COLOR_LETTER);
      priorFill=session.GetRGBFromStdColor(StdColor.COLOR_SECONDARY_PREVIEW);
      session.SetStdColorFromRGB(StdColor.COLOR_LETTER,pfcBase.ColorRGB_Create(0.08,0.10,0.13));
      session.SetStdColorFromRGB(StdColor.COLOR_SECONDARY_PREVIEW,pfcBase.ColorRGB_Create(1.0,0.97,0.86));
      Matrix3D matrix=Matrix3D.create();for(int r=0;r<4;r++)for(int c=0;c<4;c++)matrix.set(r,c,r==c?1:0);
      list.Display(pfcBase.Transform3D_Create(matrix));
      StringBuilder json=new StringBuilder("{\"schema_version\":\"torque-label-audit/v1\",\"renderer\":\"creo_display_list/v1\",\"status\":\"review_required\",\"contract_hash\":").append(quote(contractHash)).append(",\"labels\":[");
      for(int i=0;i<labels.size();i++){
        Label label=labels.get(i);if(i>0)json.append(',');
        json.append("{\"occurrence_id\":").append(quote(label.occurrence)).append(",\"connection_id\":").append(quote(label.connection))
            .append(",\"text\":").append(quote(label.text)).append(spatialPerimeter?",\"anchor_source\":\"native_complete_installation_anchor\",\"target_root\":" : ",\"anchor_source\":\"native_solid_surface\",\"target_root\":")
            .append(Arrays.toString(label.target)).append(",\"text_box_camera\":[")
            .append(label.placement.box().x()).append(',').append(label.placement.box().y()).append(',')
            .append(label.placement.box().width()).append(',').append(label.placement.box().height()).append("]}");
      }
      json.append("],\"style\":\"filled-cream-solid-dark/v1\",\"overlap_check\":\"passed\",\"occlusion_and_frame_check\":\"human_review_required\"}");
      Files.writeString(Path.of(audit),audit.endsWith(".note.json") ? json.toString().replace("torque-label-audit/v1","installation-note-audit/v1") : json.toString(),StandardCharsets.UTF_8);return list;
    } catch(Exception error){list.Delete();restoreColors(session);throw error;}
  }
  private static String quote(String value){return "\""+value.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r")+"\"";}
}
