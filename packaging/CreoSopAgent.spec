from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
skill_names = (
    "intake-preflight", "normalize-bom", "lock-assembly", "discover-cad",
    "map-bom-cad", "plan-assembly", "clarify-plan", "compile-render-jobs",
    "render-batch", "validate-repair", "publish-delivery", "resolve-step",
)

hiddenimports = collect_submodules("sop_pipeline") + collect_submodules("dashscope")
datas = [(str(root / "skills" / name), f"skills/{name}") for name in skill_names]
datas += [(str(root / "assets" / "sop-template.xlsx"), "assets")]
datas += collect_data_files("dashscope")
datas += [
    (str(root / "creo_java" / "RuntimeConfig.ps1"), "creo_java"),
    (str(root / "creo_java" / "build.ps1"), "creo_java"),
    (str(root / "creo_java" / "run_input_discovery.ps1"), "creo_java"),
    (str(root / "creo_java" / "run_agent_native_batch.ps1"), "creo_java"),
    (str(root / "creo_java" / "invoke_agent_native_jlink.ps1"), "creo_java"),
    (str(root / "creo_java" / "invoke_agent_native_worker.ps1"), "creo_java"),
    (str(root / "creo_java" / "stop_agent_native_worker.ps1"), "creo_java"),
    (str(root / "creo_java" / "test_license_binding.ps1"), "creo_java"),
    (str(root / "creo_java" / "isolated_config.pro"), "creo_java"),
    (str(root / "scripts" / "fit_creo_image.ps1"), "scripts"),
    (str(root / "creo_java" / "src" / "AutoCadDiscovery.java"), "creo_java/src"),
    (str(root / "creo_java" / "src" / "ArrowProjection.java"), "creo_java/src"),
    (str(root / "creo_java" / "src" / "RenderAssemblyImage.java"), "creo_java/src"),
    (str(root / "creo_java" / "src" / "NativeArrowBatch.java"), "creo_java/src"),
    (str(root / "creo_java" / "src" / "NativeArrowWorker.java"), "creo_java/src"),
]
compiled_root = root / "creo_java" / "build"
required_classes = ("AutoCadDiscovery.class", "NativeArrowWorker.class")
if any(not (compiled_root / name).is_file() for name in required_classes):
    raise FileNotFoundError(
        "Current J-Link classes are required; run packaging/build.ps1 before "
        "PyInstaller analysis"
    )
datas.append((str(compiled_root), "creo_java/build"))

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
# These are Windows system APIs, not redistributable toolchain dependencies.
# Some Python host environments inject Poppler/libheif DLL search directories
# even with a clean PATH. Their ICU exports are incompatible with Qt's imports.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {"icuuc.dll", "ucrtbase.dll"}]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CreoSopAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
