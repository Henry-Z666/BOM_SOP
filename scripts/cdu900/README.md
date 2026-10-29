# CDU900 部件式出版配方

这些文件是 2026-10-15 部件版交付时实际使用的出版逻辑，已从本地 `work/` 整理到可跟踪目录。它们是 CDU900 左机的产品配方，包含该机型的分组和选图；通用规则见 [按部件出版](../../rules/component-based-publication.md)。

## 输入

输入由本地 `CREO_SOP_PUBLICATION_INPUT` 指定，默认 `project-data/cdu900-left/`（已忽略）。目录中的 `analysis/` 放排序、出图检查、工具和工艺解析 JSON；可选 `bundle-manifest.json` 提供旧路径到本地文件的映射。没有映射时保留输入中的原路径。

原料、图片、项目专属解析数据和最终 SOP 均由使用者在本地准备，不随仓库上传。`出版排序内容.json` 是某次运行的数据，不是通用工艺规则；通用逻辑由这些脚本及 `rules/` 维护。

## 顺序

在仓库根目录运行，Python 需要项目依赖；Node 需要 Codex 提供的 `@oai/artifact-tool` 运行时。不要提交本机依赖目录或许可证配置。

```powershell
python scripts/cdu900/plan_component_publication.py
node scripts/cdu900/build-component-sop.mjs outputs/部件式装配SOP-20261015
python scripts/cdu900/finish-workbook.py outputs/部件式装配SOP-20261015
python scripts/cdu900/check_component_cards.py
```

可设置 `CREO_SOP_PUBLICATION_OUT` 环境变量，将规划及核对输出放到另一目录；调用 Node 构建器和收尾脚本时传入同一目录。

先由规划器保留本版物料数量并生成部件页；构建器使用仓库内实际模板出版 Excel；保存后核对原图字节、表格和打印范围；最后核对颜色边界及范围。

只调整少数页面时，可用同一构建器的 `--update-pages=43,44` 更新已有工作簿，避免重建所有工作表。`--preview=1,9` 只出预览，不导出成品。

`finish_component_delivery.py` 用于整理交付记录和预览 HTML。仅在实际看完全部页面后使用 `--reviewed` 写入人工版式检查记录；脚本不会代替读图。

资源位于 `assets/publication/`：实际模板、尺寸与合并单元格元数据、来自用户参考工艺卡的品牌图片。构建器只用 artifact-tool 写入工作簿，收尾脚本仅补充其缺少的打印 OOXML 并用 openpyxl 读取校验。
