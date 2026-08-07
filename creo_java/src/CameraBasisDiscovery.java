import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcBase.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcView.*;
import com.ptc.pfc.pfcWindow.*;

import java.io.*;
import java.nio.file.*;
import java.security.*;

/** Captures the authoritative assembly's saved default view before any rotation. */
public final class CameraBasisDiscovery {
  private static String esc(String value) { return value.replace("\\", "\\\\").replace("\"", "\\\""); }
  private static double[] normalize(double[] value) {
    double length = Math.sqrt(value[0] * value[0] + value[1] * value[1] + value[2] * value[2]);
    if (length < 1.0e-10) throw new IllegalArgumentException("Default camera direction is zero");
    return new double[] { value[0] / length, value[1] / length, value[2] / length };
  }
  private static int sign(double value) { return value >= 0.0 ? 1 : -1; }
  private static String vector(double[] value) { return "[" + value[0] + "," + value[1] + "," + value[2] + "]"; }
  private static String matrix(Matrix3D value) throws jxthrowable {
    StringBuilder result = new StringBuilder("[");
    for (int row = 0; row < 4; row++) {
      if (row > 0) result.append(','); result.append('[');
      for (int col = 0; col < 4; col++) { if (col > 0) result.append(','); result.append(value.get(row, col)); }
      result.append(']');
    }
    return result.append(']').toString();
  }
  private static String rotationMatrix(double[] right, double[] up, double[] back) {
    return "[[" + right[0] + "," + up[0] + "," + back[0] + ",0.0],["
      + right[1] + "," + up[1] + "," + back[1] + ",0.0],["
      + right[2] + "," + up[2] + "," + back[2] + ",0.0],[0.0,0.0,0.0,1.0]]";
  }
  private static String sha256(String path) throws Exception {
    MessageDigest digest = MessageDigest.getInstance("SHA-256");
    try (InputStream input = Files.newInputStream(Paths.get(path))) {
      byte[] block = new byte[1024 * 1024]; int count;
      while ((count = input.read(block)) > 0) digest.update(block, 0, count);
    }
    StringBuilder result = new StringBuilder(); for (byte value : digest.digest()) result.append(String.format("%02x", value));
    return result.toString();
  }
  private static String face(int id, String axis, int axisIndex, int faceSign) {
    double[] normal = new double[] { 0.0, 0.0, 0.0 }; normal[axisIndex] = faceSign;
    return "\"" + id + "\":{\"axis\":\"" + axis + "\",\"sign\":" + faceSign
      + ",\"axis_label\":\"" + (faceSign > 0 ? "+" : "-") + axis + "\",\"normal_root\":" + vector(normal) + "}";
  }
  public static void main(String[] args) {
    if (args.length != 3) { System.err.println("Usage: CameraBasisDiscovery <creo-start-command> <assembly-file> <output.json>"); System.exit(2); }
    AsyncConnection connection = null; Window window = null;
    try {
      System.loadLibrary("pfcasyncmt"); connection = pfcAsyncConnection.AsyncConnection_Start(args[0], null);
      Session session = connection.GetSession();
      String fileName = new File(args[1]).getName();
      java.util.regex.Matcher version = java.util.regex.Pattern.compile("^(.*)\\.([0-9]+)$").matcher(fileName);
      String name = version.matches() ? version.group(1) : fileName;
      ModelDescriptor descriptor = pfcModel.ModelDescriptor_Create(ModelType.MDL_ASSEMBLY, name, null);
      if (version.matches()) descriptor.SetFileVersion(Integer.valueOf(version.group(2)));
      window = session.OpenFile(descriptor); window.Activate(); Model model = window.GetModel();
      if (!model.GetFileName().equalsIgnoreCase(name)) throw new IllegalStateException("Opened unexpected assembly: " + model.GetFileName());
      if (version.matches() && !Integer.valueOf(version.group(2)).equals(model.GetDescr().GetFileVersion())) {
        throw new IllegalStateException("Opened unexpected version: " + model.GetDescr().GetFileVersion());
      }
      Transform3D rigid = pfcBase.MakeMatrixOrthonormal(model.GetCurrentViewTransform(), 1.0e-8);
      Matrix3D view = rigid.GetMatrix();
      // Creo stores view right/up/back in columns (row-vector convention).
      double[] savedRight = normalize(new double[] { view.get(0, 0), view.get(1, 0), view.get(2, 0) });
      double[] savedUp = normalize(new double[] { view.get(0, 1), view.get(1, 1), view.get(2, 1) });
      double[] savedDirection = normalize(new double[] { view.get(0, 2), view.get(1, 2), view.get(2, 2) });
      int sx = sign(savedDirection[0]), sy = sign(savedDirection[1]), sz = sign(savedDirection[2]);
      boolean trihedral = Math.abs(savedDirection[0]) >= 0.15 && Math.abs(savedDirection[1]) >= 0.15 && Math.abs(savedDirection[2]) >= 0.15;
      double[] direction = savedDirection;
      double[] opposite = new double[] { -direction[0], -direction[1], -direction[2] };
      double[] oppositeRight = new double[] { -savedRight[0], -savedRight[1], -savedRight[2] };
      String faces = face(1, "X", 0, sx) + "," + face(2, "Y", 1, sy) + "," + face(3, "Z", 2, sz) + ","
        + face(4, "X", 0, -sx) + "," + face(5, "Y", 1, -sy) + "," + face(6, "Z", 2, -sz);
      String json = "{\"schema_version\":\"assembly-camera-basis/v3\",\"assembly_file\":\"" + esc(new File(args[1]).getName())
        + "\",\"assembly_sha256\":\"" + sha256(args[1]) + "\",\"coordinate_system\":\"root_asm\",\"default_view_matrix\":"
        + matrix(view) + ",\"saved_default_position_direction_root\":" + vector(savedDirection)
        + ",\"default_position_direction_root\":" + vector(direction)
        + ",\"opposite_position_direction_root\":" + vector(opposite)
        + ",\"fixed_123_position_direction_root\":" + vector(direction)
        + ",\"fixed_456_position_direction_root\":" + vector(opposite)
        + ",\"fixed_123_view_matrix\":" + rotationMatrix(savedRight, savedUp, direction)
        + ",\"fixed_456_view_matrix\":" + rotationMatrix(oppositeRight, savedUp, opposite)
        + ",\"default_octant_signs\":[" + sx + "," + sy + "," + sz
        + "],\"up_reference_root\":" + vector(savedUp) + ",\"faces\":{" + faces + "},\"calibration\":{\"source\":\"Creo GetCurrentViewTransform immediately after OpenFile\",\"trihedral\":"
        + trihedral + ",\"formal_view_policy\":\"fixed_saved_default_and_centre_opposite\",\"fallback\":null}}";
      Files.writeString(Paths.get(args[2]), json + System.lineSeparator(), java.nio.charset.StandardCharsets.UTF_8);
      System.err.println("[CAMERA-BASIS] fixed_123_back=" + vector(direction) + " fixed_up=" + vector(savedUp)
        + " fixed_456_back=" + vector(opposite) + " trihedral=" + trihedral);
      System.err.println("[CAMERA-BASIS] wrote=" + args[2]);
      window.Close(); window = null; connection.End(); connection = null;
    } catch (Throwable error) {
      error.printStackTrace();
      try { if (window != null) window.Close(); } catch (Throwable ignored) {}
      try { if (connection != null) connection.End(); } catch (Throwable ignored) {}
      System.exit(1);
    }
  }
}
