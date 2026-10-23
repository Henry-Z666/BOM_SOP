import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcSelect.*;
import com.ptc.pfc.pfcSession.*;
import java.util.*;

/** Official J-Link component display commands; no change to CAD geometry. */
public final class NativeReceiverAppearance {
  private final List<Selection> selections=new ArrayList<>();
  private Assembly root;
  private String paths;
  private String restoreCommand;
  private Session session;
  private void command(String name) throws jxthrowable {
    if(session.UIGetCommand(name)==null)throw new IllegalStateException("Native component display command unavailable: "+name);
    SelectionBuffer buffer=session.GetCurrentSelectionBuffer();buffer.Clear();
    selections.clear();
    for(String path:paths.split(";")){
      intseq ids=intseq.create();for(String id:path.split("/"))ids.append(Integer.parseInt(id));
      selections.add(pfcSelect.CreateComponentSelection(pfcAssembly.CreateComponentPath(root,ids)));
    }
    for(Selection selection:selections)buffer.AddSelection(selection);
    try{
      session.RunMacro("~ Command `"+name+"`");
      try{Thread.sleep(350);}catch(InterruptedException error){Thread.currentThread().interrupt();throw new IllegalStateException(error);}
    }finally{buffer.Clear();}
  }
  public void apply(Session sourceSession,Assembly root,String paths) throws jxthrowable {
    session=sourceSession;
    this.root=root;this.paths=paths;
    command("ProCmdViewTranspShaded");
  }
  public void prepareOpaque(Session sourceSession,Assembly sourceRoot,String sourcePaths) throws jxthrowable {
    session=sourceSession;root=sourceRoot;paths=sourcePaths;command("ProCmdViewShaded");
  }
  public void restore() throws jxthrowable {
    if(paths!=null)restoreStyle("ProCmdViewShaded");
    selections.clear();
  }
  public void restoreStyle(String name) throws jxthrowable {command(name);restoreCommand=name;}
  public String getRestoreCommand(){return restoreCommand;}
}
