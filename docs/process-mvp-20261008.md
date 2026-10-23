# 工艺操作架构与力矩标注：第一阶段成果

## 已确认交付范围

第一阶段实现机械装配的最小架构闭环：资料解析 → 工艺操作 → 确认与依赖 → CAD 几何计划 → 原生标注渲染 → 审核出版。型号和参数差异保留待确认，不自动选用某个数值；标注采用气泡文字与引线，并检查清晰度和遮挡。接线、检漏、调试、包装的正式执行暂不扩展。

## 实现边界

`process_model.py` 定义带来源的工艺操作、力矩陈述和连接确认，不包含 Excel 坐标或 Creo API。`process_sources.py` 的 QSL 适配器负责读取单元格及 DrawingML，并将图片和未解析页面留在核验清单。新格式可以增加适配器，复用同一工艺模型。

`process_bridge.py` 将确认后的操作关联到现有几何步骤，增加明确前置关系，保留原始 CAD 依赖，并重新计算步骤状态、可见集和相机相关几何。总装/分装范围必须与 CAD 装配作用域一致。每个几何步骤须有唯一工艺归属；未绑定、重复绑定、依赖循环或范围不一致都会阻断。

力矩保留原始单位和容差。选择具体参数必须记录确认人、原因、连接标识、完整实例路径，并承认同操作中的竞争参数。未确认或覆盖不完整的参数不会成为正式生产数值。通用标准中的参数可作为候选加入操作，仍需确认适用条件。

`NativeTorqueAnnotations.java` 通过 Creo 原生 `DisplayList3D` 绘制面向相机的矢量文字、气泡边框和引线。目标点由实际移动实体表面取得；布局避让投影几何、已有文字、引线和安装箭头。缺少空间时明确失败，不修改相机、不覆盖 JPEG。

每张带标注图片都生成独立审计，检查实例、文字、连接和合同哈希。第一阶段仍要求人工核对最终图片的指向、裁切、遮挡与清晰度；几何投影避让不等于完整深度验证。

出版时图片中的参数与控制要点共用同一数据。待确认参数只能进入 `SOP_待确认.xlsx`；正式 `SOP.xlsx` 的出版器会阻止待确认参数。现有桌面流程可通过 `PipelineOrchestrator` 的工艺适配器接入，第一阶段提供独立 CLI；尚未为 GUI 增加工艺资料选择和参数确认表单。

## 新版资料上的实际结果

- 4 个工作簿、173 个工作表已纳入资料清单；解析包保留 26,239 个非空单元格事实。
- QSL 适配器得到 135 个有操作文字的页面候选，其中有 54 条明确带单位的力矩陈述。它们是候选，不是 135 个已批准生产步骤。初期粗提取还包含物料/工具表文字，现已限制到操作区域，避免这些表格被当成指令。
- 373 个嵌入媒体条目保留原文件哈希和核验状态；图片中的参数尚未自动转成生产规则。未提取到操作的工艺卡页面也显式保留在 `sheet_review`。
- 新 BOM 的工序 G、工位 H、CAD 名称 J 均被保留。J 的多行候选在文件扫描和 Creo 实例映射中保持分离，沿用图号优先的精确匹配。
- 产品配置显式指定第 2 行为产品根行，保留原始空层次，并建立计算用父层次。新版 927 行均归属于 `cdu900-1250l-s-d7l.asm.3` 的单个根范围，未产生游离行。该结果是材料层级与文件名核验，不是原生几何验收。

本机配置在 `data/products/cdu900-left/product.json`；解析包、待确认清单与预检记录在 `work/process-mvp-20261008/`。原始资料没有修改。

## 使用入口

安装项目后使用 `creo-sop-process`，或从项目根目录设置 `PYTHONPATH=src` 后使用 `python -m sop_pipeline.agent.process_cli`。原生阶段需将 `CREO_SOP_RUNTIME_CONFIG` 设为本机 `config/creo-runtime.json` 的绝对路径。各阶段支持 `--help`。

```powershell
# 仅提取资料，生成 process-package.json 和资料待确认清单.md
python -m sop_pipeline.agent.process_cli ingest --product data/products/cdu900-left/product.json --output work/process-mvp-20261008

# 原生 CAD 分析，输出 geometry-plan.json、scope-requests.json 与绑定候选
python -m sop_pipeline.agent.process_cli analyze --product data/products/cdu900-left/product.json --output work/process-mvp-20261008

# 根据 scope-requests 中的 stable ID 明确选择分装展开或整体安装
python -m sop_pipeline.agent.process_cli lock --geometry work/process-mvp-20261008/geometry-plan.json --scope-decisions work/process-mvp-20261008/scope-decisions.json --output work/process-mvp-20261008/locked-geometry.json

# 确认工艺适用性、实例、前置关系与力矩后编译
python -m sop_pipeline.agent.process_cli compile --package work/process-mvp-20261008/process-package.json --geometry work/process-mvp-20261008/locked-geometry.json --decisions work/process-mvp-20261008/decisions.json --output work/process-mvp-20261008/render-jobs.json

# 只操作隔离副本；实际 CAD 目录须与编译时的整套文件锁一致
python -m sop_pipeline.agent.process_cli render --jobs work/process-mvp-20261008/render-jobs.json --models AI生成SOP外发资料/cdu900模型cad资料 --output work/process-mvp-20261008/native

# 审核记录绑定图片哈希；缺少批准则生成待确认工作簿
python -m sop_pipeline.agent.process_cli publish --jobs work/process-mvp-20261008/render-jobs.json --results work/process-mvp-20261008/native/render-results.json --reviews work/process-mvp-20261008/reviews.json --output outputs/process-mvp
```

`decisions.json` 必须包含资料包 `source_package_fingerprint`、锁定几何计划 `geometry_fingerprint` 和 `bindings`。每条绑定包含 `operation_id`、`step_ids`、`scope`（`main_assembly` 或 `subassembly`）、`confirmed_by`、`applicability_reason`、`torque_required`。不需要力矩时必须填写 `no_torque_reason`；需要力矩时填写 `torque_decisions`，其中包含 `connection_id`、`claim_id`、`occurrence_ids`、确认人与原因，以及完整的 `acknowledged_claim_ids`。

跨操作依赖可放在 `operation_dependencies`；通用标准候选可通过 `additional_torque_claims` 从资料包的 `parameter_catalog` 引入。这些配置不能伪造源文本或改变源参数；编译会重新读取原件验证。`reviews.json` 使用 `steps` 数组，每项包含 `step_id`、`approved`、`image_hash` 和 `confirmed_by`。人工审核不能越过实例、标注覆盖或资料哈希硬门。

## 验证及剩余阻塞

新增测试覆盖文本框关系解析、来源伪造、单位/容差、参数冲突、依赖重排、循环、总装/分装范围、唯一绑定、实例覆盖、出版阻断、根行配置和 CLI 编译。Java 布局测试覆盖文字避让、标签分离、引线交叉和无空间失败，原生 Java 类已编译。

全量回归已通过 283 项测试；随后增加了工艺交错顺序的出版测试，工艺模块 14 项测试通过，并复查了出版器。回归所需 Qt 库来自本机已有依赖，未重新下载。新版 538 个源文件的 SHA-256 再次核验均未改变。

2026-10-08 在用户恢复许可后再次执行原生扫描：Host ID 绑定检查已通过，但 Creo 实际申请 `CREOPMA_StudentP6` 时仍返回 `Invalid (inconsistent) license key (-8)`，J-Link 返回 `XToolkitNoLicense`。项目和安装启动配置指向同一许可文件。Host ID 检查仅验证绑定关系，不验证许可签名或实际功能授权；预检 JSON 已明确标识此范围。实机结果保存在 `work/process-mvp-20261008/native-startup-verification.json`，对应隔离目录保留 `discovery.log` 和 `models/std.out`。本次失败扫描的启动进程已核实归属并清理。

同时修复了扫描程序遇到明确无许可错误后仍等待外层进程的问题，新增回归并通过原生发现模块全部 14 项测试。尚未产生新版原生 CAD 图谱、真实力矩标注图或正式生产 SOP。需要确认恢复后的有效许可入口，再执行实机分析、样例绑定、标注图检查和工作簿视觉验收，才能完成机械装配闭环验收。

原生显示列表接口依据 [PTC Display API](https://support.ptc.com/help/creo_toolkit/otk_java_plus/r11.0/usascii/creo_toolkit/api/dita/c-pfcDisplay-Display.html)；实际调用与当前本机异步 J-Link 库编译核验一致。


## 最新实机验证：许可恢复后的结果

Creo 安装配置中的许可文件已更换（新文件名前有空格），项目运行时路径已同步。主机绑定、异步 J-Link 连接及精确版本总装加载成功。原生扫描完成，读取 200 个顶层组件、2,111 个实例及 4,513 条约束。扫描副本与 532 个源 CAD 文件逐文件哈希核验一致。

真实扫描暴露出 10 条约束端点重复父路径的问题；现仅依据同条原生参考中已解析的有效根实例修正，并记录 constraint_endpoint_corrections，原始图谱保留。无原生证据的未知引用仍被拒绝。扫描模块 15 项回归测试通过。

规范图谱和 BOM 映射已经输出到 cad-graph.json、cad-mapping.json。后续几何规划发现补水管部件范围（BOM 第 900–922 行）存在依赖循环，已保存 dependency-cycle-diagnostics.json；未删除约束或强行发布。新版 CAD 加载同时存在缺失模型及再生失败记录。尚未编译正式渲染任务，未生成安装图或真实力矩标注图。最新状态以 native-startup-verification.json 为准。


## 全量重构交付入口

新版资料、完整 CAD 实例图、BOM 映射、工艺操作候选、标准参数库和范围请求现可一次重建。入口：`python -m sop_pipeline.agent.process_cli rebuild --product data/products/cdu900-left/product.json --graph work/process-mvp-20261008/cad-graph.json --output outputs/重构阶段成果-20261008`。省略 graph 则重新执行原生扫描；复用扫描必须核验全部 CAD 来源哈希。每次生成独立分析目录，防止旧成功计划混入新阻断结果。

结果入口为 `outputs/重构阶段成果-20261008/阶段评审.html`；重建清单位于 rebuild-manifest.json。依赖循环仍保留 651 个完整步骤草案及绑定候选，8 个阻断步骤单独标识。补水分装范围尚待明确。

已成功生成新版总装的真实原生安装技术验证图，修复小零件沿用总装爆炸距离的问题；许可证和出图链路已恢复。该图仅用于几何和显示链路验证，不包含批准的力矩参数，不是正式 SOP。原生力矩气泡和引线代码及避让测试已实现，真实参数绑定及标注图视觉验收仍待完成。历史无许可记录保留为历史证据，不能作为当前状态。


## 两张新版模型技术图

安全阀与压力传感器两张原生图已生成，并针对零件偏小进行了有界重跑。样例缩短展示平移、原生选择取景占比由 0.70 调整至 0.85；主体占比检查仍未通过，压力传感器箭头存在遮挡，不能作为验收通过图。

增加 `process_cli preview --analysis <分析目录> --step <步骤ID> --step <步骤ID> --output <任务文件>`。入口逐文件核验新模型、工艺来源，拒绝阻断步骤，保留原工艺依赖并标为 diagnostic_preview、production_eligible=false、parameter_status=pending。紧凑爆炸仅用于技术样例，正式规划仍保留原有接触避让判定。


## 连续局部支路闭环验证（2026-10-08）

新增 `process_cli sequence-preview --analysis <分析目录> --selection <选择文件> --output <任务文件>`。选择文件明确预装接口、前置原因、来源步骤及实例子集。入口核对新资料哈希，拒绝未就绪接收件、重复安装与阻断步骤，按顺序保留已安装零件，并保留原计划依赖供审核；始终标记 production_eligible=false。

已完成管路1分装3的单条压力检测支路：密封圈、球阀、压力传感器三个连续安装步骤，另有原始装配位置的完成状态互检图。球阀原卡15 N·m独立标注，压力传感器连接参数待确认。输出为 `outputs/连续工序SOP验证稿-20261008/成果评审.html`、四页Excel和PDF验证稿，不代表整张工艺卡或整机完成。

原生批次保留12/15列旧清单兼容，16列增加安装/完成状态区分；完成总览强制零平移、无安装箭头、全范围取景，产生独立完成状态审计。SOP工具规格数量可由tool_details带入，长物料规格自动换行并增高对应行。

## 单工序闭环验证

### 总装复用恢复（2026-10-09）

全量测试暴露出持久 Creo 进程仍逐图关闭窗口、EraseUndisplayedModels 的效率回退。原生渲染器现保留同一隔离副本的总装和窗口，按图捕获并恢复所有移动实例的完整位姿，逐项校验恢复矩阵；重新建立精确显示范围、固定相机，删除箭头和力矩显示列表。静态辅助内容按模型清理一次，每张图仍核对可见模型存在性。失败或清理不完整时丢弃缓存并重新加载，换总装、换会话及独立可见性场景也会清理缓存。

连续三张支路实机图通过：首次加载27.24秒，后续加载0.010/0.015秒；辅助隐藏8.14秒降至1.15/1.24秒。每张图的位姿恢复检查通过，CAD箭头审计与旧图在1e-6数值容差内一致。记录位于 `work/reuse-assembly-20261009/复用验证.json`；37项原生工作器和渲染验收测试通过。全量队列从既有断点切换，不重复已完成图片。

输出 `outputs/单工序闭环-20261008/工作簿/SOP_待确认.xlsx` 和单页 PDF。新版管路1分装-1卡箍连接 10 N·m 原生标注已完成，图片结构检查通过，保留人工图面审核。

`OperationBinding.torque_target_occurrences` 可明确同一工序中需要力矩的连接集合，其他步骤为 not_applicable；目标越界、非目标标注和漏标仍被拒绝。省略该字段时保留既有整工序覆盖检查。

原生字体连通笔画并增大显示尺寸；取景增加有限修复档位 1.45、1.50，按视口比例换算后的实际档位上限为 0.95。固定相机、原生锚点、无裁切和来源验收门不变。
