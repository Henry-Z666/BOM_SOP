from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
skill_names = (
    "intake-preflight", "normalize-bom", "lock-assembly", "discover-cad",
    "map-bom-cad", "plan-assembly", "clarify-plan", "compile-render-jobs",
    "render-batch", "validate-repair", "publish-delivery", "resolve-step",
)

hiddenimports = [
    "dashscope.aigc.generation",
    "dashscope.aigc.multimodal_conversation",
] + collect_submodules("sop_pipeline")
datas = collect_data_files("dashscope")
datas += [(str(root / "skills" / name), f"skills/{name}") for name in skill_names]

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["openai"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="QwenCreoSopAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
