"""Hard delivery gates. Missing CAD facts trigger automated discovery retries."""
from __future__ import annotations

from typing import Any


def _rotation(matrix: list[list[float]]) -> list[list[float]]:
    return [row[:3] for row in matrix[:3]]


def validate_contract(contract: dict[str, Any], require_render: bool = False) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") not in {"step-contract/v1", "step-contract/v2"}: errors.append("不支持的合同版本")
    if contract.get("schema_version") == "step-contract/v2":
        assembly = contract.get("assembly", {})
        if not assembly.get("authoritative_manifest"): errors.append("缺少锁定的最终总装清单")
        camera = contract.get("camera", {})
        if camera.get("selected") and camera.get("selected") not in {"fixed_123", "fixed_456"}:
            errors.append("正式步骤只能使用 fixed_123 或 fixed_456")
    phase = contract.get("automation", {}).get("phase")
    if phase not in {"planned", "rendered", "verified", "published"}: errors.append("步骤尚未完成自动 CAD 规划")
    if not contract.get("expected_bom_items"): errors.append("缺少直属 BOM 物料")
    if not contract.get("moving_occurrences"): errors.append("缺少活动 occurrence")
    if not contract.get("receiver_occurrences"): errors.append("缺少接收件 occurrence")
    translation = contract.get("translation", {})
    if translation.get("type") != "translation_only" or not translation.get("vectors"):
        errors.append("缺少仅平移的爆炸向量")
    if not translation.get("evidence"): errors.append("缺少平移方向证据")
    if not contract.get("camera", {}).get("selected"): errors.append("缺少自动选择的相机")
    visibility = contract.get("stage_visibility") or {}
    if visibility.get("policy") == "forward_exact/v1":
        expected_visible = set(visibility.get("completed_occurrences", []))
        expected_visible.update(contract.get("moving_occurrences", []))
        expected_visible.update(contract.get("receiver_occurrences", []))
        expected_visible.update(visibility.get("required_context_occurrences", []))
        actual_visible = set(contract.get("visible_occurrences", []))
        if actual_visible != expected_visible:
            errors.append("阶段可见集必须严格等于此前件＋本步活动件＋接收件＋必要上下文")
        allowed_rigid = set(visibility.get("rigid_completed_subassemblies", []))
        broad = [path for path in actual_visible if path not in allowed_rigid and any(
            other.startswith(path + "/") for other in expected_visible if other != path
        )]
        if broad:
            errors.append("阶段可见集包含会带入未来零件的宽泛父 occurrence: " + ",".join(sorted(broad)))
    method = contract.get("method", {})
    if method.get("text") and not method.get("source"): errors.append("工艺文字缺少来源")
    if require_render:
        errors.extend(validate_render(contract))
    return errors


def validate_camera_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") not in {"creo-stage-camera-contract/v2", "creo-stage-camera-contract/v3"}:
        errors.append("不支持的相机合同版本")
    if contract.get("coordinate_system") != "root_asm":
        errors.append("相机必须使用根 ASM 坐标系")
    face = contract.get("receiver_face") or {}
    if face.get("face_id") not in range(1, 7): errors.append("接收面编号必须为 1-6")
    if face.get("axis_label") not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}: errors.append("缺少接收面轴向")
    if not face.get("evidence"): errors.append("缺少接收面法向证据")
    selected = contract.get("selected") or {}
    if len(selected.get("position_direction_root", [])) != 3: errors.append("缺少绝对相机位置方向")
    if len(selected.get("up_reference_root", [])) != 3: errors.append("缺少绝对相机向上方向")
    if len(selected.get("view_matrix", [])) != 4: errors.append("缺少绝对视图矩阵")
    if not contract.get("candidates"): errors.append("缺少相机候选")
    if "camera_rotate" in contract: errors.append("新相机合同禁止相对旋转 camera_rotate")
    if contract.get("schema_version") == "creo-stage-camera-contract/v3":
        policy = contract.get("view_policy") or {}
        group = str(policy.get("view_group", ""))
        selected_id = str(selected.get("id", ""))
        if policy.get("id") != "fixed_two_view/v1": errors.append("v3 必须使用 fixed_two_view/v1")
        if group not in {"123", "456"}: errors.append("v3 缺少固定视角组 123/456")
        if selected_id != "fixed_" + group: errors.append("所选相机与固定视角组不一致")
        framing = contract.get("framing") or {}
        pan = framing.get("pan")
        if pan is not None and len(pan) != 2:
            errors.append("原生构图 PAN 必须包含两个分量")
    return errors


def validate_render(contract: dict[str, Any]) -> list[str]:
    render = contract.get("render") or {}
    errors: list[str] = []
    if not (render.get("installation_image") or render.get("exploded_image")):
        errors.append("缺少单张安装爆炸图")
    occurrences = render.get("occurrences", [])
    expected_ids = set(contract.get("moving_occurrences", []) + contract.get("receiver_occurrences", []) + contract.get("retained_occurrences", []))
    seen_ids = {item.get("id") for item in occurrences}
    if not expected_ids.issubset(seen_ids): errors.append("渲染 occurrence 集与合同不一致")
    for occurrence in occurrences:
        complete, exploded = occurrence.get("complete_matrix"), occurrence.get("exploded_matrix")
        if not complete or not exploded: errors.append(f"{occurrence.get('id')} 缺少变换审计"); continue
        if _rotation(complete) != _rotation(exploded): errors.append(f"{occurrence.get('id')} 的爆炸态发生旋转")
    projection = render.get("projection", {})
    arrows = projection.get("arrows") or []
    if projection.get("policy") == "same_cad_point/v1":
        if projection.get("status") != "passed" or not arrows:
            errors.append("同点投影箭头尚未通过")
        covered: list[str] = []
        for index, arrow in enumerate(arrows):
            arrow_covered = arrow.get("covered_occurrences") or []
            covered.extend(arrow_covered)
            required = ("anchor_local", "anchor_source", "complete_root", "exploded_root",
                        "complete_screen_plane", "exploded_screen_plane")
            if not arrow_covered or any(arrow.get(key) is None for key in required):
                errors.append(f"箭头 {index + 1} 缺少同点投影审计")
                continue
            complete = arrow["complete_root"]
            exploded = arrow["exploded_root"]
            complete_2d = arrow["complete_screen_plane"]
            exploded_2d = arrow["exploded_screen_plane"]
            if len(complete) != 3 or len(exploded) != 3:
                errors.append(f"箭头 {index + 1} 的根坐标无效")
            if len(complete_2d) != 2 or len(exploded_2d) != 2 or complete_2d == exploded_2d:
                errors.append(f"箭头 {index + 1} 的屏幕投影退化")
            if bool(arrow.get("merged")) != (len(arrow_covered) > 1):
                errors.append(f"箭头 {index + 1} 的合并审计不一致")
        moving = sorted(contract.get("moving_occurrences", []))
        if sorted(covered) != moving:
            errors.append("箭头覆盖的活动 occurrence 与合同不一致")
    elif not projection.get("moving_point_complete") or not projection.get("moving_point_exploded"):
        errors.append("缺少用于箭头的同点投影")
    return errors
