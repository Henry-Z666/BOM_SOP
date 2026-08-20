# Creo 原生自适应居中与放大研究

## 结论

Creo Parametric 已经提供符合本项目需求的原生能力，探针不应继续作为正式构图的主路径。

首选能力是 **Zoom to Selected（缩放至选定项）**：Creo 根据选定零件、特征或几何的包围范围自动缩放，并把选定对象放到图形窗口中心。它不是给定绝对 PAN/ZOOM 坐标。[PTC 的选择说明](https://support.ptc.com/help/creo/creo_pma/r12/usascii/fundamentals/fundamentals/About_Filters.html)明确说明该命令会缩放图形窗口并将选定特征放在窗口中心；[操作步骤](https://support.ptc.com/help/creo/creo_pma/r12/usascii/fundamentals/fundamentals/To_Zoom_on_the_Model.html)说明它适用于几何、零件或特征。

第二个原生能力是 **Refit**：Creo 根据当前显示对象的几何范围自动平移和缩放，使整个对象进入窗口。PTC 说明 Refit 后模型约占屏幕 80%。[Creo 操作手册](https://support.ptc.com/help/creo/creo_pma/r12/usascii/fundamentals/fundamentals/To_Refit_the_Model_to_the_Window.html)、[Object TOOLKIT Java `WWindow.Refit`](https://support.ptc.com/help/creo_toolkit/otk_java_pma/r13/usascii/creo_toolkit/api/dita/c-wfcDisplay-WWindow.html)、[Creo TOOLKIT `ProWindowRefit`](https://support.ptc.com/help/creo_toolkit/protoolkit_plus/chinese_tw/creo_toolkit/6322.html)均有明确支持。

## 官方能力与边界

| 能力 | 自适应依据 | 自动化入口 | 结论 |
| --- | --- | --- | --- |
| Zoom to Selected | 所选对象的包围范围 | 选择缓冲区 + Creo 命令 | 最符合“按安装部件大小居中放大” |
| `WWindow.Refit()` | 当前窗口中显示模型的范围 | 直接 OTK Java 方法 | 稳定的无选择后备 |
| `ScreenTransform.SetZoom/SetPan` | 调用方提供的缩放因子和窗口比例坐标 | 直接 OTK Java 方法 | 不是自适应能力，不应作为主构图 |

`ScreenTransform` 的 Zoom 只是缩放因子，PAN 是相对窗口宽高的数值；它不会自动知道部件大小。[PTC ScreenTransform 文档](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/user_guide/Transforming_Window_Coordinates.html)。因此现有探针是在外部反推 Creo 已经具备的构图行为。

## Zoom to Selected 的可编程路径

PTC Object TOOLKIT Java 支持：

1. 用 `pfcSelect.CreateComponentSelection` 按完整组件路径创建选择对象；[Programmatic Selection](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/user_guide/Programmatic_Selection.html)。
2. 用 `Session.GetCurrentSelectionBuffer()` 获取当前选择缓冲区，并通过 `Clear()` / `AddSelection()` 写入所需部件；[SelectionBuffer API](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/api/dita/c-pfcSelect-SelectionBuffer.html)。
3. 执行 Creo 的 Zoom to Selected 命令。

在本机安装的 Creo Parametric 13.4.0.0 官方资源
`Common Files/text/compiled_resource/pro_default_resources.dll` 中，通用命令标识为：

```text
ProCmdZoomIntoOutline
Zoom to Selected
Zoom to selected object bounding box.
```

PTC 官方说明可通过 trail 文件或安装目录的 resource 文件确定现有命令标识，并用 `Session.UIGetCommand` 验证命令是否存在：[Finding Creo Parametric Commands](https://support.ptc.com/help/creo_toolkit/otk_java_pma/r13/usascii/creo_toolkit/user_guide/Finding_Creo_Parametric_Creo_Commands.html)。

命令本身目前没有公开的直接 J-Link `ZoomToSelected()` 方法。可通过 `RunMacro` 调用已确认的命令，但 PTC 提醒 mapkey/macro 文本语法可能随 datecode 变化，不能把手写字符串当成跨版本稳定 API：[Macros](https://support.ptc.com/help/creo_toolkit/pfcweblink_pma/r12/usascii/creo_toolkit/user_guide/Macros.html)。所以必须把 Creo 版本、datecode 和命令存在性纳入运行时预检，不能静默降级。

## 原生留白控制

PTC 提供配置项 `zoom_to_selected_level` 控制 Zoom to Selected 的留白比例：

- `1`：默认比例；
- 大于 `1`：放得更大，周边更少；
- 小于 `1`：留出更多周边环境。

官方说明见 [`zoom_to_selected_level`](https://support.ptc.com/help/creo/creo_pma/r13/usascii/detail/detail_configuration_options.html)。这仍是“相对于所选对象包围范围”的统一策略，不是产品或步骤的绝对坐标。

2026-08-20 已完成三档真实模型标定。正式策略采用两个产品无关的相对留白档：CAD 投影活动范围/上下文范围小于 `0.2` 时使用 `0.25`，其余使用 `0.35`。小、中、大三档均以 1 张正式帧通过，全部运行共生成 0 张探针帧；源 CAD 108 个文件哈希保持不变。

## 对现有实现的判断

当前 `RenderAssemblyImage.java` 已尝试临时隐藏非焦点部件后调用：

```java
session.RunMacro("~ Command `ProCmdViewRefit`");
```

随后仍应用显式 ScreenTransform Zoom/PAN，并由 Python 端通过多帧探针修正。问题在于：

1. 原生 Refit 的自适应结果随后又被外部 Zoom/PAN 覆盖；
2. 临时 Layer blank 是否与 Refit 的实际包围范围完全一致，没有被单独验证；
3. 代码没有使用已公开的直接 `WWindow.Refit()` 方法；
4. 当前代码注释称异步 `RunMacro` 会排队，但 PTC 的 [Execution Rules](https://support.ptc.com/help/creo_toolkit/protoolkit_pma/r11.0/usascii/creo_toolkit/user_guide/Execution_Rules.html)说明异步模式下宏会在载入后立即执行，该注释不应继续作为设计依据。

因此不能简单认为“Refit 已经验证失败”。实际失败的是“Refit + 显式 Zoom/PAN + 探针覆盖”的组合路径。

## 推荐的最小实测方案

### 主方案：选择对象后原生缩放

对每个正式步骤：

1. 保持 `fixed_123` / `fixed_456` 固定方向；
2. 完成阶段可见集和爆炸平移；
3. 将全部 moving occurrence 写入选择缓冲区；
4. 预检 `UIGetCommand("ProCmdZoomIntoOutline")`；
5. 调用 Zoom to Selected；
6. 清空选择缓冲区，恢复正常显示；
7. 只导出一张正式图并执行现有硬门验证。

冷启动目标为 **每步 1 张正式帧，0 张探针帧**。如果原生命令在当前 datecode 不可用，步骤应明确失败或进入下述直接 Refit 后备，不能恢复为无限探针。

### 后备：直接 Refit 焦点简化表示

若 Zoom to Selected 的命令调用不够稳定：

1. 激活只包含 moving occurrence 的临时焦点简化表示；
2. 直接调用 `((WWindow) window).Refit()`；
3. 激活正式阶段简化表示，但不再次 Refit；
4. 如需箭头留白，只应用一个固定的小于 1 的相对 margin multiplier；
5. 导出并验证一张正式图。

该路径仍由 Creo 按焦点几何大小自适应，不依赖屏幕坐标、旧产品 Zoom 或双轴响应探针。

## 验收条件

只有三档真实部件同时满足以下条件，才允许替换正式流水线：

- 小、中、大部件均在固定 1600×1600 输出中自动居中；
- 主体占比和箭头安全边距通过现有硬门；
- 每步最多一次原生构图和一次正式导出；
- 不读取或继承旧步骤 PAN/ZOOM；
- Creo 版本/datecode、命令标识和配置值写入审计；
- 源 CAD 哈希保持不变。

探针可保留为开发诊断工具，但不再进入常规正式生成链路。
