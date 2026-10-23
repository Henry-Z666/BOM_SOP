import java.util.*;

/** Geometry-only layout; coordinates are the locked camera plane, never raster detections. */
public final class TorqueLabelLayout {
  private TorqueLabelLayout() {}
  public record Rect(double x, double y, double width, double height) {
    public boolean overlaps(Rect other, double margin) {
      return x < other.x + other.width + margin && x + width + margin > other.x
          && y < other.y + other.height + margin && y + height + margin > other.y;
    }
    public boolean contains(double px, double py) {
      return px >= x && px <= x + width && py >= y && py <= y + height;
    }
  }
  public record Placement(Rect box, double leaderX, double leaderY) {}
  private static double orientation(double ax,double ay,double bx,double by,double cx,double cy) {
    return (bx-ax)*(cy-ay)-(by-ay)*(cx-ax);
  }
  public static boolean crosses(double ax,double ay,double bx,double by,double cx,double cy,double dx,double dy) {
    return orientation(ax,ay,bx,by,cx,cy)*orientation(ax,ay,bx,by,dx,dy) < 0
        && orientation(cx,cy,dx,dy,ax,ay)*orientation(cx,cy,dx,dy,bx,by) < 0;
  }
  public static boolean intersects(Rect box,double ax,double ay,double bx,double by) {
    if(box.contains(ax,ay)||box.contains(bx,by)) return true;
    return crosses(ax,ay,bx,by,box.x,box.y,box.x+box.width,box.y)
        || crosses(ax,ay,bx,by,box.x,box.y+box.height,box.x+box.width,box.y+box.height)
        || crosses(ax,ay,bx,by,box.x,box.y,box.x,box.y+box.height)
        || crosses(ax,ay,bx,by,box.x+box.width,box.y,box.x+box.width,box.y+box.height);
  }
  public static Placement place(double x,double y,double width,double height,
      List<Rect> protectedAreas,List<Rect> occupied,List<double[]> leaders,double gap) {
    if (!Double.isFinite(x+y+width+height+gap) || width<=0 || height<=0 || gap<=0)
      throw new IllegalArgumentException("invalid torque label dimensions");
    Placement best=null;double bestScore=Double.POSITIVE_INFINITY;
    double minX=x,maxX=x,minY=y,maxY=y;
    for(Rect area:protectedAreas){minX=Math.min(minX,area.x);maxX=Math.max(maxX,area.x+area.width);minY=Math.min(minY,area.y);maxY=Math.max(maxY,area.y+area.height);}
    for (int ring=1;ring<=8;ring++) {
      double offset=gap*ring;
      double[][] candidates={{x+offset,y+offset},{x-width-offset,y+offset},
          {x+offset,y-height-offset},{x-width-offset,y-height-offset},
          {x-width/2,y+offset},{x-width/2,y-height-offset},
          // A small item can sit deep inside a large receiver outline. The
          // fixed rings alone cannot reach its free perimeter. Keep the same
          // collision gates and offer source-geometry boundary candidates.
          {x-width/2,maxY+offset},{x-width/2,minY-height-offset},
          {maxX+offset,y-height/2},{minX-width-offset,y-height/2}};
      List<double[]> search=new ArrayList<>(Arrays.asList(candidates));
      // Repeated terminal blocks need distinct lanes along the free perimeter.
      // Keep every existing overlap/crossing gate; extend the search instead
      // of shrinking text or allowing labels to obscure the connection.
      for(int lane=-12;lane<=12;lane++) {
        double horizontal=x-width/2+lane*(width+gap*.2);
        double vertical=y-height/2+lane*(height+gap*.2);
        search.add(new double[]{horizontal,maxY+offset});
        search.add(new double[]{horizontal,minY-height-offset});
        search.add(new double[]{maxX+offset,vertical});
        search.add(new double[]{minX-width-offset,vertical});
      }
      for(double[] candidate:search) {
        Rect box=new Rect(candidate[0],candidate[1],width,height);
        boolean blocked=false;
        for(Rect prior:protectedAreas) if(box.overlaps(prior,gap*0.1)){blocked=true;break;}
        if(blocked) continue;
        for(Rect prior:occupied) if(box.overlaps(prior,gap*0.1)){blocked=true;break;}
        double lx=Math.max(box.x,Math.min(box.x+box.width,x));
        double ly=Math.max(box.y,Math.min(box.y+box.height,y));
        for(double[] prior:leaders) if(crosses(x,y,lx,ly,prior[0],prior[1],prior[2],prior[3])){blocked=true;break;}
        for(double[] prior:leaders) if(intersects(box,prior[0],prior[1],prior[2],prior[3])){blocked=true;break;}
        for(Rect prior:occupied) if(intersects(prior,x,y,lx,ly)){blocked=true;break;}
        if(!blocked){
          double span=Math.max(Math.max(maxX,box.x+width)-Math.min(minX,box.x),Math.max(maxY,box.y+height)-Math.min(minY,box.y));
          double dx=box.x+width/2-(minX+maxX)/2,dy=box.y+height/2-(minY+maxY)/2;
          double score=span*span+0.05*(dx*dx+dy*dy);
          // Prefer the free space above the installation geometry. Enlarged
          // labels below a close-up tend to escape the square publication frame.
          if(box.y+height<=minY)score+=span*span*4;
          if(score<bestScore){bestScore=score;best=new Placement(box,lx,ly);}
        }
      }
    }
    if(best!=null)return best;
    throw new IllegalStateException("TORQUE_LABEL_LAYOUT_UNRESOLVED");
  }
  /** Place spatial-number labels together, preserving projected target order. */
  public static List<Placement> placeOrderedNumbers(List<double[]> specs,List<Rect> protectedAreas,List<double[]> sourceLeaders,double gap) {
    List<Integer> order=new ArrayList<>();double minX=Double.POSITIVE_INFINITY,minY=minX,maxX=-minX,maxY=-minX,total=0,maxHeight=0;
    for(int i=0;i<specs.size();i++){double[] v=specs.get(i);if(v.length!=4||!Double.isFinite(v[0]+v[1]+v[2]+v[3])||v[2]<=0||v[3]<=0)throw new IllegalArgumentException("Invalid spatial label");order.add(i);minX=Math.min(minX,v[0]);maxX=Math.max(maxX,v[0]);minY=Math.min(minY,v[1]);maxY=Math.max(maxY,v[1]);total+=v[2];maxHeight=Math.max(maxHeight,v[3]);}
    for(Rect b:protectedAreas){minX=Math.min(minX,b.x);maxX=Math.max(maxX,b.x+b.width);minY=Math.min(minY,b.y);maxY=Math.max(maxY,b.y+b.height);}
    for(double[] l:sourceLeaders){minY=Math.min(minY,Math.min(l[1],l[3]));maxY=Math.max(maxY,Math.max(l[1],l[3]));}
    order.sort(Comparator.comparingDouble(i->specs.get(i)[0]));double spacing=gap*.5;total+=spacing*Math.max(0,specs.size()-1);
    List<Placement> best=null;double bestScore=Double.POSITIVE_INFINITY;
    for(int side:new int[]{1,-1})for(int ring=1;ring<=8;ring++)for(double shift:new double[]{0,-.25,.25,-.5,.5,-1,1}){
      double x=(minX+maxX-total)/2+shift*total,y=side>0?maxY+gap*ring:minY-maxHeight-gap*ring;
      List<Placement> plan=new ArrayList<>(Collections.nCopies(specs.size(),null));List<Rect> occupied=new ArrayList<>();List<double[]> leaders=new ArrayList<>(sourceLeaders);boolean blocked=false;
      for(int index:order){double[] v=specs.get(index);Rect box=new Rect(x,y,v[2],v[3]);double lx=Math.max(box.x,Math.min(box.x+box.width,v[0])),ly=Math.max(box.y,Math.min(box.y+box.height,v[1]));
        for(Rect b:protectedAreas)if(box.overlaps(b,gap*.1)){blocked=true;break;}
        for(Rect b:occupied)if(box.overlaps(b,gap*.1)||intersects(b,v[0],v[1],lx,ly)){blocked=true;break;}
        for(double[] l:leaders)if(crosses(v[0],v[1],lx,ly,l[0],l[1],l[2],l[3])||intersects(box,l[0],l[1],l[2],l[3])){blocked=true;break;}
        if(blocked)break;plan.set(index,new Placement(box,lx,ly));occupied.add(box);leaders.add(new double[]{v[0],v[1],lx,ly});x+=v[2]+spacing;
      }
      if(!blocked){double span=Math.max(Math.max(maxX,x)-Math.min(minX,x-total),Math.max(maxY,y+maxHeight)-Math.min(minY,y));double score=span*span*(side>0?1:5);if(score<bestScore){bestScore=score;best=plan;}}
    }
    if(best==null) {
      // Tall assemblies can have targets on both ends with identical X.
      // Forcing every label into a single horizontal lane crosses leaders.
      // Try independent perimeter placements while retaining all collision
      // and leader-crossing checks; failure still blocks the image.
      for(boolean reverse:new boolean[]{false,true}) {
        List<Integer> perimeterOrder=new ArrayList<>(order);
        if(reverse)Collections.reverse(perimeterOrder);
        List<Placement> plan=new ArrayList<>(Collections.nCopies(specs.size(),null));
        List<Rect> occupied=new ArrayList<>();List<double[]> leaders=new ArrayList<>(sourceLeaders);
        boolean blocked=false;
        for(int index:perimeterOrder) {
          double[] v=specs.get(index);
          double distance=Math.min(Math.min(Math.abs(v[0]-minX),Math.abs(maxX-v[0])),
              Math.min(Math.abs(v[1]-minY),Math.abs(maxY-v[1])));
          double perimeterGap=Math.max(gap,distance/6.0+gap);
          try {
            Placement placed=place(v[0],v[1],v[2],v[3],protectedAreas,occupied,leaders,perimeterGap);
            plan.set(index,placed);occupied.add(placed.box());
            leaders.add(new double[]{v[0],v[1],placed.leaderX(),placed.leaderY()});
          } catch(IllegalStateException unresolved) {blocked=true;break;}
        }
        if(!blocked){best=plan;break;}
      }
    }
    if(best==null)throw new IllegalStateException("INSTALLATION_NUMBER_LAYOUT_UNRESOLVED");return best;
  }

}
