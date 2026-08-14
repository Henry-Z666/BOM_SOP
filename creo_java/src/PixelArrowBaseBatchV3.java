/**
 * V3 geometry-only renderer.  It deliberately exports no DisplayList arrows:
 * the companion pixel compositor owns all delivered arrow presentation.
 */
public final class PixelArrowBaseBatchV3 {
  private PixelArrowBaseBatchV3() {}

  public static void main(String[] args) {
    // Retained only for the experimental pixel-compositor adapter. Formal
    // Agent rendering calls NativeArrowBatch directly with native arrows on.
    if(args.length!=3){System.err.println("Usage: PixelArrowBaseBatchV3 <creo-start-command> <assembly-file> <stage-manifest.tsv>");System.exit(2);}
    try {
      PixelArrowCompatibility.run(args);
    } catch(Throwable e){e.printStackTrace();System.exit(1);}
  }
}
