import java.awt.Font;
import java.awt.font.FontRenderContext;
import java.awt.font.GlyphVector;
import java.awt.geom.PathIterator;
import java.util.*;
import java.util.regex.*;

/** Platform Chinese font outlines converted to native 3D line segments, never raster overlays. */
public final class UnicodeVectorText {
  public record Layout(double width,double height,List<double[]> segments,List<double[]> fills) {}
  private static final Font FONT=new Font("Microsoft YaHei",Font.PLAIN,100);
  private static final FontRenderContext CONTEXT=new FontRenderContext(null,true,true);
  public static Layout layout(String text,double size){
    return layout(text,size,18);
  }
  public static Layout layout(String text,double size,int lineLimit){
    if(text==null||text.isBlank()||!Double.isFinite(size)||size<=0)throw new IllegalArgumentException("empty annotation");
    if(lineLimit<1||lineLimit>40)throw new IllegalArgumentException("invalid annotation line length");
    if(FONT.canDisplayUpTo(text)!=-1)throw new IllegalArgumentException("Chinese annotation font unavailable");
    List<String> lines=new ArrayList<>();
    for(String paragraph:text.replace("；","\n").split("\\n",-1)){
      Matcher tokens=Pattern.compile("\\d+(?:\\.\\d+)?(?:±\\d+(?:\\.\\d+)?)?\\s*[A-Za-z][A-Za-z.·/]*|[A-Za-z][A-Za-z0-9.·/]*|.").matcher(paragraph);
      StringBuilder current=new StringBuilder();
      while(tokens.find()){
        String token=tokens.group();
        if(current.length()>0&&current.codePointCount(0,current.length())+token.codePointCount(0,token.length())>lineLimit){lines.add(current.toString());current.setLength(0);}
        current.append(token);
      }
      lines.add(current.toString());
    }
    double width=0,scale=size/100.0;List<double[]> segments=new ArrayList<>();
    for(int line=0;line<lines.size();line++){
      GlyphVector glyph=FONT.createGlyphVector(CONTEXT,lines.get(line));
      width=Math.max(width,glyph.getLogicalBounds().getWidth()*scale);
      PathIterator path=glyph.getOutline().getPathIterator(null,0.65);double[] c=new double[6];
      double x=0,y=0,startX=0,startY=0,base=(lines.size()-line-1)*size*1.35+size*0.20;
      while(!path.isDone()){
        int type=path.currentSegment(c);double nx=c[0]*scale,ny=base-c[1]*scale;
        if(type==PathIterator.SEG_MOVETO){x=nx;y=ny;startX=x;startY=y;}
        else if(type==PathIterator.SEG_LINETO){segments.add(new double[]{x,y,nx,ny});x=nx;y=ny;}
        else if(type==PathIterator.SEG_CLOSE){segments.add(new double[]{x,y,startX,startY});x=startX;y=startY;}
        path.next();
      }
    }
    List<double[]> fills=new ArrayList<>();
    double height=lines.size()*size*1.35;
    for(double scan=size/240;scan<height;scan+=size/120){
      List<Double> crossings=new ArrayList<>();
      for(double[] edge:segments)if((edge[1]<=scan&&edge[3]>scan)||(edge[3]<=scan&&edge[1]>scan))
        crossings.add(edge[0]+(scan-edge[1])*(edge[2]-edge[0])/(edge[3]-edge[1]));
      Collections.sort(crossings);
      for(int i=0;i+1<crossings.size();i+=2)fills.add(new double[]{crossings.get(i),scan,crossings.get(i+1),scan});
    }
    return new Layout(width,height,Collections.unmodifiableList(segments),Collections.unmodifiableList(fills));
  }
}
