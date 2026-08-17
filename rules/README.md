# Repository rules

这里保存 Agent 与 12 个可执行 Skill 共同遵守的规则，不是额外的执行 Skill。

- `contracts.md`：Artifact、状态、重试和交付合同。
- `render-rules.md`：Creo 几何、可见集、相机、箭头和图片硬门。
- `qwen-dev-principles.md`：去 GPT/Codex、Qwen 权限边界和可迁移开发约束。

正式运行从 BOM 与 CAD 目录创建 `run_id`，不使用 `product.json`。调用方不得指定任意输出路径、绕过 `SkillRuntime`、跳过确认状态或让 Qwen 修改 CAD 事实。
