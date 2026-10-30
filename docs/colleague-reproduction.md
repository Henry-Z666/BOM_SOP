# 同事复现操作说明

适用情况：仓库已经拉到本机，希望先成功运行一次，再生成现有 CDU900 部件式 SOP。以下命令均在仓库根目录的 PowerShell 执行。不需要重新克隆。

本路线读取已有的“出版排序内容.json”等中间数据，重现已经完成的装配方案及部件出版。它不重新生成 CAD 图片，也不验证“新原料能否自动规划全部工序”。

2026-10-30 已在独立目录验证：只复制仓库代码/中间数据，先编排，再补齐离线图片包即可生成 51 页工作簿，不使用原始 BOM/CAD 或 OpenAI 运行时。414 项代码测试通过；第 1、9、31 页已使用 LibreOffice 导出实际打印预览并检查。

## 1. 更新代码、准备 Python

先确认终端当前位置能看到 `pyproject.toml`、`scripts/`、`project-data/`。如同事有自己尚未提交的修改，先保留，不要使用强制重置。

```powershell
git pull --ff-only
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install openpyxl Pillow
```

已有可用虚拟环境时，不必重新创建。此出版路线不要求安装整套 GUI/Creo 依赖，也不要求 Node、Codex、OpenAI 工具、GPT 或 API Key。

## 2. 第一条先运行这个：复现编排

```powershell
.\.venv\Scripts\python.exe scripts/cdu900/run_component_publication.py --plan-only
```

这一步不需要原始 BOM、CAD、图片、Creo 或许可证。它自动读取仓库中的关键中间 JSON。

成功标志：终端出现 `PLAN_COMPLETE`，统计为 **40 个部件包、51 页、139 张图片引用、1016 个物料实例**。

结果在 `outputs/复现测试/`：

- `出版内容.json`：用于写入 Excel 的部件、页面、物料、说明及图片引用。
- `部件编排检查.json`：编排数量及范围核对。
- `出图检查.json`：选用图片的历史检查记录。它不是本次重新运行 Creo 的证明。

此时只是编排成功，尚无带图 Excel。

## 3. 补齐图片，生成真正的 Excel

Git 中故意没有原生图片，需要向交接人取得本地图片包 **`同事复现图片包-20261030.zip`**，含本版使用的 139 张图片，不含 BOM/CAD 原件和最终 SOP。图片包通过内部文件传输提供，不提交到 Git。

解压到 `project-data/cdu900-left/`。下面的 ZIP 路径仅为示例，请换成实际收到文件的位置：

```powershell
Expand-Archive -LiteralPath 'D:\交接\同事复现图片包-20261030.zip' `
  -DestinationPath .\project-data\cdu900-left -Force
```

解压后的结构应为：

```text
project-data/cdu900-left/
  analysis/                     # 仓库已有
  bundle-manifest.json          # 仓库已有
  native-images/                # 离线图片包补齐
    00/...
    ...
```

然后执行：

```powershell
.\.venv\Scripts\python.exe scripts/cdu900/run_component_publication.py
```

成功标志：终端出现 `PUBLICATION_COMPLETE`。

打开 `outputs/复现测试/CDU900左机部件式装配SOP.xlsx`，应有 **51 个工作表**，每张卡按部件组织，包含安装图、物料表及操作说明。`SOP交付检查.json` 应显示 `publisher: openpyxl`、`requires_openai: false`、`embedded_image_bytes_unchanged: true`。

出版不需要原始 BOM/CAD 或 Creo。默认检查的是已保存的编排、图片和 Excel；`sources_verified: false` 明确表示本次没有重新核验原料。要做原料核验，须额外取得原始 BOM 和根总装，按映射目录放置后执行：

```powershell
.\.venv\Scripts\python.exe scripts/cdu900/finish-workbook.py outputs/复现测试 --verify-sources
```

## 4. 可选：检查真实打印效果

生成 XLSX 本身不要求 Excel。若要自动导出打印预览，安装本机 LibreOffice 或可用的 Microsoft Excel，并安装预览依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install PyMuPDF
.\.venv\Scripts\python.exe scripts/cdu900/build_component_sop.py outputs/复现测试 --preview=1,9,31
```

Windows 的 Excel 预览还需 `pip install pywin32`；LibreOffice 无需 pywin32。默认优先使用能找到的 LibreOffice，可通过 `--preview-backend=excel` 显式选择 Excel。PNG 在 `outputs/复现测试/SOP页预览/`。预览失败不代表 XLSX 构建失败，可直接用本机表格软件打开查看。

## 5. 如需从 CAD 重新出图

这是另一条较重的运行路线，不是上述复现的前提。需要原始 BOM、完整 CAD 模型、QSL 工艺资料、本机 Creo/J-Link、JDK 和有效许可，并完成运行配置、实例映射、计划和渲染验证。详见根目录 README 的“安装与配置”和“使用方法”。

不要直接把实验性 `publication_rebuild` V0 当作本次交接起点：它用于研究从原料重算规划，目前还保留待确认项，不能替代本版已经整理好的方案。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 找不到 `run_component_publication.py` | 更新仓库，并确认在仓库根目录运行。 |
| 找不到 Python 3.12 | 安装 Python 3.12，或使用已有 Python 3.11+ 环境，替换命令中的解释器路径。 |
| 提示缺少 `openpyxl` 或 `PIL` | 用实际运行脚本的同一个 Python 执行 `-m pip install openpyxl Pillow`。 |
| 提示缺少 native image | 只跑编排可用 `--plan-only`；完整 Excel 需补齐离线图片包。不要创建空白图片冒充。 |
| 报图片哈希改变 | 图片须为原字节，不要通过截图、聊天压缩或重存 JPG 代替；ZIP 传输能保留原字节。 |
| 路径仍指向交接人的电脑 | 保留仓库的 `bundle-manifest.json`，使用默认输入位置，或用入口的 `--input` 指向完整资料目录。 |
| Excel COM 提示拒绝访问/打开失败 | 基本出版不依赖 COM；直接打开 XLSX，或安装 LibreOffice 并使用 `--preview-backend=libreoffice`。 |
| 想换输出目录 | 在统一入口后加 `--output D:\SOP输出`；编排、出版及核对会使用同一目录。 |

维护者生成离线图片包的命令：

```powershell
python scripts/cdu900/export_publication_images.py outputs/复现测试 --zip work/同事复现图片包-20261030.zip
```

该命令要求维护者本地已有选用图片；不会自动上传图片包。
