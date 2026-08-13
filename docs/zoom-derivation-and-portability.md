# Creo 装配 SOP：Zoom 推导与可迁移数据方案

## 结论

存在成熟、可迁移的 Zoom 方案：以**阶段可见集的屏幕投影边界**为输入，按目标画面占比推导原生 Zoom，并以一次受控复核验证结果。

迁移的是算法、策略和标定 schema；**不迁移**任何水箱或其他产品的 Zoom、PAN、CENTER 补偿、occurrence 路径、相机矩阵或步骤合同数值。

Creo 的 `ScreenTransform` 把 Zoom 定义为大于零的缩放因子；PAN 的单位是窗口宽/高。这使同一受控视口内的尺度与偏移可以确定性计算。官方说明见：[Transforming Window Coordinates](https://support.ptc.com/help/creo_toolkit/otk_java_plus/usascii/creo_toolkit/user_guide/Transforming_Window_Coordinates.html)。

## 1. 适用边界

Zoom 只负责构图尺度，不能替代以下决策：

| 决策 | 职责 |
| --- | --- |
| 阶段可见集 | 决定当前图中应显示的已完成件、移动件和必要接收件；未来件必须隐藏。 |
| 固定相机 | 仅从产品标定的 `fixed_123` / `fixed_456` 中选择，决定观看方向。 |
| 爆炸平移 | 由接收面法向推导，决定待装件与目标位的空间关系。 |
| Zoom | 决定主体在既定画面中的尺度。 |
| PAN 与固定裁切 | 在既定方向和尺度下放置画面；不改变相机方向或主体比例。 |

因此，接收面退化、被遮挡或安装关系不清时，不能靠提高 Zoom 掩盖问题；应先检查相机选择、阶段可见集、接收面和爆炸向量。

## 2. 通用推导算法

### 输入

- 已锁定的最终 ASM、版本与哈希；
- 当前步骤的前向可见集：已完成件 + 当前移动件 + 必要接收件；
- 已选择的 `fixed_123` 或 `fixed_456`；
- 固定原生画布与最终裁切框；
- 构图策略：目标占比 `r`、安全边距、Zoom 上下限与允许误差。

### 推导步骤

1. 用阶段可见集建立临时简化表示；隐藏未来件与非零件对象。
2. 应用固定相机，并在 Creo 中对该阶段执行 Refit/中心化。
3. 取得主体在**最终可用画框**内的投影边界 `(x, y, w, h)`。优先直接投影 CAD 点/边界；若接口受限，可渲染一张仅用于测量的预览图。
4. 计算可用宽高 `W`、`H`，以及目标缩放倍率：

   ```text
   z_raw = min(r × W / w, r × H / h)
   z     = clamp(z_min, z_raw, z_max)
   ```

   其中 `r` 为主体最大维度所允许占用的比例；安全边距和箭头预留空间应已从 `W/H` 中扣除。
5. 若主体中心不在目标中心，基于当前视口尺寸将像素偏移转换为 PAN。首次接入或 Creo 配置变化时，使用两个单轴 PAN 探针确定正负方向与像素响应；之后按该标定求值。
6. 用 `ZOOM=z`、`PAN=(px, py)` 重渲染一次，复核移动件、接收件、安装边界及箭头均落在安全区内。
7. 仅在测量误差超过阈值时进行一次有记录的修正；将最终值、测量值和证据写入相机合同。

### 为什么该方法可迁移

Refit 为每个阶段提供局部、可重复的基准；Zoom 随屏幕尺度缩放，而策略用归一化占比、像素边距和固定画布表达。因此模型尺寸、零件数量和装配结构改变时，只要重新测量阶段投影边界，推导仍然成立。

## 3. 推荐的可迁移数据包

### `zoom_policy/v1`（可跨产品复用）

```json
{
  "schema_version": "zoom_policy/v1",
  "target_occupancy": 0.75,
  "safe_margin_px": {"left": 80, "right": 80, "top": 80, "bottom": 120},
  "arrow_margin_px": 48,
  "zoom_limits": {"min": 0.5, "max": 4.0},
  "measurement_tolerance_px": 12,
  "max_correction_passes": 1
}
```

数值仅作为初始策略，须通过多产品试运行确认；它们不是从水箱合同提取的经验值。

### `viewport_calibration/v1`（按环境复用）

```json
{
  "schema_version": "viewport_calibration/v1",
  "creo_version": "<environment-specific>",
  "native_canvas_px": [1800, 2400],
  "publish_crop_px": [100, 400, 1600, 1600],
  "pan_response": {
    "method": "two_axis_probe/v1",
    "x_px_per_pan": "<measured>",
    "y_px_per_pan": "<measured>",
    "sign_verified": true
  }
}
```

它依赖 Creo 版本、图形窗口、导出分辨率和裁切规则；这些条件变化时应重新标定。

### `framing_measurement/v1`（可积累、不可直接复用）

每步保留阶段边界、目标占比、推导 Zoom/PAN、渲染后边界、误差和验收状态。它可用于统计并改进策略，但不得作为另一产品的直接数值输入。

## 4. 不可迁移数据

- 任一旧产品的 `zoom`、`pan`、`center` 补偿；
- 任一旧产品的相机位置、视图矩阵和相机基准；
- occurrence 路径、接收面编号、爆炸向量、BOM 文案；
- 从旧图像裁切、缩放或人工操作反推的参数。

原因是这些值依赖该产品的根坐标、模型范围、阶段可见集、接收面与局部遮挡。新产品必须锁定自己的最终 ASM 并重新标定两台固定相机。

## 5. 与现有流水线的差距

当前流水线已具备：

- 在 Creo Refit 后执行合同中的 `ZOOM` 与 `PAN`；
- 限制相机为 `fixed_123` / `fixed_456`；
- 强制 `stage_visible_bbox/v1` 焦点语义；
- 将构图参数作为步骤相机合同的一部分。

当前尚缺：根据阶段投影边界自动产生 `framing.zoom`、`framing.pan`、测量证据与复核结果的 `framing_planner`。

## 6. 落地建议

新增一个 `framing_planner`，输入阶段 occurrence 集、固定相机、`zoom_policy/v1` 与 `viewport_calibration/v1`，输出可审计的步骤构图合同。验收至少包括：

- 主体、接收件与安装边界均在最终裁切安全区；
- 接收面未退化为细线；
- 箭头长度、方向和不重叠通过既有像素规则；
- 视角仍精确等于产品标定的两台固定相机之一；
- 重渲染的实测边界与目标边界误差在策略阈值内。

## 一句话原则

**跨产品复用“如何测量和如何计算”，不要复用“上个产品算出来是多少”。**
