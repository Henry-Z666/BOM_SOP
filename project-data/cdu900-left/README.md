# CDU900 关键中间数据

本目录跟踪本次项目的关键解析、规划和出版输入，不包含原始 BOM/CAD、原工艺卡、原生图片或成品 SOP。

- `analysis/出版排序内容.json`：366 道原子工序的排序及出版输入。
- `analysis/部件式出版内容.json`：按部件分组、选图及页面编排数据。
- `analysis/process-package.json`：解析后的工艺资料与参数。
- `analysis/工艺工具来源.json`：工具、力矩等要求的来源。
- `analysis/工艺图像索引.json`、`出图检查.json`：图像引用、检查及哈希，不含图片文件。
- `analysis/新总装图谱.json`：总装结构解析快照。
- `analysis/BOM完整覆盖检查.json`：覆盖范围核对。
- `analysis/confirmed-native-bindings.tsv`：已确认的模型替代绑定。
- `product.json`：产品文件选择配置，不包含原文件。
- `bundle-manifest.json`：历史路径映射及哈希清单。清单也列出本地原料和图片，但这些实体文件不上传。

运行出版时，需自行在本地补齐相应原料和图片。可通过 `CREO_SOP_PUBLICATION_INPUT` 指定资料目录；仅克隆仓库可以查看规划数据，不能直接重新出图或完整出版。
