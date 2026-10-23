# Creo SOP Agent

面向机械装配的 Windows 桌面工具。读取 Excel BOM、Creo 装配模型和工艺资料，在本地生成装配示意图，并编排为可编辑的 Excel SOP 工艺卡。

BOM 提供物料、层级、数量和工艺要求，Creo 提供真实几何与安装位置。图片可选择 AI 审核或人工复查。

## 产品功能

- **资料解析**：识别 BOM 表头、层级、物料名称、图号、数量及工艺要求，结合工艺卡和参数标准整理连接与力矩信息。
- **装配边界识别**：利用 BOM 层次和黄色来料标记，区分整体安装的部件与需要现场装配的子件，排除整体来料内部的焊接、压铆及制造工序。
- **CAD 装配出图**：通过 Creo 官方 J-Link 接口读取总装、零件位置及约束，生成分阶段安装视图和纯平移爆炸图。
- **取景与标注**：依据安装区域尺寸组织整体位置图和局部连接图，支持红色安装箭头、中文物料名称气泡、位置编号和完整文字力矩要求。
- **断点续跑与局部重做**：保存任务状态和检查点，可暂停、恢复，对不清楚的图片重新生成。
- **Excel 工艺卡**：组合安装图、操作说明、关键控制要点、物料表和工具表，支持一页多图与续页。
- **图片审核**：先检查 CAD 身份、位置、可见性和箭头等条件，再由可选的 Qwen-VL 或人工判断图片是否清楚可用。

部件式工艺卡将紧固件及密封件归入对应部件的安装过程，不按每颗螺钉拆成独立页面。当前部件式版式通过[专项出版入口](scripts/cdu900/README.md)使用；它与桌面通用生成入口分开，产品配方需要匹配相应 BOM/CAD。

## 环境要求

| 组件 | 要求与用途 |
| --- | --- |
| 操作系统 | 64 位 Windows，具备可运行 Creo 和 Excel 的桌面会话 |
| Creo Parametric | 已安装并获得有效许可，包含官方 J-Link 组件；本项目的原生验证环境为 Creo 13.4 |
| Java | JDK 17 或更高版本，`java` 和 `javac` 均须可用 |
| Python | 源码运行需要 Python 3.11 或更高版本，建议使用 Python 3.12 虚拟环境 |
| Microsoft Excel | 桌面版 Excel，用于 COM 出版及工作簿验证；建议使用 64 位版本 |
| AI 审核（可选） | 可用的 DashScope API Key 和网络连接；不配置时使用人工复查 |

Python 依赖由 `pip install -e .` 安装，包括 PySide6、openpyxl、NumPy、Pillow、pywin32 和 DashScope SDK。

使用专项部件出版脚本时，还需要 Node.js 和 Codex 提供的 `@oai/artifact-tool` 运行时。普通桌面入口不需要这套 Node 出版环境。

## 安装与配置

以下命令在 PowerShell 中执行。

### 1. 获取源码并安装依赖

```powershell
git clone https://github.com/Henry-Z666/BOM_SOP.git
cd BOM_SOP

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

也可从 [GitHub Releases](https://github.com/Henry-Z666/BOM_SOP/releases) 获取已发布安装包。安装包仍需本机的 Creo、JDK、Excel 和有效许可证；需要当前源码功能时，可按下方方法自行构建。

### 2. 配置本机运行环境

首次使用时复制配置示例，已有配置时保留原文件：

```powershell
if (-not (Test-Path .\config\creo-runtime.json)) {
  Copy-Item .\config\creo-runtime.example.json .\config\creo-runtime.json
}
```

编辑 `config/creo-runtime.json`：

| 字段 | 填写内容 |
| --- | --- |
| `creo_loadpoint` | Creo 安装根目录，即包含 `Common Files` 和 `Parametric` 的目录 |
| `license_file` | 本机有效的 Creo 许可证文件完整路径 |
| `java_command` | `java` 命令或 `java.exe` 完整路径 |
| `javac_command` | `javac` 命令或 `javac.exe` 完整路径 |
| `python_command` | 项目虚拟环境中 `python.exe` 的路径 |
| `creo_app` | 默认 `PMA`，按实际安装配置使用 |
| `creo_feature_name` | 可选的许可功能名称，通常保留空字符串 |

JSON 中 Windows 路径的反斜杠需要写成 `\\`，格式可参照[配置示例](config/creo-runtime.example.json)。本机配置已被 Git 忽略，不提交许可证或 API Key。

### 3. 检查环境并编译原生接口

```powershell
powershell -NoProfile -File .\scripts\preflight_process.ps1 `
  -RuntimeConfig .\config\creo-runtime.json

powershell -NoProfile -File .\creo_java\build.ps1 `
  -RuntimeConfig .\config\creo-runtime.json
```

环境预检检查路径、必要组件和 Python 依赖，不代替实际启动 Creo 的验证。

### 4. 启动桌面工具

```powershell
.\.venv\Scripts\creo-sop-agent.exe
```

## 使用方法

### 准备输入资料

- **Excel BOM**：保留层次、物料编码、图号、中文名称、数量及单位。需要使用颜色分层时，保留原始层次单元格的填充色。
- **Creo 模型目录**：包含对应版本的最终总装及其引用的 ASM/PRT 模型，能够在 Creo 中正常解析引用关系。
- **工艺资料（按需）**：提供装配工艺卡、力矩规格、工具及关键控制要求，并确认其适用机型。

BOM 与 CAD 应属于同一产品版本。黄色来料边界来自 BOM 的真实填充色，不以 CAD 外观颜色或名称中是否含“焊件”来替代判断。

### 分析、生成与复查

1. 在界面中填写 Creo 安装目录、许可证文件及所需的运行环境配置。
2. 点击“选择 BOM”和“选择 CAD 文件夹”，再点击“分析 BOM 与装配方案”。
3. 处理模型匹配、来料范围、安装方向等需要确认的事实，确认后点击“确认并开始生成”。
4. 查看生成进度。长任务可使用“暂停（保留检查点）”，之后通过任务历史继续。
5. 对待复查图片选择采用、结构化重新生成或跳过。重新生成只处理相关步骤；自由备注不会直接改写 CAD 几何。
6. 完成后点击“打开交付目录”，查看 SOP 工作簿、步骤图片及待处理结果。

首次接入一套材料时，先用少量典型部件检查模型对应关系、安装方向、取景、标注和力矩适用范围，再扩大生成范围。

### AI 审核与本地处理

AI 审核是可选功能。在界面填写“DashScope Key（AI审核）”即可启用，默认模型为 `qwen3-vl-plus`。审核会将待审图片及必要的步骤上下文发送至阿里云 DashScope；CAD 几何计算和出图仍在本机 Creo 中完成。

需要纯本地处理时，不配置 Key，图片转入人工复查。AI 不通过、服务不可用或响应异常时，也保留人工处理入口。AI 审核不会绕过 CAD 结构检查。

## 部件式 SOP 与示例

部件式工艺卡以框架、泵、换热器、管路、电控箱、膨胀罐、水箱、门板等部件组织内容，在部件内部说明安装顺序和连接方法。

- [查看 Excel 示例与逐页预览](deliverables/cdu900-left/2026-10-15/README.md)
- [专项出版脚本、输入要求与运行方法](scripts/cdu900/README.md)
- [部件编排规则](rules/component-based-publication.md)
- [BOM 颜色与装配边界](rules/bom-color-assembly-boundaries.md)
- [力矩来源与适用范围](rules/torque-source-matching.md)

示例和产品配方可用于了解版式；换用其他产品时，需要重新建立 BOM/CAD 映射、部件分组和工艺适用关系。

## 命令行入口

安装项目后可查看命令帮助：

```powershell
.\.venv\Scripts\creo-sop-process.exe --help
.\.venv\Scripts\creo-sop-skill.exe --help
```

`creo-sop-process` 用于工艺资料解析、重建和工艺任务处理；`creo-sop-skill` 用于运行单个流程环节。专项部件出版的详细命令见[出版入口说明](scripts/cdu900/README.md)。

## 构建 Windows EXE

配置本机环境后，在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
powershell -NoProfile -File .\packaging\build.ps1 `
  -RuntimeConfig .\config\creo-runtime.json `
  -PythonCommand .\.venv\Scripts\python.exe
```

输出文件为 `dist/CreoSopAgent.exe`。脚本会先编译 Java 接口，再打包 Python 程序、模板、Skill 和原生脚本。目标电脑仍需安装 Creo、JDK、Excel，并配置自己的路径和授权。

## 常见问题

| 问题 | 处理方法 |
| --- | --- |
| 无法启动 Creo 或许可校验失败 | 核对安装根目录、许可文件、许可功能及 Java 路径，并确认本机 Creo 可正常启动。 |
| 模型引用或 BOM 匹配失败 | 核对产品版本、模型搜索路径、图号、模型参数和已确认的替代关系，修正后重新分析。 |
| 部件过小、连接处被遮挡 | 在复查入口修订取景或使用另一固定视角，重做受影响的步骤。 |
| AI 审核未自动通过 | 查看失败原因，人工复查图片或重新生成；未配置 Key 时进入人工复查属于正常流程。 |
| 任务中断后希望继续 | 保留运行目录、日志及检查点，通过任务历史恢复；同一 Creo 渲染队列保持串行运行。 |
| 运行期间修改了源 CAD | 恢复已锁定版本，或使用新版本重新分析和生成计划。 |

## 项目目录

| 目录 | 内容 |
| --- | --- |
| `src/` | 桌面界面、任务编排、资料解析、校验及出版代码 |
| `creo_java/` | Creo/J-Link 原生接口与运行脚本 |
| `config/` | 运行环境配置示例 |
| `assets/` | SOP 模板及出版资源 |
| `scripts/` | 预检、辅助工具和产品出版配方 |
| `rules/` | 装配边界、取景、标注和出版规则 |
| `docs/` | 技术说明、设计决策、版本与验证记录 |
| `tests/` | 自动化测试 |
| `deliverables/` | 明确选入仓库的成品示例、预览及校验清单 |

原始 BOM/CAD、本机许可证、API Key、运行数据库、历史出图缓存和构建产物保存在本地，由 `.gitignore` 排除。

## 开发与技术文档

运行 Python 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

- [产品契约与几何约束](docs/deterministic-creo-sop-contract.md)
- [正式渲染规则](rules/render-rules.md)
- [任务状态与步骤隔离](docs/adr/0001-durable-run-state-and-step-isolation.md)
- [原生箭头与可迁移推导](docs/arrow-generation-and-portability.md)
- [SOP 出版说明](docs/spreadsheet-sop-publication.md)
- [版本记录](docs/releases/)

开发进度和历史验证数据保存在 `docs/`；README 重整前的记录归档于[历史快照](docs/history/readme-before-product-guide-20261023.md)。
