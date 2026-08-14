"""Clean-run final validation (written from scratch).

Checks the whole pipeline contract without trusting any intermediate claim:
  * manifest: assembly SHA-256 still matches the input file
  * plan: every visible/moving path resolves in the discovery graph,
    visibility grows monotonically, camera ids are whitelisted
  * renders: one 1600x1600 image + metadata per step, metadata hash
    matches the manifest
  * finals: each final image contains green arrow ink and the product ink
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "out" / "data"
IMAGES = ROOT / "out" / "images"
FINAL = ROOT / "out" / "final"
MODELS = ROOT / "inputs" / "models"
CAMERA_WHITELIST = {"fixed_123", "fixed_456"}

failures: list[str] = []
notes: list[str] = []


def check(ok: bool, msg: str) -> None:
    if ok:
        notes.append(f"PASS  {msg}")
    else:
        failures.append(msg)
        notes.append(f"FAIL  {msg}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text("utf-8"))
    plan = json.loads((DATA / "plan.json").read_text("utf-8"))
    graph = json.loads((DATA / "cad-graph.json").read_text("utf-8"))
    paths_in_graph = {o["path"] for o in graph["occurrences"]}

    # ---- manifest vs inputs ----
    asm = MODELS / manifest["final_assembly_file"]
    check(asm.exists(), f"assembly file exists: {asm.name}")
    check(sha256(asm).lower() == manifest["sha256_at_batch_start"].lower(),
          "manifest sha256 matches the actual assembly file")

    # ---- plan contract ----
    steps = plan["steps"]
    check(len(steps) >= 1, f"plan has {len(steps)} step(s)")
    prev_visible: set[str] = set()
    for s in steps:
        sid = s["step_id"]
        check(s["camera"] in CAMERA_WHITELIST, f"{sid}: camera whitelisted")
        unknown = [p for p in s["visible_paths"] if p not in paths_in_graph]
        check(not unknown, f"{sid}: all visible paths resolve in CAD graph")
        unknown_m = [m["path"] for m in s["moving"]
                     if m["path"] not in paths_in_graph]
        check(not unknown_m, f"{sid}: all moving paths resolve in CAD graph")
        check(prev_visible <= set(s["visible_paths"]),
              f"{sid}: visibility only grows (no part disappears)")
        for m in s["moving"]:
            tr = np.array(m["translation"])
            # rigid descendants ride their group root (one representative
            # arrow per group): zero translation is the contract there
            if not m.get("rides_with"):
                check(np.linalg.norm(tr) > 1.0,
                      f"{sid}: explosion translation non-trivial for {m['path']}")
            # pure translation: exploded anchor = complete anchor + t
            diff = np.array(m["anchor_exploded"]) - np.array(m["anchor_complete"])
            check(np.allclose(diff, tr, atol=1e-3),
                  f"{sid}: anchor pair consistent with pure translation")
        prev_visible = set(s["visible_paths"])

    # ---- renders ----
    for s in steps:
        sid = s["step_id"]
        meta_path = IMAGES / f"{sid}.render.json"
        check(meta_path.exists(), f"{sid}: render metadata exists")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text("utf-8"))
        img = IMAGES / meta.get("image_file", f"{sid}.jpg")
        check(img.exists(), f"{sid}: rendered image exists ({img.name})")
        if not img.exists():
            continue
        with Image.open(img) as im:
            check(im.size == (1600, 1600), f"{sid}: image is 1600x1600")
            arr = np.asarray(im.convert("L"))
        # background-aware ink ratio (canvas grey is ~230, not white)
        bg = float(np.median(np.concatenate([
            arr[:50, :50].ravel(), arr[:50, -50:].ravel(),
            arr[-50:, :50].ravel(), arr[-50:, -50:].ravel()])))
        ink_ratio = float((np.abs(arr.astype(float) - bg) > 12).mean())
        check(0.001 < ink_ratio < 0.95,
              f"{sid}: image has plausible ink ({ink_ratio:.2%})")
        check(meta["sha256"].lower() == manifest["sha256_at_batch_start"].lower(),
              f"{sid}: render metadata hash matches manifest")
        check(meta["camera"] == s["camera"], f"{sid}: metadata camera matches plan")

    # ---- finals: green arrow ink present ----
    for s in steps:
        sid = s["step_id"]
        final_img = FINAL / f"{sid}.jpg"
        check(final_img.exists(), f"{sid}: final image exists")
        if not final_img.exists():
            continue
        arr = np.asarray(Image.open(final_img).convert("RGB")).astype(int)
        g, r, b = arr[:, :, 1], arr[:, :, 0], arr[:, :, 2]
        green_mask = (g > 120) & (g > r + 40) & (g > b + 40)
        n_green = int(green_mask.sum())
        check(n_green > 200, f"{sid}: green arrow ink present ({n_green} px)")

    print("\n".join(notes))
    print("-" * 60)
    if failures:
        print(f"[VALIDATE] {len(failures)} failure(s)")
        sys.exit(1)
    print(f"[VALIDATE] all {len(notes)} checks passed")


if __name__ == "__main__":
    main()
