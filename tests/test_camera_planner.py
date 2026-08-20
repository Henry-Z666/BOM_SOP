import math
import unittest

from sop_pipeline.camera_planner import (
    absolute_view_matrix,
    calibrate_camera_basis,
    classify_receiver_face,
    determinant3,
    dot,
    generate_camera_candidates,
    opposite,
    score_camera_candidate,
    select_camera,
)
from sop_pipeline.validation import validate_camera_contract


class CameraPlannerTests(unittest.TestCase):
    def setUp(self):
        self.default = absolute_view_matrix([1, -1, 1])
        self.basis = calibrate_camera_basis("tank.asm.1", "abc", self.default)

    def test_default_and_opposite_face_numbering(self):
        self.assertEqual(self.basis["faces"]["1"]["axis_label"], "+X")
        self.assertEqual(self.basis["faces"]["2"]["axis_label"], "-Y")
        self.assertEqual(self.basis["faces"]["3"]["axis_label"], "+Z")
        self.assertEqual(self.basis["faces"]["4"]["axis_label"], "-X")
        self.assertEqual(self.basis["faces"]["5"]["axis_label"], "+Y")
        self.assertEqual(self.basis["faces"]["6"]["axis_label"], "-Z")
        for left, right in ((1, 4), (2, 5), (3, 6)):
            self.assertEqual(self.basis["faces"][str(left)]["axis"], self.basis["faces"][str(right)]["axis"])
            self.assertEqual(self.basis["faces"][str(left)]["sign"], -self.basis["faces"][str(right)]["sign"])

    def test_absolute_matrix_is_right_handed_and_orthonormal(self):
        matrix = absolute_view_matrix([-2, 3, 4], [0, 0, 1])
        self.assertAlmostEqual(determinant3(matrix), 1.0, places=9)
        for row in matrix[:3]:
            self.assertAlmostEqual(dot(row[:3], row[:3]), 1.0, places=9)
        self.assertAlmostEqual(dot(matrix[0][:3], matrix[1][:3]), 0.0, places=9)

    def test_opposite_is_exact_negation(self):
        direction = self.basis["default_position_direction_root"]
        for value, expected in zip(opposite(direction), self.basis["opposite_position_direction_root"]):
            self.assertAlmostEqual(value, expected, places=12)

    def test_candidates_never_cross_receiver_half_space(self):
        face = classify_receiver_face([-0.99, 0.05, 0.02], self.basis)
        candidates = generate_camera_candidates(self.basis, face, [160, 0, 0])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "fixed_456")
        for candidate in candidates:
            self.assertGreater(dot(face["normal_root"], candidate["position_direction_root"]), 0.0)

    def test_fixed_views_replay_saved_default_and_exact_centre_opposite(self):
        saved = absolute_view_matrix([0.79, 0.16, -0.59], [0.1, 0.2, 1.0])
        basis = calibrate_camera_basis("parent.asm.1", "hash", saved)
        face_2 = classify_receiver_face([0, 1, 0], basis)
        face_5 = classify_receiver_face([0, -1, 0], basis)
        fixed_123 = generate_camera_candidates(basis, face_2, [45, 142, -59])[0]
        fixed_456 = generate_camera_candidates(basis, face_5, [45, -142, -59])[0]
        self.assertEqual(fixed_123["id"], "fixed_123")
        self.assertEqual(fixed_456["id"], "fixed_456")
        for actual_row, saved_row in zip(basis["fixed_123_view_matrix"][:3], saved[:3]):
            for actual, expected in zip(actual_row[:3], saved_row[:3]):
                self.assertAlmostEqual(actual, expected, places=12)
        for left, right in zip(fixed_123["position_direction_root"], fixed_456["position_direction_root"]):
            self.assertAlmostEqual(left, -right, places=12)
        default_matrix = basis["fixed_123_view_matrix"]
        opposite_matrix = basis["fixed_456_view_matrix"]
        for row in range(3):
            self.assertAlmostEqual(opposite_matrix[row][0], -default_matrix[row][0], places=12)
            self.assertAlmostEqual(opposite_matrix[row][1], default_matrix[row][1], places=12)
            self.assertAlmostEqual(opposite_matrix[row][2], -default_matrix[row][2], places=12)

    def test_orthographic_open_view_is_completed_to_a_trihedral_octant(self):
        saved = absolute_view_matrix([0, 0, 1], [0, 1, 0])
        basis = calibrate_camera_basis("parent.asm.1", "hash", saved)

        self.assertEqual(basis["schema_version"], "assembly-camera-basis/v4")
        self.assertEqual(
            basis["calibration"]["fallback"], "equal_octant_completion/v1"
        )
        self.assertFalse(basis["calibration"]["source_trihedral"])
        self.assertTrue(basis["calibration"]["trihedral"])
        expected = 1.0 / math.sqrt(3.0)
        for value in basis["fixed_123_position_direction_root"]:
            self.assertAlmostEqual(value, expected, places=12)
        for left, right in zip(
            basis["fixed_123_position_direction_root"],
            basis["fixed_456_position_direction_root"],
        ):
            self.assertAlmostEqual(left, -right, places=12)

    def test_face_confidence_uses_25_degree_limit(self):
        high = classify_receiver_face([1, 0.2, 0], self.basis)
        low = classify_receiver_face([1, 1, 0], self.basis)
        self.assertEqual(high["confidence"], "high")
        self.assertEqual(low["confidence"], "low")

    def test_preview_hard_gates_override_score(self):
        face = classify_receiver_face([1, 0, 0], self.basis)
        raw = generate_camera_candidates(self.basis, face, [0, 160, 0])
        failed = score_camera_candidate(raw[0], receiver_boundary_visible=False,
                                        hole_min_pixel_gap=50, occlusion_score=0,
                                        frame_coverage=0.7)
        passed_input = {**raw[0], "id": "same_fixed_view_with_valid_preview"}
        passed = score_camera_candidate(passed_input, receiver_boundary_visible=True,
                                        hole_min_pixel_gap=20, occlusion_score=0.2,
                                        frame_coverage=0.7)
        self.assertFalse(failed["eligible"])
        self.assertEqual(select_camera([failed, passed])["id"], passed["id"])

    def test_v3_contract_rejects_relative_rotation(self):
        face = classify_receiver_face([1, 0, 0], self.basis)
        face["evidence"] = "Creo planar receiver face"
        selected = generate_camera_candidates(self.basis, face, [0, 160, 0])[0]
        contract = {"schema_version": "creo-stage-camera-contract/v3", "coordinate_system": "root_asm",
                    "receiver_face": face, "candidates": [selected], "selected": selected,
                    "view_policy": {"id": "fixed_two_view/v1", "view_group": "123"},
                    "framing": {"zoom": 1.0, "pan": None}}
        self.assertEqual(validate_camera_contract(contract), [])
        contract["camera_rotate"] = "Y:180"
        self.assertIn("新相机合同禁止相对旋转 camera_rotate", validate_camera_contract(contract))

    def test_focus_contract_requires_simplified_stage_and_locked_direction(self):
        face = classify_receiver_face([1, 0, 0], self.basis)
        face["evidence"] = "Creo planar receiver face"
        selected = generate_camera_candidates(self.basis, face, [0, 160, 0])[0]
        contract = {"schema_version": "creo-stage-camera-contract/v3", "coordinate_system": "root_asm",
                    "receiver_face": face, "candidates": [selected], "selected": selected,
                    "view_policy": {"id": "fixed_two_view/v1", "view_group": "123"},
                    "framing": {"zoom": 1.0, "pan": [0, 0], "look_at_stage": True,
                                "focus_context": {"policy": "stage_visible_bbox/v1",
                                                  "occlusion_policy": "temporary_simplified_rep/v1",
                                                  "section_fallback": "receiver_normal_only/v1"}}}
        self.assertEqual(validate_camera_contract(contract), [])
        contract["framing"]["look_at_stage"] = False
        self.assertIn("特写焦点必须启用阶段中心化", validate_camera_contract(contract))


if __name__ == "__main__":
    unittest.main()
