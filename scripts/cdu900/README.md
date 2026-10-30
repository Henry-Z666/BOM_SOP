# CDU900 部件式出版

同事已拉好仓库后，请直接阅读[复现操作说明](../../docs/colleague-reproduction.md)。本入口复用已整理好的工序和实例分配，不重新分析 CAD。

## 入口

只复现编排（标准 Python，无需图片）：

```powershell
python scripts/cdu900/run_component_publication.py --plan-only
```

补齐本地图片后出版（需要 openpyxl、Pillow）：

```powershell
python scripts/cdu900/run_component_publication.py
```

默认输入为 `project-data/cdu900-left/`，输出为 `outputs/复现测试/`。可用 `--input`、`--output` 修改。读取配套中间 JSON 和路径映射；原料、图片、成品均不上传 Git。

## 独立步骤

```powershell
$env:CREO_SOP_PUBLICATION_OUT="$PWD\outputs\复现测试"
python scripts/cdu900/plan_component_publication.py
python scripts/cdu900/build_component_sop.py outputs/复现测试
python scripts/cdu900/finish-workbook.py outputs/复现测试
```

`finish-workbook.py --verify-sources` 额外要求本地原始 BOM 和根总装，按记录哈希核对；默认只核对出版结果、图片与物料，不假装已完成原料核验。

只更新已生成工作簿的指定页：

```powershell
python scripts/cdu900/build_component_sop.py outputs/复现测试 --update-pages=43,44
```

可选实际打印预览，需安装 PyMuPDF 及本机 Excel 或 LibreOffice：

```powershell
python scripts/cdu900/build_component_sop.py outputs/复现测试 --preview=1,9,31
```

默认优先使用可用的 LibreOffice，否则使用 Excel。可用 `--preview-backend=excel` 或 `--preview-backend=libreoffice` 指定。预览不是 Excel 出版的前置条件。

## 实现与范围

`src/sop_pipeline/component_workbook.py` 使用 openpyxl 复制仓库模板，保留每部件多图、中文物料、黄色来料标识、动作说明及 A4 横向单页打印。图片按原字节嵌入，保存到临时工作簿并核对通过后才替换目标文件。`build-component-sop.mjs` 仅为旧命令提供转发到 Python 的兼容入口，新流程不需要 Node。

规划器仍是这台 CDU900 的既有出版配方，不宣称可以给其他机型直接使用。新资料重建入口属于独立的实验性 V0，见[说明](../../docs/publication-input-rebuild.md)。

`check_component_cards.py` 另做 BOM 颜色和物料范围核对，要求本地 BOM。`finish_component_delivery.py` 整理已有检查及预览；未实际完成全部页面人工检查时不要使用 `--reviewed`。

维护者可用 `export_publication_images.py <输出目录> --zip <本地zip路径>` 导出选用的图片供离线交接，该脚本不上传文件。
