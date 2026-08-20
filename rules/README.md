# Repository rules

这里保存 Agent 与 12 个可执行 Skill 共同遵守的规则，不是额外的执行 Skill。

- `contracts.md`：Artifact、状态、重试和交付合同。
- `render-rules.md`：Creo 几何、可见集、相机、箭头和图片硬门。
- `deterministic-dev-principles.md`：纯 BOM + Creo 的事实来源与可迁移开发约束。

正式运行从 BOM 与 CAD 目录创建 `run_id`，不使用 `product.json`。调用方不得指定任意输出路径、绕过 `SkillRuntime`、跳过确认状态或用自由文本修改 CAD 事实。
