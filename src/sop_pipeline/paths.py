from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT_BOM = ROOT / "BOM.xlsx"
INPUT_PARTS = ROOT / "零件图"
INPUT_TEMPLATE = ROOT / "SOP示例.xlsx"
DATA = ROOT / "data"
CONTRACTS = DATA / "contracts"
RUNS = DATA / "runs"
OUTPUTS = ROOT / "outputs"
