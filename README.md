# Creo SOP Agent

Windows 桌面工具：只使用 Excel BOM、Creo ASM/PRT 模型和本地确定性规则，生成安装步骤图片与可编辑 SOP。运行时不需要云模型、API Key 或网络语义服务。

正式调用链：

```text
GUI → AgentCore → PipelineOrchestrator → SkillRuntime
    → Creo/J-Link → 确定性硬门 → Excel 出版
```

## 当前能力

- 识别 BOM 工作表、表头、层级、数量和工艺文字。
- 锁定最终总装版本与 SHA-256，扫描完整 occurrence、约束和根坐标系几何。
- 从 BOM 与 Creo 图谱生成依赖关系、完整安装态、分阶段可见集和纯平移爆炸合同。
- 使用根坐标系内预定义的 `fixed_123`、`fixed_456`，按接收面法向与爆炸投影稳定选择一次。
- 每个步骤只编译一个正式相机变体；渲染后不切视角、不 PAN/Zoom、不因构图告警二次取景。
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

正式运行只依赖本地 Creo/J-Link、Excel 和 Python 组件。`pywin32` 用于 64 位 Windows 上的 Excel COM 出版。

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
- 两台固定相机只在渲染前确定性排序一次；图片审查不能改相机或触发替代视角。
- 源 CAD 运行前后哈希必须一致。
- 相同输入、配置和 Skill 版本必须得到相同计划、相机选择与执行指纹。

详细规范：

- [纯 BOM + Creo 产品契约](docs/deterministic-creo-sop-contract.md)
- [运行状态与步骤隔离 ADR](docs/adr/0001-durable-run-state-and-step-isolation.md)
- [正式渲染规则](rules/render-rules.md)
- [合同与硬门](rules/contracts.md)
- [原生箭头与可迁移推导](docs/arrow-generation-and-portability.md)
- [SOP 出版规则](docs/spreadsheet-sop-publication.md)
