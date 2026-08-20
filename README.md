# Qwen Creo SOP Agent

Windows 桌面 Agent：用户只需选择 BOM 和 Creo CAD 文件夹、确认一次理解结果，即可生成安装步骤图片与可编辑 SOP。

正式调用链只有一条：

```text
GUI
  → AgentCore
  → PipelineOrchestrator
  → SkillRuntime
  → 12 个可执行 Skill
  → Creo / Qwen / Excel Adapter
```

GUI 只是 Agent 的界面，不包含 Creo、Qwen 或 Excel 的业务逻辑。运行状态、Artifact、执行指纹和检查点写入 SQLite；单个步骤出图失败不会中断无关步骤。

## 当前能力

- 自动识别 BOM 工作表、表头、层级、数量、工艺与控制文字。
- 自动锁定最终总装版本与 SHA-256，并通过 J-Link 扫描完整 occurrence 和约束。
- 生成依赖图、完整安装态和 `formal-render-plan/v2`。
- 按共享 CAD 接口执行密封件→封闭件→保持件的物理顺序，并将跨安装层密封件拆成独立步骤组。
- 在生成前统一展示一次确认页；用户不确定时可采用推荐方案。
- 由 Creo/J-Link 原生 `DisplayList3D` 绘制同 CAD 锚点箭头，不使用后期像素箭头。
- 使用 `fixed_123`、`fixed_456` 两个确定性视角；当前正式路径仅允许 Creo 原生 `Zoom to Selected`，每任务一张正式帧，不接受外部 PAN/ZOOM。
- 一个常驻 Creo Worker 复用模型副本，逐步骤保存检查点，默认 20 个任务后重启。
- 硬门校验、有限修复、候选图、局部释疑重跑和动态 SOP 出版。
- Qwen 仅处理语义理解、受限推荐和图片语义复核；不能改变 CAD 事实、状态机或输出路径。

已通过 Fake Adapter 全链路和真实 Creo 单步骤 Agent/Skill 冒烟。42 步全量、10 次一致性和新设备清洁迁移仍属于后续验收项，不在 README 中宣称完成。

## 12 个可执行 Skill

```text
分析：intake-preflight → normalize-bom → lock-assembly → discover-cad
    → map-bom-cad → plan-assembly → clarify-plan

生成：compile-render-jobs → render-batch → validate-repair
    → publish-delivery

释疑：resolve-step → 最小失效范围 → 必要步骤重跑 → 重新出版
```

每个 Skill 都包含 `SKILL.md`、版本化定义、Handler、输入输出 Artifact、稳定错误码和独立调用入口。聚合说明位于 `skills/generate-creo-assembly-sop/`，不作为第 13 个执行步骤。

## 本机配置

复制 Creo 配置示例，但不要提交真实路径或许可证：

```powershell
Copy-Item ./config/creo-runtime.example.json ./config/creo-runtime.json
$env:DASHSCOPE_API_KEY = '<your-key>'
```

桌面 Agent 会在首次分析时把用户在界面中输入的 DashScope Key 使用 Windows DPAPI
按当前用户加密保存；后续启动可将 Key 输入框留空。部署环境变量中的 Key 只在当前进程
使用，不会被应用自行复制到本地设置。输入新 Key 会替换已保存值，配置中不保存明文。

正式 Qwen 接入使用阿里云 DashScope Python SDK，不依赖 OpenAI、GPT 或 Codex 运行时。`pywin32` 是 Windows COM 绑定的包名；64 位 Python 会安装 64 位扩展，可在 64 位 Windows 上驱动 Excel。

## 开发入口

安装并启动桌面 Agent：

```powershell
python -m pip install -e .
qwen-creo-sop-agent
```

在已有运行中独立调试 Skill：

```powershell
qwen-creo-sop-skill `
  --workspace <Agent工作区> `
  --run-id <运行标识> `
  --skill normalize-bom `
  --input-ref analysis/input-manifest.json
```

运行确定性测试：

```powershell
python -m pytest -q
```

从 Agent 入口执行真实 Creo 小批量冒烟：

```powershell
python ./scripts/smoke_agent_single_step.py `
  --bom ./BOM.xlsx `
  --cad ./零件图 `
  --step-count 3
```

该脚本只用于开发验收；正式用户通过 GUI 运行。

## 仓库与本地产物边界

仓库只提交代码、Skill、规则、Schema、示例配置、测试和开发文档。下列内容只保留在本机并由 `.gitignore` 排除：

- BOM、CAD、SOP 示例和许可证；
- `data/`、`work/`、`output/`、`outputs/`、`delivery/`；
- Agent SQLite、日志、图片、候选、检查点和临时模型副本；
- Java/PyInstaller 构建目录和最终安装包。

最终用户交付目录只能包含：

```text
交付结果/
├─ SOP.xlsx              # 有疑问时为 SOP_待确认.xlsx
└─ 步骤图片/
```

## 目录

| 目录 | 内容 |
| --- | --- |
| `src/sop_pipeline/agent/` | AgentCore、状态机、SkillRuntime、规划、渲染、验证、出版 |
| `src/sop_pipeline/desktop/` | PySide6 GUI 与后台进程边界 |
| `skills/` | 12 个执行 Skill 与聚合流程说明 |
| `creo_java/` | 产品无关的 J-Link 扫描、原生箭头和常驻 Worker |
| `rules/` | Qwen 边界、渲染硬门和统一合同 |
| `docs/` | 产品契约、架构决策与可迁移推导机制 |
| `packaging/` | PyInstaller 构建入口 |
| `tests/` | Fake Adapter、状态机、恢复、规模和确定性测试 |

## 核心约束

- 最终总装是唯一几何、位置、坐标和相机事实来源。
- occurrence 使用从根总装开始的完整路径，不能使用裸特征号。
- 爆炸只允许沿有 CAD 证据的接收方向纯平移。
- 后续零件不得为了画面好看提前显示。
- 相机只允许固定双视角；PAN、Zoom 和箭头不得由 Qwen 猜测。
- 源 CAD 运行前后哈希必须一致。
- 相同输入和 Skill 版本产生相同执行指纹，成功 Artifact 可复用。

详细规范见：

- [产品契约](docs/qwen-agent-product-contract.md)
- [实施计划](docs/qwen-agent-implementation-plan.md)
- [运行状态与步骤隔离 ADR](docs/adr/0001-durable-run-state-and-step-isolation.md)
- [安装图规划规则](docs/render-planning-rules.md)
- [对话与旧水箱成果迁移审计](docs/rule-migration-audit.md)
- [原生箭头与可迁移推导](docs/arrow-generation-and-portability.md)
- [Zoom 推导机制](docs/zoom-derivation-and-portability.md)
- [SOP 出版规则](docs/spreadsheet-sop-publication.md)
