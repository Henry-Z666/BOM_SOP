"""Invoke a Creo-native extractor and persist a fact-only CAD graph."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .io import read_json
from .product import Product


def discover(product: Product, output: Path, assembly_file: str | None = None) -> Path:
    command = os.getenv("CREO_DISCOVERY_COMMAND")
    if not command: raise RuntimeError("未配置 CREO_DISCOVERY_COMMAND（必须为 Creo OTK/J-Link 原生抽取器）。")
    source = product.models_dir / (assembly_file or product.final_assembly)
    if not source.exists(): raise FileNotFoundError(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([command, str(source), str(output)], check=True, shell=False)
    # Schema validation makes a runner failure explicit before planning starts.
    from .cad_graph import CadGraph
    CadGraph.from_json(read_json(output))
    return output
