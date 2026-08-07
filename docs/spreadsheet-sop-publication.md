# Spreadsheet SOP 出版

`products/water-tank/scripts/publish_sop_spreadsheets.mjs` 使用
`@oai/artifact-tool` 将已通过安装图校验的 PNG 放入“一主工序一工作表”的
Excel 模板。它只处理出版：不规划步骤、不运行 Creo，也不修正未通过的图片。

## 输入

- 一份已经预排好工作表、BOM/工装/控制要点文字的 SOP 模板；
- `creo-render-jobs/v3` JSON；
- 每个 `job_id` 对应一张通过校验的 PNG。

水箱当前模板含 8 个工作表，对应 42 个安装子步骤。图像布局是每张主工序表内的
三列方形图片区，保持“主工序一页”，而不是“一张图片一页”。

## 运行

```powershell
node ./products/water-tank/scripts/publish_sop_spreadsheets.mjs `
  --reference ./data/runs/reference_template.xlsx `
  --jobs ./data/runs/corrected-v2-render-jobs.json `
  --images ./data/runs/published-sop-build/png `
  --output ./outputs/published_sop/water-tank-spreadsheets-trial.xlsx
```

脚本在写图前渲染模板文字区，并在写图后确认每张工作表的浮动图片数量。然后使用
`validate_published_sop.py` 对导出的 `.xlsx` 复核工作表顺序、文件编号、工序名称、
公式错误和 42 张嵌入图片。

## 已知边界

当前 artifact-tool 的工作簿预览不能完整显示“从既有 xlsx 导入后新增的浮动图片”。
因此 QA 采用两段式：模板文字区进行渲染检查；导出工作簿对图片 drawing 和 Excel
压缩包中的 media 关系进行结构检查。最终视觉放行仍应在 Excel/LibreOffice 中抽查。

水箱模板的 `AN5` 使用 Excel 的 `CELL("filename")` 取得工作表名称。该公式在 Excel
有效，但 artifact-tool 目前显示为 `#NAME?`；发布器只对白名单中的这一格跳过运行时
公式误报，其他公式错误仍会阻断出版。

这是 Codex/spreadsheets 的可选出版适配器，不是可移植正式发布器。若目标是在没有
Codex 的新电脑上批量出 SOP，应另行提供不依赖 `@oai/artifact-tool` 的出版实现。
