# Creo 原生自适应取景

## 当前正式方案

正式流水线只使用 Creo 原生 **Zoom to Selected**。它根据所选对象的三维包围范围自动缩放并居中，不依赖屏幕坐标、绝对 PAN/ZOOM 或探针图片。

每个安装步骤按固定顺序执行：

1. 应用 `fixed_123` 或 `fixed_456` 固定方向；
2. 激活精确的阶段可见集并完成纯平移爆炸；
3. 将全部 moving occurrence 与 receiver occurrence 的完整 ComponentPath 加入 Selection Buffer；
4. 用 `UIGetCommand("ProCmdZoomIntoOutline")` 验证命令存在；
5. 设置本步 `zoom_to_selected_level`，通过 `RunMacro` 执行一次 `ProCmdZoomIntoOutline`；
6. 清空 Selection Buffer，导出一张正式原始图；
7. 执行固定中心 1600×1600 裁切和最终栅格硬门。

PTC 官方说明：

- [Zoom to Selected 操作](https://support.ptc.com/help/creo/creo_pma/r12/usascii/fundamentals/fundamentals/To_Zoom_on_the_Model.html)
- [Programmatic Selection](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/user_guide/Programmatic_Selection.html)
- [SelectionBuffer API](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/api/dita/c-pfcSelect-SelectionBuffer.html)
- [Finding Creo Parametric Commands](https://support.ptc.com/help/creo_toolkit/otk_java_pma/r13/usascii/creo_toolkit/user_guide/Finding_Creo_Parametric_Creo_Commands.html)
- [`zoom_to_selected_level`](https://support.ptc.com/help/creo/creo_pma/r13/usascii/detail/detail_configuration_options.html)

## 自适应留白

Creo 先按“爆炸态移动件＋接收件”的实际选择包围盒完成自适应取景，再应用一个固定的相对留白：

```text
zoom_to_selected_level = 0.75
level_policy = fixed_native_selection_margin/v1
```

尺寸自适应由 Creo 对联合选择包围盒的原生 fit 完成；`0.75` 只控制 fit 之后的统一相对留白。该值由真实第一步 `0.42 → 0.31` 的最终主体跨度按比例校准，使预期跨度约为 `0.55`。不再根据 moving/receiver 比例二次缩放，避免接收件尺寸或爆炸距离被重复计入。接收件仍直接参与原生选择和中心计算。

## 时长与重试边界

- 一次渲染尝试只允许一条原生取景命令和一张正式栅格；
- 不生成探针帧、候选取景帧或自动构图修正帧；
- Creo 超时、进程故障等系统错误可由调度器有界重试，每次新尝试重新获得一张栅格预算；
- 构图硬门失败保留真实图片进入复核，不在同一次尝试中重复取景。

## 验收与审计

Creo 的原生居中不能替代最终图片验证。1600×1600 成图仍必须通过主体/安装活动中心、接收件可见、箭头边距、裁边、尺寸和权威装配硬门。

每张成功导出的正式图写入 `native-framing-audit/v1`，至少记录：

- Creo 版本/datecode；
- `ProCmdZoomIntoOutline` 已在当前会话验证并成功执行；
- `moving_and_receiver_occurrences/v1` 选择范围；
- 实际 `zoom_to_selected_level`；
- 单命令限制与绝对 PAN/ZOOM 禁止标记。

## 已移除方案

屏幕响应探针、多帧构图搜索、PAN/ZOOM 恢复缓存、临时隐藏非焦点部件后 Refit、像素箭头和几何检测动态裁切均已从正式代码与合同删除，不能作为回退入口。
