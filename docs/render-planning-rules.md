# 安装图规划规则

每一步的渲染合同必须同时给出：活动 occurrence 组、接收 occurrence、仅平移向量、可见 occurrence 集、接收面编号、固定相机组及构图参数。

- 可见集只包含当前 BOM 项及其前序项；后续项以 Creo 临时简化表示排除。
- 数量大于一的同一 BOM 项作为一个活动组平移；已完成子装配进入父层时作为整体平移。
- 相机的唯一机器真值是根 ASM 坐标中的绝对位置方向 `ABS:px:py:pz` 与向上参考 `UP:ux:uy:uz`。`ABS` 是从当前阶段可见集中心指向相机的单位方向；相机沿其反方向看向可见集中心。正式任务禁止用“正面/背面/底面”或相对 `X/Y/Z` 旋转描述视角。
- 每个权威 ASM 必须先生成 `assembly-camera-basis/v3`。Creo 采用行向量变换：屏幕 right/up/back 是视图矩阵前三列，不能按前三行读取。固定 123 必须逐方向重放默认打开视图的完整正交矩阵；固定 456 保持 screen-up 列不变，同时将 right 与 back 列取反，形成真正的中心对称规范视图。默认可见的 X/Y/Z 符号面依次命名为 1/2/3，反面为 4/5/6。
- 正式任务只有两个允许的相机：`fixed_123` 与 `fixed_456`。接收面 1/2/3 默认先试 `fixed_123`，接收面 4/5/6 默认先试 `fixed_456`，但正式选择必须以“活动件和接收位置同时清楚可见”为准；禁止生成或选择任何第三视角。
- 两个固定视角必须用阶段预览验证；若其中一张把接收面退化成线或遮住活动件，只能选择另一张，不得新造第三视角。
- 正向阶段可见集必须严格等于 `此前已完成 occurrence + 本步活动 occurrence + 接收 occurrence + 有证据的必要上下文`。禁止用根水箱或宽泛父 occurrence 代替精确路径，因为这会把未来零件带入早期步骤；只有已完成且在合同中列入 `rigid_completed_subassemblies` 的小合件根路径可以整体保留。
- 爆炸距离采用“最短可读距离”：从 45 mm 起，只有在活动件与接收位置仍重叠时才递增；箭头始终等长于审计后的纯平移，不得为了醒目额外拉长。
- 构图先对阶段预览测量主体边界，再把确定性的 `ZOOM/PAN` 写入合同。禁止复用针对完整总装标定的固定 CENTER 补偿；正式方图不得二次动态裁切或重采样。
- 123/456 的角度和画面滚转均来自 Creo 默认规范视图，不再做等权化、重新配平或自定义 `UP=+Z`。紧固件步骤仍必须目视区分全部 BOM 数量对应的接收孔；若不能区分，检查接收面归类或装配根坐标变换，不得改用第三相机。
- 固定 123 默认侧的合同禁止非零 PAN，只允许记录 ZOOM；固定 456 中心对称侧允许并必须记录 ZOOM、PAN、正方形画幅和偏移测量来源。相机方向不得为构图偏移而改变。
- 每张爆炸图使用 `same_cad_point/v1`：同一活动 occurrence 的同一局部 CAD 锚点分别经过爆炸态与完整态变换，箭头严格从爆炸态锚点指向完整态锚点。
- 所有正式安装图先统一为 Creo 原生 1800×2400 px（9×12 英寸、200 DPI）画布并在 Creo 内 Refit，再执行固定中心的 1600×1600 单次裁切。裁切不检测几何、不改变主体比例、不缩放，JPEG 质量 100，并排除底部会话文字。
- 父装配中的活动子装配必须以其根 occurrence 整体平移，并在合同中记录其全部内部 descendant；父层不得把这些 child occurrence 留在原位、重复平移或作为独立后续件显示。渲染前后均须核对根 occurrence 与 descendant 的相对位姿不变。
- 每张安装底图必须通过递归 occurrence 可见性与 Creo 原生显示配置隔离实体零件。不得按颜色、名称或图像后处理猜测/删除焊缝；所有基准、注释、透明平面、线条、焊接符号及其它非零件对象必须在 Creo 会话内原生屏蔽。安装箭头只在原生纯零件底图上叠加。
# 相机合同描述格式

人工说明统一写成：`接收面=5（默认可见 Y 面的反面，轴向=-Y）；相机位置方向=[px,py,pz]；向上方向=[ux,uy,uz]`。不得再写“转到背面”“转到底面”或“沿 Y 转 180°”。

123 的 `ABS` 与 `UP` 必须直接取自 `assembly-camera-basis/v3` 的 `fixed_123_position_direction_root` 和 `up_reference_root`，不得手写通用 `[±1,±1,±1]`。456 使用 `ABS=-fixed_123.ABS` 且保持同一 `UP`。执行器按 Creo 列语义一次性构造右手正交矩阵并调用 `SetCurrentViewTransform`；结果与此前步骤、当前 Creo 视图及旋转顺序无关。

绝对方向、构图与输出画幅必须解耦。123 只在原生 Refit 后放大，不写 PAN。456 先在 `ZOOM=1` 的原生正方形预览中确定目标缩放，再在该目标缩放下渲染两个 PAN 探针，使用同缩放下的实测像素响应反解偏移并写入合同。不得把 `ZOOM=1` 的像素/PAN 系数外推到其他缩放。测量只忽略固定的左下角 Creo 消息区域，不动态裁切、不缩放或修改最终 JPEG。

# CAD matching rule

Resolve a BOM item against Creo occurrences in this order: (1) BOM drawing number, then (2) BOM model number when no drawing-number match exists. Record the winning key and value in the stage contract. A model-number match is an explicit fallback, not an inferred substitution; if neither key maps uniquely, mark the occurrence unresolved rather than guessing.
