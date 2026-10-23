import java.util.*;
public class TorqueLabelLayoutTest {
  public static void main(String[] args) {
    var protectedAreas=List.of(new TorqueLabelLayout.Rect(-5,-5,10,10));
    var occupied=new ArrayList<TorqueLabelLayout.Rect>();
    var leaders=new ArrayList<double[]>();
    var first=TorqueLabelLayout.place(0,0,12,4,protectedAreas,occupied,leaders,4);
    if(first.box().overlaps(protectedAreas.get(0),0))throw new AssertionError("text obscures geometry");
    occupied.add(first.box());leaders.add(new double[]{0,0,first.leaderX(),first.leaderY()});
    var second=TorqueLabelLayout.place(2,0,12,4,protectedAreas,occupied,leaders,4);
    if(second.box().overlaps(first.box(),0))throw new AssertionError("label overlap");
    if(TorqueLabelLayout.crosses(0,0,first.leaderX(),first.leaderY(),2,0,second.leaderX(),second.leaderY()))
      throw new AssertionError("crossed leaders");
    var deepReceiver=new TorqueLabelLayout.Rect(-1000,-1000,2000,2000);
    var perimeter=TorqueLabelLayout.place(0,0,12,4,List.of(deepReceiver),List.of(),List.of(),4);
    if(perimeter.box().overlaps(deepReceiver,0))throw new AssertionError("small item label covers large receiver");
    try {TorqueLabelLayout.place(0,0,12,4,List.of(deepReceiver),List.of(new TorqueLabelLayout.Rect(-10000,-10000,20000,20000)),List.of(),4);
      throw new AssertionError("no-space layout should fail");
    } catch(IllegalStateException expected) {}
    if(!NativeTorqueAnnotations.asciiText("4±0.4 N.m").equals("4+/-0.4 N.m"))
      throw new AssertionError("torque tolerance glyph normalization");
    try {NativeTorqueAnnotations.asciiText("");throw new AssertionError("unrenderable text accepted");}
    catch(IllegalArgumentException expected) {}
    var numbers=List.of(new double[]{-240,-790,90,35},new double[]{0,-790,90,35},
        new double[]{240,-790,90,35},new double[]{-240,790,90,35},
        new double[]{0,790,90,35},new double[]{240,790,90,35});
    var door=List.of(new TorqueLabelLayout.Rect(-300,-800,600,1600));
    var placements=TorqueLabelLayout.placeOrderedNumbers(numbers,door,List.of(),30);
    for(int i=0;i<placements.size();i++) {
      if(placements.get(i).box().overlaps(door.get(0),0))throw new AssertionError("number covers tall door");
      for(int j=0;j<i;j++) {
        if(placements.get(i).box().overlaps(placements.get(j).box(),0))throw new AssertionError("number overlap");
        if(TorqueLabelLayout.crosses(numbers.get(i)[0],numbers.get(i)[1],placements.get(i).leaderX(),placements.get(i).leaderY(),
            numbers.get(j)[0],numbers.get(j)[1],placements.get(j).leaderX(),placements.get(j).leaderY()))throw new AssertionError("number leaders cross");
      }
    }
    System.out.println("Torque layout: geometry protection, label separation, leader crossing and unresolved-space checks passed");
  }
}
