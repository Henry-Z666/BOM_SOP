# Creo 原生自适应取景

## 当前正式方案

正式流水线使用 Creo 原生 **Zoom to Selected** 同时框选活动件与约束所指向的接收曲面。它不依赖屏幕识别、人工 PAN/ZOOM、坐标换算或探针图片。

每个安装步骤按固定顺序执行：

1. 应用 `fixed_123` 或 `fixed_456` 固定方向；
2. 激活精确的阶段可见集并完成纯平移爆炸；
3. 将全部 moving occurrence 的完整 ComponentPath，以及每条安装约束对应的接收 `SURFACE`（接收 occurrence path + Creo surface ID）加入 Selection Buffer；
4. 用 `UIGetCommand("ProCmdZoomIntoOutline")` 验证命令存在；
5. 设置本步 `zoom_to_selected_level`，通过 `RunMacro` 执行一次 `ProCmdZoomIntoOutline`；Creo 直接以活动件和具体接收曲面共同确定中心与范围；
6. 不执行任何后续 PAN/ZOOM 坐标修正；
7. 清空 Selection Buffer，导出一张正式原始图；
8. 由 Creo 按当前原生图形区比例超采样输出（短边 1600），再执行一次固定中心正方形裁切，得到 1600×1600；禁止补白、拉伸、放大裁切或按图片内容动态选裁切框。

PTC 官方说明：

- [Zoom to Selected 操作](https://support.ptc.com/help/creo/creo_pma/r12/usascii/fundamentals/fundamentals/To_Zoom_on_the_Model.html)
- [Programmatic Selection](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/user_guide/Programmatic_Selection.html)
- [SelectionBuffer API](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/api/dita/c-pfcSelect-SelectionBuffer.html)
- [Finding Creo Parametric Commands](https://support.ptc.com/help/creo_toolkit/otk_java_pma/r13/usascii/creo_toolkit/user_guide/Finding_Creo_Parametric_Creo_Commands.html)
- [`zoom_to_selected_level`](https://support.ptc.com/help/creo/creo_pma/r13/usascii/detail/detail_configuration_options.html)

## 自适应留白

Creo 对“爆炸态活动件几何＋完整态接收曲面”一次性完成原生取景，并应用固定相对留白：

```text
zoom_to_selected_level = 0.70
level_policy = fixed_native_selection_margin/v1
```

初始值 `0.70` 提供更大的统一相对留白，降低活动件或接收部位贴近画面边缘的风险。`0.80` 和 `0.85` 仅用于兼容已持久化任务，`0.95` 仅用于有界的“聚焦安装区域”二次生成。完整接收 occurrence 继续显示，但不作为选择对象；只有约束明确指向的接收曲面进入原生框选。因此，当安装点位于大主体表面时，主体尺寸不会压低活动区的放大倍数，多个活动件与各自接收部位也会共同进入画面。该规则按约束引用生效，不按零件名称、BOM 行或接收件尺寸套用经验比例。

## 时长与重试边界

- 一次渲染尝试只允许一条原生取景命令和一张正式栅格；
- 不生成探针帧、候选取景帧或自动构图修正帧；
- Creo 超时、进程故障等系统错误可由调度器有界重试，每次新尝试重新获得一张栅格预算；
- 构图硬门失败保留真实图片进入复核，不在同一次尝试中重复取景。

## 验收与审计

Creo 的原生居中不能替代最终图片验证。正方形成图仍必须通过主体/安装活动中心、接收件可见、逐审计箭头栅格数量、箭头边距、裁边、尺寸和权威装配硬门。

每张成功导出的正式图写入 `native-framing-audit/v1`，至少记录：

- Creo 版本/datecode；
- `ProCmdZoomIntoOutline` 已在当前会话验证并成功执行；
- `moving_occurrences_and_receiver_surfaces/v1` 选择范围；
- `none_native_surface_fit/v1`，表示原生框选后不再做坐标调整；
- 实际 `zoom_to_selected_level`；
- 单命令限制与自由/人工 PAN/ZOOM 禁止标记。

## 已移除方案

屏幕响应探针、多帧构图搜索、PAN/ZOOM 恢复缓存、完整接收主体参与 fit、临时隐藏非焦点部件后 Refit、像素箭头和几何检测动态裁切均已从正式代码与合同删除，不能作为回退入口。
