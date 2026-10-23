import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcComponentFeat.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcModelItem.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcSolid.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;

/** Read-only probe of actual root component leaves and exact new model files. */
public final class NativeCadIdentityProbe {
  private static String q(String s){return "\""+(s==null?"":s).replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r").replace("\t","\\t")+"\"";}
  private static String details(Model model) throws jxthrowable {
    if(model==null)return "{\"status\":\"not_loaded\"}";
    StringBuilder params=new StringBuilder();
    for(String key:new String[]{"SL_PRT_NUMBER","P_MODEL_NAME","SL_MODEL_NAME","PWGXH_CODE","PTC_COMMON_NAME","SL_REVISION"}){
      try{Parameter p=model.GetParam(key);if(p!=null){if(params.length()>0)params.append(',');params.append(q(key)).append(':').append(q(p.GetValue().GetStringValue()));}}catch(Throwable absent){}
    }
    String bounds="null",geometryError="null";
    try{Outline3D o=((Solid)model).GetGeomOutline();bounds="["+Arrays.toString(new double[]{o.get(0).get(0),o.get(0).get(1),o.get(0).get(2)})+","+Arrays.toString(new double[]{o.get(1).get(0),o.get(1).get(1),o.get(1).get(2)})+"]";}
    catch(Throwable unavailable){geometryError=q(unavailable.getClass().getSimpleName());}
    return "{\"status\":\"loaded\",\"file\":"+q(model.GetFileName())+",\"parameters\":{"+params+"},\"local_bounds\":"+bounds+",\"geometry_error\":"+geometryError+"}";
  }
  private static ModelDescriptor descriptor(String versioned) throws jxthrowable {
    java.util.regex.Matcher v=java.util.regex.Pattern.compile("^(.*)\\.([0-9]+)$").matcher(versioned);
    String name=v.matches()?v.group(1):versioned;
    ModelDescriptor d=pfcModel.ModelDescriptor_Create(name.toLowerCase(Locale.ROOT).endsWith(".asm")?ModelType.MDL_ASSEMBLY:ModelType.MDL_PART,name,null);
    try{if(v.matches())d.SetFileVersion(Integer.valueOf(v.group(2)));}catch(jxthrowable e){throw new RuntimeException(e);}
    return d;
  }
  private static String cachedComponent(Session session,Assembly root,intseq ids) throws jxthrowable {
    Assembly owner=root;Model child=null;ComponentFeat component=null;
    for(int depth=0;depth<ids.getarraysize();depth++){
      component=null;Features features=owner.ListFeaturesByType(Boolean.FALSE,FeatureType.FEATTYPE_COMPONENT);
      for(int i=0;i<features.getarraysize();i++)if(features.get(i).GetId()==ids.get(depth)){component=(ComponentFeat)features.get(i);break;}
      if(component==null)throw new IllegalArgumentException("Unknown component feature");
      child=session.GetModelFromDescr(component.GetModelDescr());
      if(depth+1<ids.getarraysize()){
        if(child==null)child=session.RetrieveModel(component.GetModelDescr());
        owner=(Assembly)child;
      }
    }
    return "{\"feature_status\":"+component.GetStatus().getValue()+",\"embedded\":"+component.GetIsEmbedded()+",\"descriptor\":"+q(component.GetModelDescr().GetFileName())+",\"cached_model\":"+details(child)+"}";
  }
  public static void main(String[] args){
    AsyncConnection connection=null;
    try{
      System.loadLibrary("pfcasyncmt");connection=pfcAsyncConnection.AsyncConnection_Start(args[0],null);
      Session session=connection.GetSession();String[] focus=args[4].split(";",-1);
      StringBuilder aliases=new StringBuilder();
      if(focus.length>3&&!focus[3].isBlank())for(String entry:focus[3].split(",")){
        String[] pair=entry.split("=",2);String outcome;
        try{
          Model original=session.RetrieveModel(descriptor(pair[1]));
          Model alias=original.CopyAndRetrieve(pair[0],null);
          outcome="{\"status\":\"copied_in_disposable_session\",\"source\":"+details(original)+",\"alias\":"+details(alias)+"}";
        }catch(Throwable failure){outcome="{\"status\":\"failed\",\"error\":"+q(failure.getClass().getSimpleName()+":"+failure.getMessage())+"}";}
        if(aliases.length()>0)aliases.append(',');aliases.append("{\"mapping\":").append(q(entry)).append(",\"result\":").append(outcome).append('}');
      }
      StringBuilder candidates=new StringBuilder();
      if(focus.length>1&&!focus[1].isBlank())for(String file:focus[1].split(",")){
        String result;try{result=details(session.RetrieveModel(descriptor(file)));}catch(Throwable error){result="{\"status\":\"failed\",\"error\":"+q(error.getClass().getSimpleName())+"}";}
        if(candidates.length()>0)candidates.append(',');candidates.append("{\"requested_file\":").append(q(file)).append(",\"native_model\":").append(result).append('}');
      }
      Assembly root=(Assembly)session.RetrieveModel(descriptor(new File(args[1]).getName()));
      // Optional, explicitly confirmed replacements are tested only in this
      // disposable session. No model is saved by this probe.
      StringBuilder replacements=new StringBuilder();
      if(focus.length>2&&!focus[2].isBlank())for(String entry:focus[2].split(",")){
        String[] pair=entry.split("=",2);String outcome;String phase="locate_owner";
        try{
          String[] parts=pair[0].split("/");Assembly owner=root;
          for(int depth=0;depth<parts.length-1;depth++){
            ComponentFeat ancestor=(ComponentFeat)owner.GetFeatureById(Integer.parseInt(parts[depth]));
            owner=(Assembly)session.RetrieveModel(ancestor.GetModelDescr());
          }
          ComponentFeat component=(ComponentFeat)owner.GetFeatureById(Integer.parseInt(parts[parts.length-1]));
          phase="read_position";com.ptc.pfc.pfcBase.Transform3D before=component.GetPosition();
          phase="retrieve_replacement";
          Model replacement=session.RetrieveModel(descriptor(pair[1]));
          phase="create_replace_operation";FeatureOperations ops=FeatureOperations.create();ops.append(component.CreateReplaceOp(replacement));
          phase="activate_owner";owner.Display();session.GetModelWindow(owner).Activate();
          phase="execute_replace_operation";
          owner.ExecuteFeatureOps(ops,pfcSolid.RegenInstructions_Create(Boolean.FALSE,Boolean.TRUE,null));
          component=(ComponentFeat)owner.GetFeatureById(Integer.parseInt(parts[parts.length-1]));
          double deviation=0;for(int r=0;r<4;r++)for(int c=0;c<4;c++)deviation=Math.max(deviation,Math.abs(before.GetMatrix().get(r,c)-component.GetPosition().GetMatrix().get(r,c)));
          outcome="{\"status\":\"replaced\",\"placement_matrix_max_delta\":"+deviation+",\"new_model\":"+details(session.GetModelFromDescr(component.GetModelDescr()))+"}";
        }catch(Throwable failure){outcome="{\"status\":\"failed\",\"phase\":"+q(phase)+",\"error\":"+q(failure.getClass().getSimpleName()+":"+failure.getMessage())+"}";}
        if(replacements.length()>0)replacements.append(',');replacements.append("{\"mapping\":").append(q(entry)).append(",\"result\":").append(outcome).append('}');
      }
      StringBuilder observations=new StringBuilder();
      for(String path:focus[0].split(",")){
        intseq ids=intseq.create();for(String id:path.split("/"))ids.append(Integer.parseInt(id));
        String result;
        try{result=details(pfcAssembly.CreateComponentPath(root,ids).GetLeaf());}
        catch(Throwable failure){result="{\"status\":\"failed\",\"error\":"+q(failure.getClass().getSimpleName()+":"+failure.getMessage())+"}";}
        String cached;try{cached=cachedComponent(session,root,ids);}catch(Throwable error){cached="{\"status\":\"failed\",\"error\":"+q(error.getClass().getSimpleName())+"}";}
        if(observations.length()>0)observations.append(',');observations.append("{\"occurrence\":").append(q(path)).append(",\"component_path_leaf\":").append(result).append(",\"manual_cached_component\":").append(cached).append('}');
      }
      String json="{\"schema_version\":\"native-component-leaf-probe/v1\",\"root_file\":"+q(root.GetFileName())+",\"root_version\":"+root.GetDescr().GetFileVersion()+",\"preloaded_new_source_models\":["+candidates+"],\"replacement_probes\":["+replacements+"],\"native_alias_probes\":["+aliases+"],\"observations\":["+observations+"]}";
      Files.writeString(Path.of(args[2]),json);connection.End();connection=null;Files.writeString(Path.of(args[3]),"complete\n");
    }catch(Throwable failure){failure.printStackTrace();try{if(connection!=null)connection.End();}catch(Throwable ignored){}System.exit(1);}
  }
}
