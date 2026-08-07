"""Create the user-corrected water-tank render sequence (v2).

The corrected sequence is explicit by design: installation direction comes
from the receiver face/reference evidence, while the fixed 123/456 camera is an
independent visibility decision.  Completed multi-occurrence groups retain all
members.  Subassembly closeups use their own state pool and are collapsed to a
rigid parent only when installed into the product.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"
OUT = RUNS / "corrected-v2-camera-contracts"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def axis_face(vector: list[float], basis: dict[str, Any]) -> dict[str, Any]:
    axis = max(range(3), key=lambda i: abs(vector[i]))
    sign = 1 if vector[axis] >= 0 else -1
    label = ("+" if sign > 0 else "-") + "XYZ"[axis]
    for face_id, face in basis["faces"].items():
        if face["axis_label"] == label:
            return {"face_id": int(face_id), **face}
    raise ValueError(label)


def framing_for(pool_name: str, camera_id: str) -> dict[str, Any]:
    """Native framing only; it never changes either locked camera direction.

    Close-up pools are fitted and enlarged independently.  Product stages use
    a smaller zoom so the full accumulated body and the exploded part remain
    inside the square frame.  CENTER is authoritative: no inherited negative
    PAN is allowed to push later stages into the lower-left corner.
    """
    closeup = pool_name in {"partition", "fixed_sub", "top", "outflow", "sensor"}
    if pool_name == "partition":
        pan = [-0.90, -1.12] if camera_id == "fixed_123" else [-0.71, -1.12]
    elif pool_name == "fixed_sub":
        # Refit of this three-occurrence nested subset is stable but overly
        # conservative in the authoritative final ASM.  The calibrated
        # product-level screen transform leaves it low in the square frame;
        # move it upward without changing either locked camera direction.
        pan = [0.01, 0.10]
    elif pool_name == "outflow":
        # The fixed-part close-up is tiny compared with the authoritative root
        # assembly. Keep the product-level calibrated ScreenTransform centre;
        # a literal PAN=0 is not Creo's native Refit centre and moves this
        # nested subset outside the graphics window.
        pan = [0.0, 0.0]
    elif pool_name == "sensor":
        pan = [0.0, 0.0]
    elif pool_name == "body":
        pan = [-0.55, -0.78] if camera_id == "fixed_123" else [-0.42, -0.78]
    else:
        pan = [-0.90, -1.12] if camera_id == "fixed_123" else [-0.71, -1.12]
    framing = {
        "frame": "square",
        # Closeups tolerate 2.4x; complete product stages use the largest
        # verified scale that keeps both the device and exploded parts inside
        # the fixed square frame for both locked views.
        "zoom": (
            2.0 if pool_name == "sensor"
            else 3.0 if pool_name == "fixed_sub"
            else 1.2 if pool_name == "outflow"
            else 2.4 if closeup
            else 2.15
        ),
        "center": True,
        "pan": pan,
        "look_at_stage": pool_name in {"fixed_sub", "outflow", "sensor"},
        "pan_evidence": (
            f"corrected-v2 native {'closeup' if closeup else 'product-stage'} framing; "
            f"{camera_id}; calibrated native ScreenTransform"
        ),
    }
    return framing


# pool, BOM, title, moving, receiver, vector, camera, additions
ACTIONS = [
    ("partition", "30.1.1", "隔板 893 与三颗压铆螺柱组合", ["51/5025/79","51/5025/82","51/5025/83"], "51/5025/47", [0,45,0], "fixed_123", ["51/5025"]),
    # 旧 02 删除：不再把 30.1.2.2 错当成第二张重复特写。
    ("body", "30.1.2", "隔板合件 894 装入隔板合件 893", ["51/5050"], "51/5025/47", [0,0,60], "fixed_123", ["51/5050"]),
    ("body", "30.1.3", "不锈钢转接头 BT0906006471", ["51/4584"], "51/5050/51", [0,-60,0], "fixed_456", ["51/4584"]),
    ("body", "30.1.4", "两只不锈钢转接头 BT0906006425", ["51/4888","51/4891"], "51/5025/47", [0,0,60], "fixed_456", ["51/4888","51/4891"]),
    ("body", "30.1.5", "两只焊接接头 BT0906007487", ["51/9757","51/7792"], "51/5050/51", [0,-60,0], "fixed_456", ["51/9757","51/7792"]),
    ("body", "30.1.6", "两只固定件 JL9906991352", ["51/7789","51/10207"], "51/5025/47", [0,-60,0], "fixed_456", ["51/7789","51/10207"]),
    ("body", "30.1.7", "两只宝塔接头 BT0906405263", ["51/9874","51/13371"], "51/5025/47", [0,0,-60], "fixed_123", ["51/9874","51/13371"]),
    ("body", "30.1.8", "隔板 JL9906603254", ["51/9932"], "51/5025/47", [0,60,0], "fixed_123", ["51/9932"]),
    ("body", "30.1.9", "固定件 JL0000408371", ["51/12868"], "51/5025/47", [0,60,0], "fixed_123", ["51/12868"]),
    ("body", "30.1.10", "接头 BT0905906161", ["51/12871"], "51/12868", [0,0,60], "fixed_456", ["51/12871"]),
    ("body", "30.1.11", "压力传感器子装配 DKBA61669668", ["51/10180"], "51/12871", [0,0,60], "fixed_456", ["51/10180"]),
    ("fixed_sub", "30.1.12.2", "固定合件：两颗 CLS-M4-1 螺钉", ["51/13364/111","51/13364/114"], "51/13364/110", [60,0,0], "fixed_456", []),
    ("body", "30.1.12", "固定合件 JL9915840872 装入主箱体", ["51/13364"], "51/5025/47", [0,0,-60], "fixed_123", ["51/13364"]),

    # One dedicated top-plate closeup, matching the combined 30.1.1 rule.
    ("top", "30.2.2-30.2.4", "顶板合件特写：固定件、两只夹箍接头与六角螺栓", ["80/58","80/1999","80/2688","80/1873"], "80/51", [0,60,0], "fixed_123", ["80/58","80/1999","80/2688","80/1873"]),
    ("body", "30.4+30.5", "顶板安装前：四条密封件", ["268","277","282","289"], "51/5025/47", [0,60,0], "fixed_123", ["268","277","282","289"]),
    ("body", "30.2", "完成的顶板合件装入水箱", ["80"], "51/5025/47", [0,100,0], "fixed_123", ["80"]),
    ("body", "30.3", "六颗 GB9074.4-M4x12 螺钉", ["257","260","261","262","263","264"], "80/51", [0,60,0], "fixed_123", ["257","260","261","262","263","264"]),

    # The three 30.6 seals are used at two different interfaces.  Render them
    # immediately before the parts they seal instead of as one misleading group.
    ("body", "30.6a", "两只快接弯头 O 形密封圈（30.6 的 2/3）", ["195","196"], "51/5025/47", [0,0,60], "fixed_456", ["195","196"]),
    ("body", "30.7", "两只快接弯头 BT0905905398", ["61","64"], "51/4888", [0,0,60], "fixed_456", ["61","64"]),
    ("body", "30.8", "软管 XS0200506461", ["65"], "61", [-60,0,0], "fixed_456", ["65"]),

    # Top clamp stack: seal first, then end-cap, finally the clamp.  All three
    # leave the +Y receiving face; the former in-plane vectors were invalid.
    ("body", "30.10", "夹箍垫片 XS0701705490", ["336"], "80/1999", [0,60,0], "fixed_123", ["336"]),
    ("body", "30.9", "夹箍端盖 BT0905905084", ["337"], "80/1999", [0,60,0], "fixed_123", ["337"]),
    ("body", "30.11", "不锈钢卡箍 BT0805005010", ["463"], "80/1999", [0,60,0], "fixed_123", ["463"]),

    # 30.26 is the pressure-sensor seal and must precede 30.12 physically.
    ("body", "30.26", "压力传感器密封圈 XS0701705394", ["432"], "51/4584", [0,-45,0], "fixed_456", ["432"]),
    ("body", "30.12", "压力传感器 DQ1608405144", ["428"], "51/4584", [0,-60,0], "fixed_456", ["428"]),

    # Bottom valve stack, built from the tank outward.  The old script called
    # 30.14 an O-ring; BOM proves it is the pair of ZL0601905915 ball valves.
    ("body", "30.13a", "两只罐体侧球阀密封圈（30.13 的 2/7）", ["228","213"], "51/5025/47", [0,-90,0], "fixed_456", ["228","213"]),
    ("body", "30.14", "两只球阀 ZL0601905915", ["199","216"], "51/7792", [0,-90,0], "fixed_456", ["199","216"]),
    ("body", "30.13b", "两只转接头侧球阀密封圈（30.13 的 2/7）", ["202","217"], "199", [0,-90,0], "fixed_456", ["202","217"]),
    ("body", "30.15+30.16", "两只球阀转接头 BT0906007327 / BT0906007476", ["425","427"], "199", [0,-90,0], "fixed_456", ["425","427"]),

    # The remaining 30.6/30.13 seals belong to the left-side solenoid interface.
    ("body", "30.6b+30.13c", "电磁阀接口三只密封圈（30.6 的 1/3、30.13 的 2/7）", ["401","403","405"], "51/10180/41", [0,0,60], "fixed_123", ["401","403","405"]),
    ("body", "30.17", "接头 BT0906007485", ["363"], "51/10180/41", [0,0,60], "fixed_123", ["363"]),
    ("body", "30.18", "电磁阀 ZL0602305241", ["368"], "363", [0,90,0], "fixed_123", ["368"]),

    # Dedicated fixed-part subassembly, then rigid installation on the tank.
    ("outflow", "30.20.3", "固定件合件：两颗 CLS-M4-1 压铆螺母", ["457/11","457/14"], "457/6", [0,0,35], "fixed_123", ["457/11","457/14"]),
    ("outflow", "30.20.2", "固定件合件：两颗 GB9074.4-M4x12 螺钉", ["445","447"], "457/6", [-45,0,0], "fixed_123", ["445","447"]),
    ("body", "30.19", "完成的固定件合件 JL9915841895", ["457"], "51/5025/47", [-90,0,0], "fixed_123", ["457"]),

    # Inlet-pipe weldment closeup.  The seventh 30.13 seal is installed with
    # its two nested welded parts before the completed parent moves as a unit.
    ("sensor", "30.21.1+30.21.2+30.13d", "进水管焊件特写：端头、焊接接头与密封圈", ["393/213","393/436","407"], "393/51", [0,0,30], "fixed_123", ["393/213","393/436","407"]),
    ("body", "30.21", "完成的进水管焊件 SX0402209158", ["393"], "368", [0,0,90], "fixed_123", ["393"]),
    ("body", "30.22", "卡箍垫圈 XS0701705035", ["471"], "393/213", [0,0,90], "fixed_123", ["471"]),
    ("body", "30.23", "卡式端盖 BT0905905077", ["382"], "393/213", [0,0,90], "fixed_123", ["382"]),
    ("body", "30.25", "不锈钢卡箍 BT0805005153", ["470"], "393/213", [0,0,90], "fixed_123", ["470"]),
    ("body", "30.27", "带胶不锈钢 U 型管夹 BT0000001064", ["448"], "457/6", [0,90,0], "fixed_123", ["448"]),
    ("body", "30.24", "U 型管夹的两颗 GB9074.4-M4x12 螺钉", ["460","461"], "448", [0,60,0], "fixed_123", ["460","461"]),
]


def main() -> int:
    basis = read(RUNS / "jb9918900337-camera-basis-v3.json")
    graph = read(RUNS / "jb9918900337-final-recursive-discovery.json")
    occurrence = {item["occurrence_id"]: item for item in graph["occurrences"]}
    pools: dict[str, list[str]] = {
        "partition": ["51/5025/47"],
        "body": ["51/5025"],
        "fixed_sub": ["51/13364/110"],
        "top": ["80/51"],
        "outflow": ["457/6"],
        "sensor": ["393/213"],
    }
    jobs: list[dict[str, Any]] = []
    for index, (pool_name, level, title, moving, receiver, vector, camera_id, additions) in enumerate(ACTIONS, 1):
        missing = [path for path in moving + [receiver] if path not in occurrence]
        if missing:
            raise RuntimeError(f"{level}: missing occurrences {missing}")
        pool = pools[pool_name]
        # A rigid parent replaces any child state from its closeup pool.
        visible = sorted(set(pool + moving + [receiver]))
        face = axis_face(vector, basis)
        group = camera_id.split("_")[1]
        camera = {
            "schema_version": "creo-stage-camera-contract/v3", "bom_level": level,
            "basis_file": "data/runs/jb9918900337-camera-basis-v3.json", "coordinate_system": "root_asm",
            "receiver_face": {**face, "occurrences": [receiver], "point_root": occurrence[receiver]["transform"]["origin"],
                              "evidence": "user correction + receiver-face-normal/reference-image evidence"},
            "candidates": [{"id": camera_id, "position_direction_root": basis[camera_id + "_position_direction_root"],
                            "up_reference_root": basis["up_reference_root"], "view_matrix": basis[camera_id + "_view_matrix"]}],
            "view_policy": {"id": "fixed_two_view/v1", "view_group": group,
                            "allowed_camera_ids": ["fixed_123","fixed_456"],
                            "selection_rule": "camera chosen independently for moving+receiver visibility"},
            "selection": {"status": "user_correction_selected", "reason": "moving and receiving geometry must both remain readable"},
            "selected": {"id": camera_id, "position_direction_root": basis[camera_id + "_position_direction_root"],
                         "up_reference_root": basis["up_reference_root"], "view_matrix": basis[camera_id + "_view_matrix"]},
            "framing": framing_for(pool_name, camera_id),
        }
        camera_name = f"{index:02d}-{level.replace('.', '-')}-camera.json"
        write(OUT / camera_name, camera)
        rigid = [path for path in pool if path in occurrence and occurrence[path]["part_no"].lower().endswith(".asm")]
        jobs.append({
            "job_id": f"{index:02d}-{level.replace('.', '-')}", "bom_level": level, "title": title,
            "moving_occurrences": moving, "receiver_occurrences": [receiver], "visible_occurrences": visible,
            "stage_visibility": {"policy": "forward_exact/v1", "completed_occurrences": sorted(set(pool)),
                                 "required_context_occurrences": [], "rigid_completed_subassemblies": rigid},
            "translation": {"type": "translation_only", "vector": vector,
                            "evidence": "receiver-face-normal/user-corrected reference; never moving in receiver plane"},
            "camera_contract_file": f"corrected-v2-camera-contracts/{camera_name}",
            "render": {"frame": "square", "draw_install_arrows": True,
                       "projection_policy": "same_cad_point/v1", "output_rule": "single_installation_image"},
        })
        for path in additions:
            if path not in pool:
                pool.append(path)
        # Promote completed closeups into the body only at rigid install points.
        if level == "30.1.1": pools["body"] = ["51/5025"]
        if level == "30.2":
            pools["body"] = [path for path in pools["body"] if not path.startswith("80/")]
        if level == "30.19":
            pools["body"] = [path for path in pools["body"] if not path.startswith("457/")]
        if level == "30.21":
            pools["body"] = [path for path in pools["body"] if not path.startswith("393/")]
    write(RUNS / "corrected-v2-render-jobs.json", {
        "schema_version": "creo-render-jobs/v3", "authoritative_assembly_manifest": "jb9918900337-authoritative-assembly.json",
        "planning_policy": "user-corrected/receiver-normal/forward-exact/v2", "jobs": jobs,
    })
    write(RUNS / "corrected-v2-errata-map.json", {
        "schema_version": "render-errata/v1", "removed_old_images": ["02"],
        "structural_changes": ["top-plate closeup added", "four seals moved before top-plate install",
                               "gasket before end-cap before clamp", "outflow rigid-parent install added"],
        "job_count": len(jobs),
    })
    print(f"created {len(jobs)} corrected jobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
