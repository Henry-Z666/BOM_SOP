import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcSession.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * V3 geometry-only renderer.  It deliberately exports no DisplayList arrows:
 * the companion pixel compositor owns all delivered arrow presentation.
 */
public final class PixelArrowBaseBatchV3 {
  private PixelArrowBaseBatchV3() {}
  private static String required(String[] fields,int index,int line) {
    if(index>=fields.length || fields[index].trim().isEmpty()) throw new IllegalArgumentException("Missing manifest field "+index+" on line "+line);
    return fields[index].trim();
  }
  public static void main(String[] args) {
    if(args.length!=3){System.err.println("Usage: PixelArrowBaseBatchV3 <creo-start-command> <assembly-file> <stage-manifest.tsv>");System.exit(2);}
    AsyncConnection connection=null;
    try {
      System.loadLibrary("pfcasyncmt"); List<String> rows=Files.readAllLines(Path.of(args[2]),StandardCharsets.UTF_8);
      connection=pfcAsyncConnection.AsyncConnection_Start(args[0],null); Session session=connection.GetSession(); int completed=0;
      for(int line=0;line<rows.size();line++){
        String row=rows.get(line);if(row.trim().isEmpty()||row.startsWith("#"))continue;String[] f=row.split("\\t",-1);
        if(f.length!=9)throw new IllegalArgumentException("Expected 9 TSV fields on line "+(line+1));
        String output=required(f,0,line+1),moving=required(f,1,line+1),visible=required(f,5,line+1),camera=required(f,6,line+1),audit=required(f,7,line+1);
        System.err.println("[PIXEL_V3] base_start="+(completed+1)+" output="+output);
        boolean nativeCalibration=Boolean.parseBoolean(required(f,8,line+1));
        RenderAssemblyImage.renderInSession(session,args[1],output,moving,Double.parseDouble(required(f,2,line+1)),Double.parseDouble(required(f,3,line+1)),Double.parseDouble(required(f,4,line+1)),visible,camera,audit,nativeCalibration);
        completed++;System.err.println("[PIXEL_V3] base_completed="+completed);
      }
      connection.End();
    } catch(Throwable e){e.printStackTrace();try{if(connection!=null)connection.End();}catch(Throwable ignored){}System.exit(1);}
  }
}
