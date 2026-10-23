# Creo SOP Agent

## 最新成果（2026-10-23 整理入仓）

已按用户提供的总装工艺卡，完成 **40 个部件工作包、51 页主要 CAD 装配工艺卡**。紧固件及密封件归入对应部件，黄色 BOM 来料边界保持完整；本版按用户要求省略标签、现场照片、检查和包装页。

- [Excel 成品及逐页预览](deliverables/cdu900-left/2026-10-15/README.md)
- [本次版本说明、验证与复现条件](docs/component-sop-delivery-20261015.md)
- [按部件出版规则](rules/component-based-publication.md)
- [已整理的出版脚本](scripts/cdu900/README.md)

下方记录保留各阶段的历史范围；最新出品以本节链接为准。

2026-10-13 已完成100步阶段装配SOP：61页、228张原生图，覆盖框架安装、门锁拉杆及管路分装。新版BOM颜色边界已固化到正式计划、任务编译与出版入口；无颜色证据的旧计划拒绝编译，内部制造件和重复领料在出版时再次阻止。见 [100步阶段出品](docs/stage-100-step-delivery-20261013.md) 与 [通用颜色边界规则](rules/bom-color-assembly-boundaries.md)。

2026-10-12 已完成新版颜色与层级规则下的32步框架装配阶段性SOP：28页、110张原生图、一页多图。独立核对原始BOM底色与实际Excel页表，内部制造行没有新增工序或重复计料，嵌套黄色子件也随外层完整发料组件排除。见 [32步出品与颜色自检](docs/frame-32-step-delivery-20261012.md)。

2026-10-12 已按新版 BOM 黄色发料组件重构装配边界，排除完整组件内部的制造步骤，避免提前显示整个未装框架。四根立柱安装阶段已完成 17 张实机图和 4 页多图 SOP。见 [完整发料组件规则与阶段成果](docs/supplied-assembly-boundaries-20261012.md)。

物料气泡默认直接显示新版报表中的中文名称；位置编号仍关联局部组，统一使用力矩气泡样式，并可与完整力矩文字共同布局。见 [气泡融合验证及名称规则](docs/material-number-callouts-20261010.md)。

2026-10-10 通用取景默认规则已升级为 v15：按 CAD 安装特征尺寸选择单图或定位＋全部局部组，原生视口标定，定位编号绑定安装完成点，逐件排除对照检查遮挡。规则与验证范围见 [通用取景规则](rules/adaptive-installation-framing.md) 和 [实机验证记录](docs/通用取景验证记录-20261010.md)。这是小批量诊断出图能力；正式多图编译、含力矩标签重新绑定及全组出版仍需接通验收。

2026-10-08 材料基线已更新：新版原件保留在 `AI生成SOP外发资料/`，桌面 `左机材料/` 已同步。旧材料、历史运行与交付输出已归档，不能作为新版验证结果复用。整理范围与待核实差异见 [材料更新记录](docs/materials-refresh-20261008.md)。工艺操作与力矩标注的第一阶段入口、验证范围及最新实机结果见 [重构阶段成果](docs/process-mvp-20261008.md)。

Windows 桌面工具：使用 Excel BOM、Creo ASM/PRT 模型、本地确定性规则和可选的 Qwen-VL 图片审核，生成安装步骤图片与可编辑 SOP。CAD 真值始终由本地 Creo 硬门控制；AI 只判断渲染图片是否清楚可用。

正式调用链：

```text
GUI → AgentCore → PipelineOrchestrator → SkillRuntime
    → Creo/J-Link → 确定性硬门 → AI图片审核 → Excel 出版/人工复查
```

## 当前能力

- 识别 BOM 工作表、表头、层级、数量和工艺文字。
- 锁定最终总装版本与 SHA-256，扫描完整 occurrence、约束和根坐标系几何。
- 从 BOM 与 Creo 图谱生成依赖关系、完整安装态、分阶段可见集和纯平移爆炸合同。
- 使用根坐标系内预定义的 `fixed_123`、`fixed_456`，按接收面法向与爆炸投影稳定选择一次。
- 确定性硬门通过后调用 Qwen-VL 审核真实图片；AI 判断正确的步骤自动进入 SOP，AI 不通过、未配置或调用失败的步骤才进入人工复查。
- 每次渲染只执行一个相机变体；人工退回后可通过有界选项切换另一固定相机、修订爆炸或重建可见集，只重跑当前步骤。
- 通过 Creo/J-Link 原生 `DisplayList3D` 在同一 CAD 锚点绘制箭头。
- 复用常驻 Creo Worker、逐步骤检查点，并隔离单步骤失败。
- 通过结构化硬门验证图片，把已验证的图片和 BOM 文字写入 SOP。

## 执行流程

```text
分析：intake-preflight → normalize-bom → lock-assembly → discover-cad
    → map-bom-cad → plan-assembly → clarify-plan

生成：compile-render-jobs → render-batch → validate-repair
    → publish-delivery

释疑：resolve-step → 最小失效范围 → 必要步骤重跑 → 重新出版
```

缺失安装方向时只接受结构化的 ±X、±Y、±Z 选择或明确轴向句式。occurrence、接收件、相机和取景参数不能通过自由文本覆盖。

## 本机配置

复制 Creo 配置示例，不要提交真实路径或许可证：

```powershell
Copy-Item ./config/creo-runtime.example.json ./config/creo-runtime.json
```

正式运行依赖本地 Creo/J-Link、Excel 和 Python 组件。`pywin32` 用于 64 位 Windows 上的 Excel COM 出版。要启用 AI 图片审核，可在桌面界面填写 DashScope Key，或设置 `DASHSCOPE_API_KEY`；Key 使用 Windows DPAPI 保存。未配置 Key 时不会误放行，图片自动转人工复查。

## 开发入口

```powershell
python -m pip install -e .
creo-sop-agent
```

独立调试 Skill：

```powershell
creo-sop-skill `
  --workspace <Agent工作区> `
  --run-id <运行标识> `
  --skill normalize-bom `
  --input-ref analysis/input-manifest.json
```

运行测试：

```powershell
python -m pytest -q
```

真实 Creo 小批量冒烟：

```powershell
python ./scripts/smoke_agent_single_step.py `
  --bom ./BOM.xlsx `
  --cad ./零件图 `
  --step-count 3
```

## 构建 Windows EXE

新版 EXE 从 [GitHub Releases](https://github.com/Henry-Z666/BOM_SOP/releases) 下载；源码提交与 EXE 发布版本一一对应，EXE 不放进 Git 历史。发布包含 SHA-256 校验文件。

在仓库根目录执行。构建机需要 Windows、Python 3.11+、JDK 17+、已安装且可用的 Creo/J-Link；真实出版还需要桌面版 Excel。当前原生验证环境为 Creo 13.4。不要直接跳过 Java 编译运行 PyInstaller，否则可能把旧渲染器打进安装包。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
# 仅首次创建；已有本机配置时不要覆盖
if (-not (Test-Path .\config\creo-runtime.json)) {
  Copy-Item .\config\creo-runtime.example.json .\config\creo-runtime.json
}
# 先编辑配置中的 Creo、许可证、java_command、javac_command、python_command
powershell -NoProfile -File .\packaging\build.ps1 `
  -RuntimeConfig .\config\creo-runtime.json `
  -PythonCommand .\.venv\Scripts\python.exe
```

成功后得到单文件 `dist/CreoSopAgent.exe`。构建脚本先编译 J-Link 类，再打包 Python 依赖、DashScope SDK、模板、Skill 和原生脚本。它不会打包 Creo、Java、Excel、许可证或 API Key。目标电脑仍须安装 Creo/J-Link、JDK（当前运行时也检查 `javac`）及桌面 Excel，并填写自己的路径和授权。GUI 使用打包的 Python 运行时；若手动提供运行时 JSON，其中的 `python_command` 仍须指向可解析的命令。v0.2.0 已重新编译、打包并通过 270 项测试和 65 项子测试；包内模板及关键 Java 类与构建源文件哈希一致。此项发布验证不等同于用 EXE 再跑一遍数小时的全量 Creo/AI 流程。

打包时会隔离外部 DLL 搜索路径，并排除工具链注入的 ICU/UCRT 同名库，避免 QtCore 导入失败。v0.2.0 最终 EXE 已验证后台入口启动，以及桌面主窗口的创建和关闭。

## 如何使用 AI 审核

1. 启动 EXE，填写 Creo 安装目录、许可证和 Excel 路径；确认 BOM 与 CAD 文件夹属于同一产品版本。
2. 需要自动审核时填写界面的 `DashScope Key（AI审核）`。启用意味着将待审图片及必要步骤上下文交给阿里云 DashScope；请先确认材料允许外发。不要把 Key 写进仓库、截图或交付目录。
3. 分析 BOM/CAD，处理缺件、映射歧义、安装方向等释疑，再开始生成。
4. 结构硬门先判定几何与箭头合同。硬门失败不会交给 AI“豁免”；结构合格的正式图进入图片审核，通过后自动纳入 SOP。
5. AI 拒绝、网络/响应异常或未配置 Key 的步骤进入人工复查。选择采用、结构化二次生成或跳过；备注本身不改变几何。
6. 核对交付 SOP 和待复查列表，不把“已处理步骤数”当作“已通过图片数”。当前主流程在渲染批次结束后审核，不是每出一张立即审核。

默认模型为 `qwen3-vl-plus`，默认关闭思考模式，不自动切换 Max 兜底。源代码/无界面入口可在启动前设置：

```powershell
$env:CREO_SOP_AI_REVIEW_MODEL = 'qwen3-vl-plus'
$env:CREO_SOP_AI_REVIEW_THINKING = '0'
# 无界面入口禁用 AI（保留人工复查）
$env:CREO_SOP_AI_REVIEW = '0'
```

注意：当前 GUI 在检测到 Key 并开始分析时会重新启用 AI，因此上述关闭变量不是 GUI 的可靠关闭开关；GUI 尚无独立禁用开关。未配置 Key 的环境走人工复查。AI 通过率不是准确率，仍需抽检并统计漏放、误拒、耗时和请求失败。

### 常见使用场景

| 场景 | 建议操作与验收 |
| --- | --- |
| 首次接入一套材料 | 先分析映射，再跑少量不同复杂度、不同总装步骤；检查原始 CAD 哈希、箭头、取景和 AI 结论，再扩大批量。 |
| 多个独立总装 | 使用森林计划；每个任务保留所属总装，合并任务采用全局索引。核对日志中的 `authoritative_assembly` 与计划一致。 |
| 只允许本地处理 | 不配置 AI Key，使用确定性检查和人工复查，不发送图片。 |
| 部分图片不清楚 | 人工选择另一固定相机或有界修订选项，只重跑相关步骤；不放宽结构门、不创建第三相机。 |
| 长任务异常或中断 | 保留运行目录、日志和检查点，先检查任务身份、图片哈希及耗时；不要同时启动两个 Creo 渲染批次。 |

## 性能优化与异常排查

- 先建立当前步骤的可见状态，只清理相关分支及刚性子装配内的辅助特征；按模型去重清理，但不丢弃重复 occurrence 的遍历。每步恢复独立状态，不修改源 CAD。
- 清理前先检查整个可见分支的子模型能否读取，再执行图层操作。遇到 `CREO_VISIBLE_MODEL_MISSING` 时查看日志中的 occurrence 和 model，补齐正确源文件后重新分析/锁定输入；不要覆盖旧锁定清单或自动反复重试。缺件不能通过 AI 放行。
- 常驻 Worker 减少启动成本。子进程输出写临时文件，避免继承管道使调用端额外等待 Worker 空闲退出。
- 多总装编译后重新分配全局 `plan_index`；原生入口用 `ExpectedTaskId` 二次核对，身份不一致立即失败且不重试，防止渲染错误总装和覆盖旧图。
- 原生构建检查全部五个 Java 入口/依赖源码，而不只检查 Worker，避免修改渲染器后仍执行旧 class。
- 日志新增 `model_open_seconds`、`stage_visibility_seconds`，并保留辅助特征清理耗时。总耗时还包含进程启动、渲染、导出和校验，不能把差额全部归因于读取总装。

2026-09-05 左机迭代发现：旧计划 536 个任务中有 370 个索引不匹配；314 个检查点条目仅对应 147 张 JPEG，另有 4 张图片哈希与检查点不符。旧批次不是健康运行，不能直接据此推算每张成本或复用已通过状态。原始 BOM 和 515 个 CAD 文件哈希未变。修复后跨两个总装实测单步为 33.5 秒、42.3 秒；这只证明选中任务恢复正常，不代表复杂大总装已全面达标。

后续验证优先选跨总装、复杂步骤和硬门失败负样本，分别报告新图数量、结构合格数、真实 AI 调用数和人工复查数，不再为凑满 500 张长时间盲跑。详见 [左机迭代验证记录](docs/left-material-iteration-20260905.md)。

同日复杂步骤追测确认：总装打开约 12–17 秒，可见状态设置约 13 秒；原流程 180 秒仍未完成，前置模型检查后 65.6 秒明确报出缺失引用 `317/963/125445 → DKBA81229151.prt`，且阻止无意义的自动重试。这是缩短缺件失败反馈时间，不是把失败步骤变成成功图片。

首轮小样本 `qwen3-vl-plus` 实测：5 张结构合格图中 4 张通过、1 张转人工复查，另 1 张硬门失败图未上传；平均 2.537 秒/张，无调用错误。

2026-09-06 全量复测已完成：536 项全部处理，408 项取得合格/可复核图片（406 正式、2 预览），81 项缺件跳过、26 项占位、21 项未取得合格结果。经用户明确授权，406 张正式图片完成真实 AI 审核，347 张通过、59 张转人工复查、0 次调用失败。审核子进程平均 2.836 秒/张，累计约 19.2 分钟（含本地复核及进程开销）。85.47% 是通过率，不是准确率。源文件哈希核验通过，定时任务已删除。随后已将 347 张批准图片按原模板出版为初步成果：96 个主工序、120 页，保留多图排版与续页；不代表全部任务完成或工艺已签审，材料及成品工作簿不上传代码仓库。

## 交付与仓库边界

本机 BOM、CAD、许可证、运行数据库、日志、图片、检查点、构建目录和安装包均由 `.gitignore` 排除。最终交付目录只能包含：

```text
交付结果/
├─ SOP.xlsx
└─ 步骤图片/
```

## 核心约束

- BOM 是工艺事实来源；锁定的最终总装是几何、位置和坐标事实来源。
- occurrence 使用从根总装开始的完整路径，不使用裸特征号。
- 爆炸只允许沿有 Creo 证据的方向纯平移。
- 后续零件不得为了画面提前显示。
- 初次生成只使用 `fixed_123` / `fixed_456` 中的一台；人工二次生成可明确选择“换另一个固定视角”，但不得创建第三个相机。
- AI 只在结构硬门全部通过后判断图片可读性；AI 通过可自动纳入 SOP，AI 未通过或不可用时保留人工采用、二次生成与跳过能力。
- 二次生成只接受有真实脚本映射的结构化选项，自由备注不参与渲染任务改写。
- 源 CAD 运行前后哈希必须一致。
- 相同输入、配置和 Skill 版本必须得到相同计划、相机选择与执行指纹。

详细规范：

- [纯 BOM + Creo 产品契约](docs/deterministic-creo-sop-contract.md)
- [运行状态与步骤隔离 ADR](docs/adr/0001-durable-run-state-and-step-isolation.md)
- [正式渲染规则](rules/render-rules.md)
- [合同与硬门](rules/contracts.md)
- [原生箭头与可迁移推导](docs/arrow-generation-and-portability.md)
- [SOP 出版规则](docs/spreadsheet-sop-publication.md)


## 新版资料全量重建

`python -m sop_pipeline.agent.process_cli rebuild --product data/products/cdu900-left/product.json --graph work/process-mvp-20261008/cad-graph.json --output outputs/重构阶段成果-20261008`

省略 `--graph` 执行新扫描。复用图谱前校验全部 CAD 哈希，重新解析工艺卡和标准；生成独立分析目录、全量绑定候选、阻断记录和可搜索评审页面。当前评审入口：`outputs/重构阶段成果-20261008/阶段评审.html`。
