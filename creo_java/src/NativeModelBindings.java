import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSolid.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Explicit source-backed aliases in memory; never saves or edits source CAD. */
public final class NativeModelBindings {
  public static void apply(Session session) throws Throwable {
    String configured=System.getProperty("sop.nativeBindings", "");
    if(configured.isBlank())return;
    for(String line:Files.readAllLines(Path.of(configured))){
      if(line.isBlank()||line.startsWith("#"))continue;
      String[] fields=line.split("\t",-1);
      if(fields.length!=5||!Set.of("user_confirmed","exact_native_drawing_and_spec_quantity").contains(fields[4]))throw new IllegalArgumentException("Unconfirmed native binding");
      String aliasName=fields[0],file=fields[1],expectedHash=fields[2],drawing=fields[3];
      if(!aliasName.matches("[A-Za-z0-9_-]+")||!file.matches("[A-Za-z0-9_.-]+"))throw new IllegalArgumentException("Unsafe native binding filename");
      byte[] bytes=Files.readAllBytes(Path.of(file));
      String actualHash=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
      if(!actualHash.equalsIgnoreCase(expectedHash))throw new IllegalStateException("Binding source SHA mismatch: "+file);
      int last=file.lastIndexOf('.');int version=Integer.parseInt(file.substring(last+1));String base=file.substring(0,last);
      ModelDescriptor descriptor=pfcModel.ModelDescriptor_Create(base.endsWith(".asm")?ModelType.MDL_ASSEMBLY:ModelType.MDL_PART,base,null);descriptor.SetFileVersion(version);
      Model source=session.RetrieveModel(descriptor);
      String identity=source.GetParam("P_MODEL_NAME").GetValue().GetStringValue();
      if(!identity.equalsIgnoreCase(drawing))throw new IllegalStateException("Binding drawing identity mismatch: "+file);
      ModelDescriptor aliasDescriptor=pfcModel.ModelDescriptor_Create(base.endsWith(".asm")?ModelType.MDL_ASSEMBLY:ModelType.MDL_PART,aliasName+(base.endsWith(".asm")?".asm":".prt"),null);
      Model alias=session.GetModelFromDescr(aliasDescriptor);
      if(alias==null)alias=source.CopyAndRetrieve(aliasName,null);
      if(!alias.GetParam("P_MODEL_NAME").GetValue().GetStringValue().equalsIgnoreCase(drawing))throw new IllegalStateException("Conflicting cached alias");
      com.ptc.pfc.pfcBase.Outline3D a=((Solid)source).GetGeomOutline(),b=((Solid)alias).GetGeomOutline();
      for(int r=0;r<2;r++)for(int c=0;c<3;c++)if(Math.abs(a.get(r).get(c)-b.get(r).get(c))>1e-8)throw new IllegalStateException("Binding geometry changed");
    }
  }
}
