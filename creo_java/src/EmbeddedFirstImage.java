import com.ptc.cipjava.*;
import com.ptc.pfc.pfcGlobal.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcWindow.*;

/** In-process OTK entrypoint: exports the first Creo-native full-state image. */
public final class EmbeddedFirstImage {
  public static void start() {
    new Thread(() -> {
      try {
        Session session = pfcSession.GetCurrentSessionWithCompatibility(CreoCompatibility.C4Compatible);
        String output = System.getenv("AI_ASSEMBLY_IMAGE");
        if (output == null || output.length() == 0) throw new IllegalStateException("AI_ASSEMBLY_IMAGE is required");
        for (int attempt = 0; attempt < 90; attempt++) {
          Model model = session.GetCurrentModel();
          if (model != null && model.GetType() == ModelType.MDL_ASSEMBLY) {
            Window window = session.GetModelWindow(model);
            window.Activate(); window.Repaint();
            JPEGImageExportInstructions options = pfcWindow.JPEGImageExportInstructions_Create(10.0, 7.5);
            options.SetImageDepth(RasterDepth.RASTERDEPTH_24); options.SetDotsPerInch(DotsPerInch.RASTERDPI_100);
            window.ExportRasterImage(output, options);
            System.err.println("[EMBEDDED-EXPORT] wrote=" + output);
            return;
          }
          Thread.sleep(1000);
        }
        throw new IllegalStateException("Timed out waiting for an assembly model.");
      } catch (Throwable error) { error.printStackTrace(); }
    }, "ai-assembly-export").start();
  }
  public static void stop() {}
}
