import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcSession.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Runs deterministic Creo-native DisplayList arrows in one bounded session. */
public final class NativeArrowBatch {
  private NativeArrowBatch() {}

  private static String required(String[] fields, int index, int line) {
    if (index >= fields.length || fields[index].trim().isEmpty())
      throw new IllegalArgumentException("Missing manifest field " + index + " on line " + line);
    return fields[index].trim();
  }

  public static void run(String[] args) throws Throwable {
    if (args.length != 3)
      throw new IllegalArgumentException(
          "Usage: NativeArrowBatch <creo-start-command> <assembly-file> <stage-manifest.tsv>");
    List<String> rows = Files.readAllLines(Path.of(args[2]), StandardCharsets.UTF_8);
    AsyncConnection connection = null;
    try {
      System.loadLibrary("pfcasyncmt");
      connection = pfcAsyncConnection.AsyncConnection_Start(args[0], null);
      Session session = connection.GetSession();
      int completed = 0;
      for (int line = 0; line < rows.size(); line++) {
        String row = rows.get(line);
        if (row.trim().isEmpty() || row.startsWith("#")) continue;
        String[] fields = row.split("\\t", -1);
        if (fields.length != 8)
          throw new IllegalArgumentException("Expected 8 TSV fields on line " + (line + 1));
        String output = required(fields, 0, line + 1);
        String moving = required(fields, 1, line + 1);
        String visible = required(fields, 5, line + 1);
        String camera = required(fields, 6, line + 1);
        String audit = required(fields, 7, line + 1);
        System.err.println("[NATIVE_ARROW] start=" + (completed + 1) + " output=" + output);
        RenderAssemblyImage.renderInSession(
            session,
            args[1],
            output,
            moving,
            Double.parseDouble(required(fields, 2, line + 1)),
            Double.parseDouble(required(fields, 3, line + 1)),
            Double.parseDouble(required(fields, 4, line + 1)),
            visible,
            camera,
            audit,
            true);
        completed++;
        System.err.println("[NATIVE_ARROW] completed=" + completed);
      }
      connection.End();
      connection = null;
    } finally {
      if (connection != null) try { connection.End(); } catch (Throwable ignored) {}
    }
  }

  public static void main(String[] args) {
    try {
      run(args);
    } catch (Throwable error) {
      error.printStackTrace();
      System.exit(1);
    }
  }
}
