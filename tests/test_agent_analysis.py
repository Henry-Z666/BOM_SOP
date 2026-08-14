from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from sop_pipeline.agent import AgentCore
from sop_pipeline.agent.local_workflow import LocalAnalysisWorkflow


def _xlsx(path: Path, sheets: list[tuple[str, list[list[str]]]]) -> None:
    workbook_sheets = "".join(
        f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _) in enumerate(sheets, 1)
    )
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{workbook_sheets}</sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relationships}</Relationships>",
        )
        for sheet_index, (_, rows) in enumerate(sheets, 1):
            xml_rows = []
            for row_index, values in enumerate(rows, 1):
                cells = []
                for column_index, value in enumerate(values, 1):
                    column = chr(64 + column_index)
                    cells.append(
                        f'<c r="{column}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>'
                    )
                xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
            archive.writestr(
                f"xl/worksheets/sheet{sheet_index}.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
            )


class LocalAnalysisWorkflowTests(unittest.TestCase):
    def test_two_inputs_produce_a_stable_pre_generation_summary(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            _xlsx(
                bom,
                [
                    ("说明", [["本文件由ERP导出"], ["请勿修改"]]),
                    (
                        "装配BOM",
                        [
                            ["层级", "物料编码", "图号", "名称", "型号", "数量", "单位", "装配步骤", "关键控制点", "工具"],
                            ["30", "ROOT", "ROOT-ASM", "设备总装", "", "1", "件", "第3步：目视检查", "", ""],
                            ["30.1", "A", "PART-A", "底座", "", "1", "件", "第1步：固定底座", "保持水平", "固定工装"],
                            ["30.2", "B", "PART-B", "顶板", "", "1", "件", "第2步：安装顶板", "螺钉拧紧", "电批"],
                            ["30.2.1", "RAW", "RAW-PLATE", "不锈钢板", "1.5-304", "0.2", "千克", "", "", ""],
                        ],
                    ),
                ],
            )
            cad = root / "cad"
            cad.mkdir()
            (cad / "root-asm.asm.2").write_bytes(b"root")
            (cad / "part-a.prt.1").write_bytes(b"a")
            (cad / "part-b.prt.1").write_bytes(b"b")
            core = AgentCore(root / "agent-workspace", workflow=LocalAnalysisWorkflow())

            run_id = core.create_run(bom, cad)
            packet = core.analyze(run_id)

        self.assertEqual(packet.facts["bom_sheet"], "装配BOM")
        self.assertEqual(packet.facts["bom_rows"], 4)
        self.assertEqual(packet.facts["main_processes"], 3)
        self.assertEqual(packet.facts["renderable_main_processes"], 2)
        self.assertEqual(packet.facts["draft_installation_steps"], 2)
        self.assertEqual(packet.facts["final_assembly"], "root-asm.asm.2")
        self.assertEqual(packet.facts["non_modeled_bom_rows"], 1)
        self.assertEqual(packet.items, ())

    def test_multiple_equally_ranked_assemblies_become_a_confirmation_item(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            _xlsx(
                bom,
                [(
                    "BOM",
                    [
                        ["层级", "物料编码", "图号", "名称", "数量", "单位", "装配步骤"],
                        ["30", "ROOT", "UNKNOWN", "设备总装", "1", "件", "第1步：安装总装"],
                    ],
                )],
            )
            cad = root / "cad"
            cad.mkdir()
            (cad / "candidate-a.asm.1").write_bytes(b"a")
            (cad / "candidate-b.asm.1").write_bytes(b"b")
            core = AgentCore(root / "agent-workspace", workflow=LocalAnalysisWorkflow())

            packet = core.analyze(core.create_run(bom, cad))

        questions = {item.item_id: item for item in packet.items}
        self.assertIn("select-final-assembly", questions)
        self.assertEqual(
            questions["select-final-assembly"].options,
            ("candidate-a.asm.1", "candidate-b.asm.1"),
        )
        self.assertEqual(packet.facts["final_assembly"], "candidate-a.asm.1")

    def test_analysis_is_identical_across_ten_clean_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bom = root / "BOM.xlsx"
            _xlsx(
                bom,
                [(
                    "BOM",
                    [
                        ["层级", "物料编码", "图号", "名称", "数量", "单位", "装配步骤"],
                        ["30", "ROOT", "ROOT-ASM", "设备总装", "1", "件", "第2步：目视检查"],
                        ["30.1", "A", "PART-A", "底座", "1", "件", "第一步：固定底座"],
                    ],
                )],
            )
            cad = root / "cad"
            cad.mkdir()
            (cad / "root-asm.asm.1").write_bytes(b"root")
            (cad / "part-a.prt.1").write_bytes(b"part")

            packets = []
            fingerprints = []
            for index in range(10):
                core = AgentCore(root / f"workspace-{index}", workflow=LocalAnalysisWorkflow())
                run_id = core.create_run(bom, cad)
                packets.append(core.analyze(run_id))
                fingerprints.append(core.get_run(run_id).input_fingerprint)

        self.assertTrue(all(packet == packets[0] for packet in packets[1:]))
        self.assertEqual(len(set(fingerprints)), 1)


if __name__ == "__main__":
    unittest.main()
