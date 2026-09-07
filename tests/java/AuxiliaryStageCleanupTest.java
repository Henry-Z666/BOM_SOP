import com.ptc.pfc.pfcAssembly.Assembly;
import com.ptc.pfc.pfcComponentFeat.ComponentFeat;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcLayer.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcSolid.Solid;
import java.lang.reflect.*;
import java.util.*;

/** Exercises the real cleanup traversal with fake J-Link models, without Creo. */
public final class AuxiliaryStageCleanupTest {
  static final class FeatureList extends Features {
    final List<Feature> values;
    FeatureList(List<Feature> values) { super(0); this.values = values; }
    public int getarraysize() { return values.size(); }
    public Feature get(int i) { return values.get(i); }
  }
  @SuppressWarnings("unchecked")
  static <T> T proxy(Class<T> type, InvocationHandler handler) {
    return (T)Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, handler);
  }
  static final class Node {
    final String name;
    final Map<Integer, Node> children = new LinkedHashMap<>();
    int scans, hidden, blanked, retrieved;
    boolean unavailable;
    Model model;
    Node(String name) { this.name = name; }
    Node child(int id, Node node) { children.put(id, node); return this; }
    Model model() {
      if (model != null) return model;
      Layer layer = proxy(Layer.class, (p,m,a) -> {
        if (m.getName().equals("AddItem")) { hidden++; return null; }
        if (m.getName().equals("SetStatus")) {
          check(a[0] == DisplayStatus.LAYER_BLANK, "wrong layer status"); blanked++; return null;
        }
        throw new AssertionError(m.getName());
      });
      InvocationHandler handler = (p,m,a) -> {
        switch (m.getName()) {
          case "GetFileName": return name;
          case "CreateLayer": return layer;
          case "ListFeaturesByType":
            if (a[1] == FeatureType.FEATTYPE_COMPONENT) {
              List<Feature> list = new ArrayList<>();
              children.forEach((id, node) -> list.add(proxy(ComponentFeat.class, (q,n,b) -> {
                if (n.getName().equals("GetId")) return id;
                if (n.getName().equals("GetModelDescr"))
                  return proxy(ModelDescriptor.class, (r,o,c) -> node.name);
                throw new AssertionError(n.getName());
              })));
              return new FeatureList(list);
            }
            scans++;
            return new FeatureList(a[1] == FeatureType.FEATTYPE_DATUM_PLANE
                ? List.of(proxy(Feature.class, (q,n,b) -> null)) : List.of());
          default: throw new AssertionError(m.getName());
        }
      };
      model = children.isEmpty() ? proxy(Solid.class, handler) : proxy(Assembly.class, handler);
      return model;
    }
  }
  static void check(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
  static int clean(Node root, Set<String> desired, Node... nodes) throws Throwable {
    Map<String, Node> models = new HashMap<>();
    for (Node n : nodes) models.put(n.name, n);
    models.put(root.name, root);
    Session session = proxy(Session.class, (p,m,a) -> {
      if (!m.getName().equals("RetrieveModel")) throw new AssertionError(m.getName());
      Node n = models.get(((ModelDescriptor)a[0]).GetFileName());
      if (n.unavailable) throw new MissingModel();
      n.retrieved++; return n.model();
    });
    Method method;
    Object[] args;
    try {
      method = RenderAssemblyImage.class.getDeclaredMethod("hideAuxiliaryFeatures",
          Session.class, Model.class, Set.class, String.class, Set.class);
      args = new Object[]{session, root.model(), new HashSet<String>(), "", desired};
    } catch (NoSuchMethodException oldRenderer) {
      method = RenderAssemblyImage.class.getDeclaredMethod("hideAuxiliaryFeatures",
          Session.class, Model.class, Set.class);
      args = new Object[]{session, root.model(), new HashSet<String>()};
    }
    method.setAccessible(true);
    try { return (Integer)method.invoke(null, args); }
    catch (InvocationTargetException error) { throw error.getCause(); }
  }
  static final class MissingModel extends com.ptc.pfc.pfcExceptions.XToolkitNotFound {
    MissingModel() { super(0); }
  }
  public static void main(String[] args) throws Throwable {
    Node absent = new Node("absent.prt");
    absent.unavailable = true;
    Node brokenSub = new Node("broken.asm").child(2, absent);
    Node brokenRoot = new Node("broken-root.asm").child(10, brokenSub);
    boolean refused = false;
    try { clean(brokenRoot, Set.of("10"), brokenSub, absent); }
    catch (IllegalStateException unavailable) {
      refused = unavailable.getMessage().contains("CREO_VISIBLE_MODEL_MISSING")
          && unavailable.getMessage().contains("10/2")
          && unavailable.getMessage().contains("absent.prt");
    }
    check(refused, "missing visible descendant must block rendering");
    check(brokenRoot.hidden == 0 && brokenSub.hidden == 0,
        "expensive auxiliary edits started before detecting a missing visible model");

    Node moving = new Node("moving.prt"), future = new Node("future.prt");
    Node sub = new Node("sub.asm").child(1, moving).child(2, future);
    Node root = new Node("root.asm").child(10, sub);
    check(clean(root, Set.of("10/1"), sub, moving, future) == 3, "must clean root, ancestor, moving only");
    check(future.retrieved == 0 && future.scans == 0, "excluded future model was visited");
    check(root.blanked == 1 && sub.blanked == 1 && moving.blanked == 1, "visible auxiliaries not blanked");

    Node left = new Node("left.prt"), right = new Node("right.prt");
    Node shared = new Node("shared.asm").child(1, left).child(2, right);
    Node repeatedRoot = new Node("repeated.asm").child(10, shared).child(20, shared);
    check(clean(repeatedRoot, Set.of("10/1", "20/2"), shared, left, right) == 4, "shared ASM lost a visible branch");
    check(shared.blanked == 1 && left.blanked == 1 && right.blanked == 1, "model cleanup must deduplicate, not traversal");

    Node leaf = new Node("leaf.prt"), sibling = new Node("sibling.prt");
    Node nested = new Node("nested.asm").child(3, leaf);
    Node rigidRoot = new Node("rigid.asm").child(1, nested).child(10, sibling);
    check(clean(rigidRoot, Set.of("1"), nested, leaf, sibling) == 3, "rigid subtree must be fully cleaned");
    check(sibling.retrieved == 0, "occurrence prefix boundary was ignored");
    System.out.println("PASS: excluded branches pruned; visible ancestors, rigid descendants and shared-model occurrences retained");
  }
}
