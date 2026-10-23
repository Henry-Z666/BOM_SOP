import java.util.*;
public class SpatialNumberLayoutTest {
  public static void main(String[] args) {
    for(double scale:new double[]{.001,1,1000}) {
      var specs=new ArrayList<double[]>();
      for(double x:new double[]{30,-30,10,-10}) specs.add(new double[]{x*scale,0,12*scale,8*scale});
      var body=new TorqueLabelLayout.Rect(-40*scale,-5*scale,80*scale,10*scale);
      var placements=TorqueLabelLayout.placeOrderedNumbers(specs,List.of(body),List.of(),5*scale);
      for(int i=0;i<specs.size();i++) {
        var a=placements.get(i);var s=specs.get(i);
        if(a.box().overlaps(body,0))throw new AssertionError("number covers body");
        for(int j=0;j<i;j++) {
          var b=placements.get(j);var t=specs.get(j);
          if(a.box().overlaps(b.box(),0)||TorqueLabelLayout.crosses(s[0],s[1],a.leaderX(),a.leaderY(),t[0],t[1],b.leaderX(),b.leaderY()))throw new AssertionError("numbers or leaders overlap");
        }
      }
    }
    System.out.println("Spatial-number layout: complete group, separation, crossing and unit-scale checks passed");
  }
}
