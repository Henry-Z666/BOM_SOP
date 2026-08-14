"""Clean-run automatic step-image review (written from scratch).

Two-layer review, agreed framework (auto_review/v1):

  rule layer : deterministic checks recomputable from the plan / manifest /
               render-meta / arrow-audit artifacts alone (no AI).  They run
               FIRST and cover the data-visible aspects:
                 C3a explosion translations applied as planned
                 C4  no later-step part visible in this step
                 C6  view locked to one of the calibrated cameras
                 C7  subject centred on the target centre
                 C8  subject large enough (occupancy)
                 C9a arrows exist, accepted, endpoints valid, ink differs

  vlm layer  : Qwen-VL semantic checks, run ONLY when every rule check
               passed (agreed call strategy):
                 V1 welding marks must be hidden
                 V2 stray transparent planes / auxiliary lines hidden
                 V3b moving parts explode as intact groups
                 C5  moving + receiver parts clearly visible
                 V9b arrows clear, pointing moving part -> receiver

Portability rules:
  * prompts reference ROLES only (moving part / receiver / installed),
    never product constants; every geometry fact is injected from the
    runtime artifacts;
  * model endpoint / key env / model name live in review_config.json;
  * every threshold lives in review_config.json.

Output: out/data/review.json - rounds are appended so the retry loop keeps
a full history (schema auto_review/v1).

Drawing channel (clean-run-render-meta/v2): the built-in-arrow renders carry
paper-mm truths instead of the model-channel framing/moving fields, so the
rule layer switches to mm-calibre checks (C6 rotation lock from the view
transform, C7 ink centre vs sheet centre, C8 outline area fill, C9 green
ink at the recorded tail/head) and the VLM layer runs single-image adapted
prompts.  Pixel thresholds convert via px = mm * dpi / 25.4.

usage: python auto_review.py [--round N] [--first F] [--count C]
"""

import argparse
import base64
import datetime
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "out" / "data"
CONFIG_PATH = ROOT / "src" / "review_config.json"


# ---------------------------------------------------------------- utilities
def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ink_bbox(img_path: Path, cfg: dict, exclude_label: bool = True):
    """Ink bounding box of a render, background-aware, optionally excluding
    the bottom-left SimpRep label zone (model channel only; never erased).
    Returns (top, bottom, left, right) or None."""
    img = Image.open(img_path).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    h, w = arr.shape[:2]
    # background colour = median of the four corners
    corners = np.concatenate([
        arr[:8, :8].reshape(-1, 3), arr[:8, -8:].reshape(-1, 3),
        arr[-8:, :8].reshape(-1, 3), arr[-8:, -8:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    ink = np.abs(arr - bg).max(axis=2) > cfg["checks"]["ink_bg_delta"]
    if exclude_label:
        ex = cfg["checks"]["ink_label_exclude"]
        ink[int(h * ex["row_frac"]):, :int(w * ex["col_frac"])] = False
    rows = np.any(ink, axis=1)
    cols = np.any(ink, axis=0)
    if not rows.any():
        return None
    r0, r1 = np.argmax(rows), h - np.argmax(rows[::-1])
    c0, c1 = np.argmax(cols), w - np.argmax(cols[::-1])
    return int(r0), int(r1), int(c0), int(c1)


def b64_image(path: Path) -> str:
    data = Path(path).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def check(id_, layer, ok, reason, actions):
    entry = {"id": id_, "layer": layer, "pass": bool(ok), "reason": reason}
    if not ok:
        entry["action"] = actions.get(id_, "manual")
    return entry


# ------------------------------------------------------------ rule checks
def rule_camera(step, meta, manifest, cfg):
    cam_id = step.get("camera")
    if cam_id not in cfg["allowed_cameras"]:
        return False, f"camera '{cam_id}' not in allowed set"
    if cam_id != meta.get("camera"):
        return False, f"render used '{meta.get('camera')}' plan wants '{cam_id}'"
    mat = manifest["cameras"][cam_id]
    view_rot = meta["view_rot"]
    cos_tol = cfg["checks"]["C6_camera_cosine_tol"]
    for r in range(3):
        col = [mat[k][r] for k in range(3)]      # stored rows, read as cols
        n = sum(v * v for v in col) ** 0.5
        if n == 0:
            return False, "manifest camera column has zero norm"
        dot = sum(view_rot[r][k] * col[k] / n for k in range(3))
        if dot < cos_tol:
            return False, f"view row {r} deviates from calibration (cos={dot:.5f})"
    col_norm = sum(mat[k][0] ** 2 for k in range(3)) ** 0.5
    s = meta.get("view_scale", 0.0)
    base = meta.get("view_scale_base")
    zf = meta.get("zoom_factor")
    if base and zf:
        # official/v2 framing: zoom lives in the ScreenTransform, the view
        # transform keeps unit scale, so the read-back scale must equal the
        # base scale (rotation lock is checked above)
        expect = base
        rel = abs(s - expect) / expect if expect else 1.0
        if rel > cfg["checks"]["C6_scale_rel_tol"]:
            return False, f"view scale {s:.4f} off base {expect:.4f} ({rel:.1%})"
        return True, f"locked to {cam_id}, rotation cos>= {cos_tol}, scale=base rel {rel:.1%}, zoom_factor {zf:.3f}"
    rel = abs(s - col_norm) / col_norm if col_norm else 1.0
    if rel > cfg["checks"]["C6_scale_rel_tol"]:
        return False, f"view scale {s:.4f} off calibration {col_norm:.4f} ({rel:.1%})"
    return True, f"locked to {cam_id}, rotation cos>= {cos_tol}, scale rel {rel:.1%}"


def rule_centering(meta, img_path, cfg):
    # focus-final/v2 (sop-context/v1): focus is diagnostic-only; the
    # final frame is whole-machine for every step, so centering is
    # audited on the machine ink below (the focus residual, when
    # present, stays informational).
    bb = ink_bbox(img_path, cfg)
    if bb is None:
        return False, "no ink in final image"
    pol = meta["framing"]["policy"]
    size = meta["image_size"]
    ml, mr, mt, mb = pol["margins"]
    targ_cx = (ml + size - mr) / 2.0
    targ_cy = (mt + size - mb) / 2.0
    cx = (bb[2] + bb[3]) / 2.0
    cy = (bb[0] + bb[1]) / 2.0
    # same calibre as the Renderer's unbiasedCentre (centering/v3): the raw
    # ink centre is biased ONLY when a side is actually clipped by the
    # frame/letterbox band; then recover it from the UNclipped edge plus the
    # expected ink extent.  centering/v3 corrections: (a) mm -> px needs the
    # meta factor k_px_per_mm on top of the zoom multiplier, (b) recovery
    # fires only when the bbox touches a frame edge - a shortfall with free
    # edges is an extent-model error, not clipping, and the raw centre stays
    # the honest estimate, (c) recover from the edge that is NOT clipped.
    out_mm = meta["framing"].get("outline_mm", [0.0, 0.0])
    k_px = meta["framing"].get("zoom_multiplier", 0.0)
    k_mm = meta["framing"].get("k_px_per_mm", 1.0)
    exp_w, exp_h = out_mm[0] * k_mm * k_px, out_mm[1] * k_mm * k_px
    meas_w, meas_h = bb[3] - bb[2], bb[1] - bb[0]
    edge = 6
    l_clip, r_clip = bb[2] <= edge, bb[3] >= size - edge
    if exp_w > 0 and meas_w < exp_w - edge and (l_clip != r_clip):
        cx = bb[3] - exp_w / 2.0 if l_clip else bb[2] + exp_w / 2.0
    t_clip, b_clip = bb[0] <= edge, bb[1] >= size - edge
    if exp_h > 0 and meas_h < exp_h - edge and (t_clip != b_clip):
        cy = bb[1] - exp_h / 2.0 if t_clip else bb[0] + exp_h / 2.0
    tol = cfg["checks"]["C7_center_tol_px"]
    dx, dy = cx - targ_cx, cy - targ_cy
    ok = max(abs(dx), abs(dy)) <= tol
    return ok, f"ink centre ({cx:.0f},{cy:.0f}) vs target ({targ_cx:.0f},{targ_cy:.0f}), residual ({dx:.0f},{dy:.0f}), tol {tol}px"


def rule_size(meta, img_path, cfg):
    pol = meta["framing"]["policy"]
    size = meta["image_size"]
    ml, mr, mt, mb = pol["margins"]
    am = pol["arrow_margin"]
    avail_w = size - ml - mr - 2 * am
    avail_h = size - mt - mb - 2 * am
    # focus-final/v2 (sop-context/v1): the subject of EVERY final frame
    # is the whole-machine ink; focus audits are diagnostic-only.
    bb = ink_bbox(img_path, cfg)
    if bb is None:
        return False, "no ink in final image"
    w, h = bb[3] - bb[2], bb[1] - bb[0]
    occ = (w * h) / (avail_w * avail_h)
    fill = max(w / avail_w, h / avail_h)
    need_occ = cfg["checks"]["C8_min_occupancy"]
    need_fill = cfg["checks"]["C8_min_fill"]
    # size-calibre/v2: the renderer zooms to the 3D outline extent while
    # this check measures the 2D ink silhouette (systematically smaller
    # by occlusion), so the fill floor sits below the renderer's nominal
    # occupancy.
    ok = fill >= need_fill and occ >= need_occ
    return ok, (f"ink {w}x{h}, fill {fill:.0%} (need {need_fill:.0%}), "
                f"occupancy {occ:.2%} (floor {need_occ:.0%})")


def rule_explosion_data(step, meta, completed_paths, cfg):
    tol = cfg["checks"]["C3_translation_tol_mm"]
    plan_mv = {m["path"]: m["translation"] for m in step["moving"]}
    meta_mv = {m["path"]: m for m in meta["moving"]}
    if set(plan_mv) != set(meta_mv):
        return False, (f"moving set mismatch: plan={sorted(plan_mv)} "
                       f"render={sorted(meta_mv)}")
    for p, t in plan_mv.items():
        m = meta_mv[p]
        dt = [abs(a - b) for a, b in zip(t, m["translation"])]
        if max(dt) > tol:
            return False, f"{p}: planned {t} vs applied {m['translation']}"
        back = [m["anchor_exploded"][k] - m["anchor_complete"][k] - t[k]
                for k in range(3)]
        if max(abs(v) for v in back) > tol:
            return False, f"{p}: read-back displacement disagrees ({back})"
        for done in completed_paths:
            if p == done or p.startswith(done + "/") \
                    or done.startswith(p + "/"):
                return False, f"{p}: an installed part is being exploded"
    return True, f"{len(plan_mv)} moving part(s): translations applied and read-back verified"


def rule_later_blocked(plan, index, cfg, completed=()):
    if plan["steps"][index]["step_id"] in cfg["checks"]["c4_exempt_steps"]:
        return True, "exempt step (order adjusted in understanding phase)"
    vis = plan["steps"][index]["visible_paths"]
    cur_roots = plan["steps"][index]["moving_paths"]
    later_roots = []
    for s in plan["steps"][index + 1:]:
        later_roots.extend(s["moving_paths"])
    hits = []
    for lr in later_roots:
        if any(lr == cr or lr.startswith(cr + "/") for cr in cur_roots):
            continue          # nested rider of the current subassembly
        # a later root whose subtree is ALREADY installed (a child moved in
        # an earlier step, e.g. step-0 children under a step-18 parent)
        # must stay visible; only truly uninstalled roots are blocked
        if any(p == lr or p.startswith(lr + "/") or lr.startswith(p + "/")
               for p in completed):
            continue
        for vp in vis:
            # a visible part that THIS step itself installs (its own moving
            # subtree) is expected on screen, not later-step leakage - e.g.
            # step-0 fasteners 457/11 under the step-18 parent root 457
            if any(vp == cr or vp.startswith(cr + "/") for cr in cur_roots):
                continue
            if vp == lr or vp.startswith(lr + "/"):
                hits.append(vp)
    if hits:
        return False, f"later-step parts visible: {sorted(set(hits))}"
    return True, f"none of {len(later_roots)} later root(s) appear in the visible set"


def rule_arrows_data(step, arrows_step, img_path, arrows_path, cfg):
    if arrows_step is None:
        return False, "step missing from arrows.json"
    arrows = arrows_step.get("arrows", [])
    if not arrows:
        return False, "no arrows recorded"
    plan_paths = {m["path"] for m in step["moving"]
                  if "rides_with" not in m}
    # merge_policy coverage audit: roots that project to an identical arrow
    # (one rigid group) are absorbed into the representative's merged_paths,
    # so the covered set is path + merged_paths, not just path.
    got_paths = set()
    for a in arrows:
        got_paths.add(a["path"])
        got_paths.update(a.get("merged_paths", []))
    if plan_paths != got_paths:
        return False, f"arrow set {sorted(got_paths)} != moving set {sorted(plan_paths)}"
    for a in arrows:
        if a.get("status") != "accepted":
            return False, f"{a['path']}: arrow status={a.get('status')}"
        if not a.get("identity_ok"):
            return False, f"{a['path']}: same-CAD-point identity failed"
        if not a.get("length_ok") or not a.get("in_image"):
            return False, f"{a['path']}: length/in-image constraint failed"
    # ink difference: the overlay must actually draw something
    base = np.asarray(Image.open(img_path).convert("RGB"), dtype=np.int16)
    over = np.asarray(Image.open(arrows_path).convert("RGB"), dtype=np.int16)
    if base.shape != over.shape:
        return False, "render and arrow images differ in size"
    diff_px = int((np.abs(over - base).max(axis=2) > 30).sum())
    need = cfg["checks"]["C9_min_ink_diff_px"]
    if diff_px < need:
        return False, f"arrow ink only {diff_px}px (< {need})"
    return True, f"{len(arrows)} arrow(s) accepted, identity ok, ink diff {diff_px}px"


# ------------------------------------------- drawing-channel (meta v2) checks
def px_per_mm(meta: dict) -> float:
    return meta.get("dpi", 100) / 25.4


def rule_camera_v2(step, meta, manifest, cfg):
    """Rotation lock from the drawing view transform (row-vector p'=p*M).
    Empirically decoded (probe vs manifest, 30.25/30.14/30.21): the 3x3 part
    is rotation * g with g = s_fit / k (D-system, k = sheet_w/1000), and its
    ROWS are parallel to the manifest camera ROWS as stored (the model
    channel read-back is the transpose - hence a separate v2 rule).  A
    rotation*g 3x3 has element RMS = g/sqrt(3)."""
    cam_id = step.get("camera")
    if cam_id not in cfg["allowed_cameras"]:
        return False, f"camera '{cam_id}' not in allowed set"
    if cam_id != meta.get("camera"):
        return False, f"render used '{meta.get('camera')}' plan wants '{cam_id}'"
    mat = manifest["cameras"][cam_id]
    vt = meta["view_transform"]
    g = math.sqrt(sum(vt[r][c] ** 2 for r in range(3)
                      for c in range(3)) / 3.0)
    if g <= 0:
        return False, "view_transform has zero scale"
    cos_tol = cfg["checks"]["C6_camera_cosine_tol"]
    for r in range(3):
        row = [vt[r][k] / g for k in range(3)]
        cam_row = mat[r][:3]
        n = sum(v * v for v in cam_row) ** 0.5
        if n == 0:
            return False, "manifest camera row has zero norm"
        dot = sum(row[k] * cam_row[k] / n for k in range(3))
        if dot < cos_tol:
            return False, f"view row {r} deviates from calibration (cos={dot:.5f})"
    # D-system scale cross-check: g must equal view_scale / k
    k = meta["sheet_mm"][0] / 1000.0
    expect = meta.get("view_scale", 0.0) / k
    rel = abs(g - expect) / expect if expect else 1.0
    if rel > cfg["checks"]["C6_scale_rel_tol"]:
        return False, f"transform norm {g:.4f} off view_scale/k {expect:.4f} ({rel:.1%})"
    return True, (f"locked to {cam_id}, rotation cos>= {cos_tol}, "
                  f"D-scale rel {rel:.1%} (closed-form s_fit)")


def rule_centering_v2(meta, img_path, cfg):
    """The union (model + anchors) is centred on the paper by construction
    (framing_drawing/v1); audit it as ink centre vs sheet centre in px."""
    bb = ink_bbox(img_path, cfg, exclude_label=False)
    if bb is None:
        return False, "no ink in final image"
    w, h = Image.open(img_path).size
    targ_cx, targ_cy = w / 2.0, h / 2.0
    cx, cy = (bb[2] + bb[3]) / 2.0, (bb[0] + bb[1]) / 2.0
    tol = cfg["checks"]["C7_center_tol_mm"] * px_per_mm(meta)
    dx, dy = cx - targ_cx, cy - targ_cy
    ok = max(abs(dx), abs(dy)) <= tol
    return ok, (f"ink centre ({cx:.0f},{cy:.0f}) vs sheet centre "
                f"({targ_cx:.0f},{targ_cy:.0f}), residual ({dx:.1f},{dy:.1f})px, "
                f"tol {tol:.0f}px")


def rule_size_v2(meta, cfg):
    """C8 for the drawing channel: model outline area vs sheet area
    (paper-mm truths from the meta; no pixel silhouette needed)."""
    sw, sh = meta["sheet_mm"]
    x0, y0, x1, y1 = meta["view_outline_mm"]
    fill = ((x1 - x0) * (y1 - y0)) / (sw * sh)
    need = cfg["checks"]["C8_min_outline_fill"]
    ok = fill >= need
    return ok, (f"view outline {x1 - x0:.0f}x{y1 - y0:.0f}mm on "
                f"{sw:.0f}x{sh:.0f} sheet, area fill {fill:.0%} "
                f"(floor {need:.0%})")


def rule_explosion_v2():
    return True, ("drawing channel: complete-pose policy, the arrow carries "
                  "the installation motion - explosion N/A")


def rule_arrows_v2(meta, img_path, cfg):
    """Built-in arrow ink audit: enough saturated green, and green ink at
    the recorded tail cross / head tip (mm -> px via the meta dpi)."""
    arr = np.asarray(Image.open(img_path).convert("RGB"), dtype=np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    green = (g - np.maximum(r, b)) > 60
    n = int(green.sum())
    need = cfg["checks"]["C9_min_ink_diff_px"]
    if n < need:
        return False, f"arrow ink only {n}px (< {need})"
    k = px_per_mm(meta)
    tol = cfg["checks"]["C9_arrow_loc_tol_mm"] * k
    ys, xs = np.nonzero(green)
    hits = []
    for name, mm in (("tail", meta["tail_sheet_mm"]),
                     ("head", meta["head_sheet_mm"])):
        cx, cy = mm[0] * k, (meta["sheet_mm"][1] - mm[1]) * k
        if not ((np.abs(xs - cx) <= tol) & (np.abs(ys - cy) <= tol)).any():
            return False, (f"no arrow ink within {tol:.0f}px of {name} "
                           f"sheet mm ({mm[0]:.1f},{mm[1]:.1f})")
        hits.append(f"{name}@({cx:.0f},{cy:.0f})px")
    return True, f"{n}px green ink, {' '.join(hits)} within {tol:.0f}px"


# ------------------------------------------------------------- VLM checks
def vlm_parse(text: str):
    """Strict {pass, reason} JSON with tolerant extraction fallback."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"): t.rfind("}") + 1] if "{" in t else t
    lo, hi = t.find("{"), t.rfind("}")
    if lo >= 0 and hi > lo:
        try:
            obj = json.loads(t[lo:hi + 1])
            if isinstance(obj.get("pass"), bool):
                return obj["pass"], str(obj.get("reason", ""))[:300]
        except json.JSONDecodeError:
            pass
    return False, f"vlm_parse_error: {text[:200]}"


def vlm_call(client, model, prompt, images, timeout_s):
    content = [{"type": "text", "text": prompt}]
    for im in images:
        content.append({"type": "image_url", "image_url": {"url": im}})
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": content}],
        temperature=0.0, timeout=timeout_s)
    return resp.choices[0].message.content


PROMPTS = {
    "V1": ("这是一张机械装配爆炸图渲染图。请判断画面中是否残留任何"
           "焊接标识（焊缝符号、焊道形状、焊接标注文字等）。"
           "没有任何焊接标识则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V2": ("这是一张机械装配爆炸图渲染图。请判断画面中是否残留"
           "无关的透明平面、基准面、孤立辅助线或线框残余"
           "（不属于任何实体零件的半透明面片或细直线）。"
           "没有则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V3": ("这是一张机械装配爆炸图渲染图。本步骤的活动件（正在安装的"
           "零件，可能是多个独立零件）名为：{names}。请判断："
           "1) 每个活动件是否都整体地离开其安装位置、以完整形态悬浮"
           "在爆开位置（没有零件断裂或脱节）；"
           "2) 其余已安装结构是否保持在原位、未被爆开。"
           "两项都满足则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V5": ("这是两张图：第一张是机械装配爆炸图渲染图，第二张是同一"
           "视图叠加绿色安装箭头后的图（箭头尾部位于活动件的爆开"
           "位置，可据此识别活动件）。本步骤的活动件名为：{names}，"
           "接受件是图中体积最大的已装配基础结构。请判断："
           "1) 活动件与接受件是否都能被清晰看见（未被严重遮挡、"
           "不是只剩极小一角）；2) 接受件上活动件要装入的安装位置"
           "（孔洞、接口、槽位或贴合面）是否朝向观察者可见。"
           "两项都满足则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V9": ("这是一张机械装配爆炸图，图中的绿色箭头表示安装方向。"
           "正确方向是：箭头尾部位于爆开悬浮位置，箭头头部指向安装到位"
           "位置（接受件一侧）。请判断：1) 每条绿色箭头方向是否正确；"
           "2) 箭头是否清晰可辨（不与背景混淆、未断裂）。"
           "都满足则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
}

# drawing channel: complete pose + built-in arrows, ONE image carries the
# arrows, so every prompt is single-image and reworded for the pose policy
PROMPTS_V2 = {
    "V1": PROMPTS["V1"],
    "V2": ("这是一张机械装配工程图渲染图。图中的绿色箭头是交付内容"
           "的一部分（安装方向指示），不属于残留元素。请除绿色箭头"
           "之外判断画面中是否残留无关的透明平面、基准面、孤立辅助"
           "线或线框残余（不属于任何实体零件的半透明面片或细直线）。"
           "没有则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V3": ("这是一张机械装配工程图渲染图。本步骤的活动件（正在安装的"
           "零件，可能是多个独立零件）名为：{names}。图中绿色箭头的"
           "箭尾贴在活动件上：活动件已沿箭头方向爆开、悬浮在箭尾"
           "附近，箭头头部指向它在接受结构上的空安装座位。请判断："
           "1) 每个活动件是否都在箭尾附近以完整形态出现（零件本体"
           "完整、没有断裂、脱节或缺失几何，可识别为一个整体）；"
           "2) 其余已安装结构是否保持在原位、未被爆开。"
           "两项都满足则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V5": ("这是一张机械装配工程图渲染图，图中内置的绿色箭头表示本"
           "步骤的安装动作（箭尾贴在爆开的活动件上，头部指向安装"
           "座）。本步骤的活动件名为：{names}，接受件是图中体积最大"
           "的已装配基础结构。请判断：1) 活动件与接受件是否都能被"
           "清晰看见（未被严重遮挡、不是只剩极小一角）；2) 接受件上"
           "活动件要装入的安装位置（孔洞、接口、槽位或贴合面）是否"
           "朝向观察者可见。两项都满足则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
    "V9": ("这是一张机械装配工程图，图中内置的绿色箭头表示安装方向"
           "（箭尾贴在爆开的活动件上，头部指向安装到位位置）。请"
           "判断：1) 每条绿色箭头方向是否正确（头部指向接受件座"
           "位一侧）；2) 箭头是否清晰可辨（不与背景混淆、未断裂）。"
           "都满足则通过。只输出JSON："
           '{"pass": true/false, "reason": "简短理由"}'),
}


def run_vlm_layer(step, img_path, arrows_path, cfg):
    v = cfg["vlm"]
    api_key = os.environ.get(v["api_key_env"], "")
    if not api_key:
        raise SystemExit(f"env {v['api_key_env']} not set; VLM layer unavailable")
    from openai import OpenAI
    client = OpenAI(base_url=v["api_base_url"], api_key=api_key)
    names = "、".join(step.get("bom_names") or ["(unnamed)"])
    full = b64_image(img_path)
    jobs = [
        ("V1", PROMPTS["V1"], [full]),
        ("V2", PROMPTS["V2"], [full]),
        ("V3", PROMPTS["V3"].replace("{names}", names), [full]),
        ("V5", PROMPTS["V5"].replace("{names}", names),
         [full, b64_image(arrows_path)]),
        ("V9", PROMPTS["V9"], [b64_image(arrows_path)]),
    ]
    out = []
    for id_, prompt, images in jobs:
        try:
            text = vlm_call(client, v["model"], prompt, images,
                            v.get("timeout_s", 60))
            ok, reason = vlm_parse(text)
        except Exception as e:                     # network / API errors
            ok, reason = False, f"vlm_call_error: {e}"
        out.append((id_, ok, reason))
        print(f"[REVIEW] {step['step_id']} {id_}: "
              f"{'pass' if ok else 'FAIL'} - {reason}")
    return out


def run_vlm_layer_v2(step, img_path, cfg):
    """Drawing channel VLM layer: single image, adapted prompts."""
    v = cfg["vlm"]
    api_key = os.environ.get(v["api_key_env"], "")
    if not api_key:
        raise SystemExit(f"env {v['api_key_env']} not set; VLM layer unavailable")
    from openai import OpenAI
    client = OpenAI(base_url=v["api_base_url"], api_key=api_key)
    names = "、".join(step.get("bom_names") or ["(unnamed)"])
    full = b64_image(img_path)
    jobs = [(id_, PROMPTS_V2[id_].replace("{names}", names), [full])
            for id_ in ("V1", "V2", "V3", "V5", "V9")]
    out = []
    for id_, prompt, images in jobs:
        try:
            text = vlm_call(client, v["model"], prompt, images,
                            v.get("timeout_s", 60))
            ok, reason = vlm_parse(text)
        except Exception as e:                     # network / API errors
            ok, reason = False, f"vlm_call_error: {e}"
        out.append((id_, ok, reason))
        print(f"[REVIEW] {step['step_id']} {id_}: "
              f"{'pass' if ok else 'FAIL'} - {reason}")
    return out


# ------------------------------------------------------------------ main
def review_round(round_no: int, first: int = 0, count=None):
    cfg = load_json(CONFIG_PATH)
    plan = load_json(DATA / "plan.json")
    manifest = load_json(DATA / "manifest.json")
    arrows_audit = load_json(DATA / "arrows.json")
    arrows_by_id = {s["step_id"]: s for s in arrows_audit.get("steps", [])}
    # batch-scoped image folder (naming/v1): the plan owns the directory
    IMAGES = ROOT / plan["images_dir"]

    results = []
    all_steps = list(enumerate(plan["steps"]))
    # scope-independent installed-prefix: completed at step i = union of the
    # moving paths of steps 0..i-1 (C3 read-back + C4 already-installed skip)
    prefixes: list[list] = []
    acc: list = []
    for _, s in all_steps:
        prefixes.append(list(acc))
        acc.extend(s["moving_paths"])
    scoped = all_steps[first:first + count] if count else all_steps[first:]
    for i, step in scoped:
        completed = prefixes[i]
        sid = step["step_id"]
        img = IMAGES / f"{sid}.jpg"
        arrows_img = IMAGES / f"{sid}.arrows.jpg"
        meta_path = IMAGES / f"{sid}.render.json"
        entries = []
        if not (img.exists() and meta_path.exists()):
            entries.append(check("C0", "rule", False,
                                 "render artifacts missing",
                                 {"C0": "rerender_step"}))
            results.append(step_result(sid, entries, cfg))
            continue
        meta = load_json(meta_path)
        v2 = meta.get("schema") == "clean-run-render-meta/v2"
        if not v2 and not arrows_img.exists():
            entries.append(check("C0", "rule", False,
                                 "arrow artifacts missing",
                                 {"C0": "rerender_step"}))
            results.append(step_result(sid, entries, cfg))
            continue
        # drawing channel repairs re-render the step (built-in arrows);
        # the model-channel redo_arrows action does not apply
        act = dict(cfg["actions"])
        if v2:
            act["C9"] = "rerender_step"

        # ---- rule layer first ----
        if v2:
            ok, why = rule_camera_v2(step, meta, manifest, cfg)
            entries.append(check("C6", "rule", ok, why, act))
            ok, why = rule_centering_v2(meta, img, cfg)
            entries.append(check("C7", "rule", ok, why, act))
            ok, why = rule_size_v2(meta, cfg)
            entries.append(check("C8", "rule", ok, why, act))
            ok, why = rule_explosion_v2()
            entries.append(check("C3", "rule", ok, why, act))
            ok, why = rule_later_blocked(plan, i, cfg, completed)
            entries.append(check("C4", "rule", ok, why, act))
            ok, why = rule_arrows_v2(meta, img, cfg)
            entries.append(check("C9", "rule", ok, why, act))
        else:
            ok, why = rule_camera(step, meta, manifest, cfg)
            entries.append(check("C6", "rule", ok, why, act))
            ok, why = rule_centering(meta, img, cfg)
            entries.append(check("C7", "rule", ok, why, act))
            ok, why = rule_size(meta, img, cfg)
            entries.append(check("C8", "rule", ok, why, act))
            ok, why = rule_explosion_data(step, meta, completed, cfg)
            entries.append(check("C3", "rule", ok, why, act))
            ok, why = rule_later_blocked(plan, i, cfg, completed)
            entries.append(check("C4", "rule", ok, why, act))
            ok, why = rule_arrows_data(step, arrows_by_id.get(sid),
                                       img, arrows_img, cfg)
            entries.append(check("C9", "rule", ok, why, act))
        for e in entries:
            print(f"[REVIEW] {sid} {e['id']}: "
                  f"{'pass' if e['pass'] else 'FAIL'} - {e['reason']}")

        # ---- VLM layer only when every rule passed ----
        if all(e["pass"] for e in entries):
            vlm_out = (run_vlm_layer_v2(step, img, cfg) if v2
                       else run_vlm_layer(step, img, arrows_img, cfg))
            for id_, ok, reason in vlm_out:
                entries.append(check(id_, "vlm", ok, reason, act))
        else:
            entries.append(check("VX", "vlm", True,
                                 "vlm layer skipped (rule layer failed)", {}))

        results.append(step_result(sid, entries, cfg))

    # ---- write review.json with round history ----
    review_path = DATA / "review.json"
    doc = {"schema": "auto_review/v1", "rounds": []}
    if review_path.exists():
        try:
            doc = load_json(review_path)
        except json.JSONDecodeError:
            pass
    n_pass = sum(1 for r in results if r["verdict"] == "pass")
    doc["rounds"].append({
        "round": round_no,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": cfg["vlm"]["model"],
        "steps": results,
        "summary": {"total": len(results), "pass": n_pass,
                    "fail": len(results) - n_pass},
    })
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"[REVIEW] round {round_no}: {n_pass}/{len(results)} steps pass "
          f"-> {review_path}")
    return 0 if n_pass == len(results) else 1


def step_result(sid, entries, cfg):
    fails = [e for e in entries if not e["pass"]]
    actions = sorted({e.get("action", "manual") for e in fails}) or []
    return {
        "step_id": sid,
        "verdict": "pass" if not fails else "fail",
        "checks": entries,
        "actions": actions,
        "manual": "manual" in actions,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=0)
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--count", type=int, default=None)
    args = ap.parse_args()
    sys.exit(review_round(args.round, args.first, args.count))
