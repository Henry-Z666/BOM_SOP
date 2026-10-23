public class UnicodeVectorTextTest {
 public static void main(String[] args) {
  var text=UnicodeVectorText.layout("卡箍紧固力矩：10 N.m；紧固后划线",4);
  if(text.segments().size()<100||text.height()<=5.4)throw new AssertionError("Chinese outlines or wrapping missing");
  if(text.fills().size()<100)throw new AssertionError("solid glyph fill missing");
  for(double[] line:text.segments())for(double n:line)if(!Double.isFinite(n))throw new AssertionError("invalid outline");
  for(double[] line:text.segments())if(line[1]<0||line[3]<0||line[1]>text.height()||line[3]>text.height())throw new AssertionError("outline outside label height");
  try{UnicodeVectorText.layout("",1);throw new AssertionError("empty text accepted");}catch(IllegalArgumentException expected){}
  System.out.println("Chinese vector text test passed");
 }
}
