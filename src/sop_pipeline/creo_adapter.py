"""Boundary around the actual Creo-native execution implementation.

This module deliberately never attempts to synthesize a rendering. It hands an
automatically planned contract to a separately registered Creo API script and
records only the manifest returned by that script.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .io import read_json, write_json
from .paths import RUNS
from .product import Product
from .validation import validate_contract, validate_render


class CreoExecutionError(RuntimeError):
    pass


def execute(contract_path: Path, product: Product) -> dict:
    contract = read_json(contract_path)
    errors = validate_contract(contract)
    if errors:
        raise CreoExecutionError("渲染被阻断：" + "；".join(errors))
    command = os.getenv("CREO_RUNNER_COMMAND")
    if not command:
        raise CreoExecutionError("未配置 CREO_RUNNER_COMMAND；需要 Creo 原生 API 执行器，禁止 GUI 点击自动化。")

    run_dir = RUNS / contract["step_id"]
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    assembly = product.models_dir / contract["assembly"]["file"]
    if not assembly.exists():
        raise CreoExecutionError(f"找不到权威 ASM：{assembly}")
    source_models = product.models_dir
    staged_models = run_dir / "models"
    # Creo may write trail/configuration files when opening an ASM. Stage the
    # complete model set so dependency resolution and any transient write stay
    # outside the user's source CAD folder.
    shutil.copytree(source_models, staged_models)
    staged_assembly = staged_models / assembly.relative_to(source_models)
    request = {"contract": contract, "workspace": str(run_dir), "source_assembly": str(staged_assembly),
               "result_manifest": str(run_dir / "result.json"), "source_read_only": True}
    request_path = run_dir / "request.json"
    write_json(request_path, request)
    subprocess.run([command, str(request_path)], cwd=run_dir, check=True, shell=False)
    manifest_path = run_dir / "result.json"
    if not manifest_path.exists():
        raise CreoExecutionError("Creo 执行器没有产生 result.json")
    manifest = read_json(manifest_path)
    contract["render"] = manifest
    contract["automation"]["phase"] = "rendered"
    errors = validate_render(contract)
    if errors:
        raise CreoExecutionError("Creo 输出未通过审计：" + "；".join(errors))
    write_json(contract_path, contract)
    return contract
