# Qwen transplant research reference

This directory preserves the unique, uncommitted source files recovered from
`Qwen_transplant` before that temporary workspace was removed.

It is deliberately outside `src/` and is **not a production pipeline**.  Do
not import or execute these files from formal jobs.  They are retained only so
their useful ideas can be reviewed and reimplemented behind the canonical,
model-neutral pipeline interfaces.

## Worth carrying forward

- `scalable-pipeline/python/plan_steps.py`: header-driven BOM discovery,
  Chinese/Arabic step-number parsing, strict BOM-order allocation, explicit
  no-silent-skip diagnostics, scalable step IDs, and contact-based planning.
- `scalable-pipeline/java/Renderer.java`: later experimental framing,
  visibility, explosion, and audit logic.
- `scalable-pipeline/java/DrawingRenderer.java`: experimental Creo drawing
  entities for arrows, avoiding a separate pixel-overlay calibration path.
- `scalable-pipeline/python/auto_review.py`: rule-first image review followed
  by optional Qwen-VL semantic checks.
- `qwen/`: an early function-calling client and tool-dispatch prototype.

## Do not adopt directly

- The Qwen client imports the OpenAI SDK and therefore violates the intended
  provider-neutral/Qwen-native runtime direction.
- The tool prototype permits caller-supplied paths and omits formal render and
  publication tools; its interfaces and filesystem policy require redesign.
- The scalable-pipeline files assume the old `clean_run` directory layout and
  contain product-specific configuration.  They must be decomposed and tested
  before any production use.
- Machine-specific launchers, license paths, compiled classes, logs, images,
  CAD session copies, spike scripts, and diagnostics were intentionally not
  retained.

The implementation plan under `docs/` is historical evidence, not an active
task list.  Current repository contracts and verified code take precedence.
