---
name: publish-delivery
description: Publish validated or clearly pending Creo SOP steps into a dynamically paginated XLSX and strict two-item delivery directory.
---

# Publish delivery

Invoke `publish-delivery` through `SkillRuntime` with normalized BOM, locked plan, validation and candidate artifacts. It emits `publication-result/v1`.

Group by main process and add continuation pages without fixed counts. Use the built-in template.
Publish only approved or visibly pending images. Verify workbook structure and Excel print output.
Keep only the SOP workbook and `步骤图片` in the user delivery directory.
