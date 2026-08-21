import com.ptc.cipjava.*;
import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcSession.*;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

/**
 * Owns one bounded Creo/J-Link session and consumes atomic render commands.
 *
 * The command spool is deliberately file based: the desktop Agent can recover
 * after either process dies without depending on an interactive console pipe.
 */
public final class NativeArrowWorker {
  private NativeArrowWorker() {}

  private static void atomicWrite(Path target, String content) throws Exception {
    Path temporary = target.resolveSibling(target.getFileName() + ".tmp");
    Files.writeString(temporary, content, StandardCharsets.UTF_8);
    try {
      Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    } catch (AtomicMoveNotSupportedException ignored) {
      Files.move(temporary, target, StandardCopyOption.REPLACE_EXISTING);
    }
  }

  private static String safeMessage(Throwable error) {
    StringWriter buffer = new StringWriter();
    error.printStackTrace(new PrintWriter(buffer));
    return buffer.toString().replace('\t', ' ').replace('\r', ' ').replace('\n', '|');
  }

  private static List<Path> pendingCommands(Path commands) throws Exception {
    try (Stream<Path> paths = Files.list(commands)) {
      return paths
          .filter(path -> path.getFileName().toString().endsWith(".request"))
          .sorted(Comparator.comparing(path -> path.getFileName().toString()))
          .toList();
    }
  }

  private static Path directChild(Path root, String fileName, String suffix) {
    if (!fileName.matches("[A-Za-z0-9._-]+") || !fileName.endsWith(suffix))
      throw new IllegalArgumentException("Unsafe worker artifact name: " + fileName);
    Path resolved = root.resolve(fileName).normalize();
    if (!resolved.getParent().equals(root))
      throw new IllegalArgumentException("Worker artifact escapes its spool directory");
    return resolved;
  }

  public static void run(String[] args) throws Throwable {
    if (args.length != 5)
      throw new IllegalArgumentException(
          "Usage: NativeArrowWorker <creo-start-command> <assembly-file> <worker-root> <max-commands> <idle-seconds>");
    Path root = Path.of(args[2]).toAbsolutePath().normalize();
    Path commands = root.resolve("commands");
    Path manifests = root.resolve("manifests");
    Path results = root.resolve("results");
    Files.createDirectories(commands);
    Files.createDirectories(manifests);
    Files.createDirectories(results);
    // Formal-task restart policy is owned by RenderScheduler, not this spool.
    int maxCommands = Integer.parseInt(args[3]);
    int idleSeconds = Integer.parseInt(args[4]);
    if (maxCommands < 1 || maxCommands > 100 || idleSeconds < 10 || idleSeconds > 3600)
      throw new IllegalArgumentException("Worker lifetime is outside compiled bounds");

    Path ready = root.resolve("ready.tsv");
    Path heartbeat = root.resolve("heartbeat.tsv");
    AsyncConnection connection = null;
    try {
      System.loadLibrary("pfcasyncmt");
      connection = pfcAsyncConnection.AsyncConnection_Start(args[0], null);
      Session session = connection.GetSession();
      long pid = ProcessHandle.current().pid();
      atomicWrite(ready, "native-arrow-worker/v1\t" + pid + "\t" + maxCommands + "\n");
      long idleDeadline = System.nanoTime() + idleSeconds * 1_000_000_000L;
      long nextHeartbeat = 0L;
      int completedCommands = 0;
      while (completedCommands < maxCommands) {
        long now = System.nanoTime();
        if (now >= nextHeartbeat) {
          atomicWrite(heartbeat, "native-arrow-worker/v1\t" + pid + "\t" + completedCommands
              + "\t" + Instant.now().toString() + "\n");
          nextHeartbeat = now + 1_000_000_000L;
        }
        List<Path> pending = pendingCommands(commands);
        if (pending.isEmpty()) {
          if (System.nanoTime() >= idleDeadline) break;
          Thread.sleep(200L);
          continue;
        }
        Path request = pending.get(0);
        String requestName = request.getFileName().toString();
        String commandId = requestName.substring(0, requestName.length() - ".request".length());
        Path result = directChild(results, commandId + ".result", ".result");
        String[] fields = Files.readString(request, StandardCharsets.UTF_8).trim().split("\\t", -1);
        if (fields.length == 1 && fields[0].equals("SHUTDOWN")) {
          atomicWrite(result, "passed\tshutdown\n");
          Files.deleteIfExists(request);
          break;
        }
        if (fields.length != 2 || (!fields[0].equals("RENDER") && !fields[0].equals("VISIBILITY")))
          throw new IllegalArgumentException("Unsupported worker command");
        Path manifest = directChild(manifests, fields[1], ".tsv");
        try {
          int rendered = fields[0].equals("RENDER")
              ? NativeArrowBatch.renderManifest(session, args[1], manifest)
              : NativeArrowBatch.renderVisibilityManifest(session, args[1], manifest);
          completedCommands++;
          if (completedCommands >= maxCommands) Files.deleteIfExists(ready);
          // Publish a fresh liveness record before the command result.  A Creo
          // render is synchronous and can legitimately take longer than the
          // periodic heartbeat interval; the caller must never observe a
          // completed result paired with a stale heartbeat and kill a healthy
          // session before its next loop iteration.
          atomicWrite(heartbeat, "native-arrow-worker/v1\t" + pid + "\t" + completedCommands
              + "\t" + Instant.now().toString() + "\n");
          nextHeartbeat = System.nanoTime() + 1_000_000_000L;
          atomicWrite(result, "passed\t" + rendered + "\n");
          Files.deleteIfExists(request);
          idleDeadline = System.nanoTime() + idleSeconds * 1_000_000_000L;
        } catch (Throwable error) {
          atomicWrite(result, "failed\tCREO_RENDER_FAILED\t" + safeMessage(error) + "\n");
          Files.deleteIfExists(request);
          throw error;
        }
      }
      connection.End();
      connection = null;
    } finally {
      Files.deleteIfExists(ready);
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
