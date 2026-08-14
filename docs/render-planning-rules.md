# 安装图规划规则

每一步的渲染合同必须同时给出：活动 occurrence 组、接收 occurrence、仅平移向量、可见 occurrence 集、接收面编号、固定相机组及构图参数。

- 可见集只包含当前 BOM 项及其前序项；后续项以 Creo 临时简化表示排除。
- 数量大于一的同一 BOM 项作为一个活动组平移；已完成子装配进入父层时作为整体平移。
- 相机的唯一机器真值是根 ASM 坐标中的绝对位置方向 `ABS:px:py:pz` 与向上参考 `UP:ux:uy:uz`。`ABS` 是从当前阶段可见集中心指向相机的单位方向；相机沿其反方向看向可见集中心。正式任务禁止用“正面/背面/底面”或相对 `X/Y/Z` 旋转描述视角。
- 每个权威 ASM 必须先生成 `assembly-camera-basis/v4`。Creo 采用行向量变换：屏幕 right/up/back 是视图矩阵前三列，不能按前三行读取。若打开文件所得视向在任一根轴上的绝对分量小于 `0.15`，说明它会把至少一组接收面压成轮廓，必须保留原符号和 UP，以 `equal_octant_completion/v1` 补成等权三轴视向；否则逐方向重放保存视向。固定 456 保持 screen-up 不变，并与固定 123 中心对称。默认可见的 X/Y/Z 符号面依次命名为 1/2/3，反面为 4/5/6。任何自动补全仍必须经过正式渲染硬门。
- 正式任务只有两个允许的相机：`fixed_123` 与 `fixed_456`。接收面 1/2/3 默认先试 `fixed_123`，接收面 4/5/6 默认先试 `fixed_456`，但正式选择必须以“活动件和接收位置同时清楚可见”为准；禁止生成或选择任何第三视角。
- 正式规划必须先用接收面法向选择处于正确半空间的固定视角，再验证接收面夹角和爆炸向量的屏幕投影。若所选视角仍把接收面退化成线，该步骤必须留作疑问或重新规划；禁止在生成阶段盲目切到中心反向相机，因为它会落入接收面的错误半空间，也不得新造第三视角。
- 正向阶段可见集必须严格等于 `此前已完成 occurrence + 本步活动 occurrence + 接收 occurrence + 有证据的必要上下文`。禁止用根水箱或宽泛父 occurrence 代替精确路径，因为这会把未来零件带入早期步骤；只有已完成且在合同中列入 `rigid_completed_subassemblies` 的小合件根路径可以整体保留。
- 爆炸距离采用“最短可读距离”：从 45 mm 起，只有在活动件与接收位置仍重叠时才递增；箭头始终等长于审计后的纯平移，不得为了醒目额外拉长。
- 构图参数必须写入 `fixed-frame-presentation/v1` 白名单，并声明 `framing_priority=installation_activity/v1` 与 `zoom_anchor=installation_activity_center/v1`。执行器先以“活动 occurrence + 接收 occurrence”的根坐标中心为锚点，对整个阶段可见集施加同量临时平移，使该锚点落到根原点，再执行 Creo 原生 Refit 和 ZOOM；禁止只乘 Zoom 而保留偏离原点的活动区，这会退化为围绕错误角点放大。worker 不能接受 Qwen、GUI 或用户任意注入相机矩阵和输出路径。每张 1600×1600 方图用 `raster-composition-gate/v2` 测量主体、绿色原生箭头和边界；主体最长边占幅低于 54% 必须继续放大。正式方图不得二次动态裁切或重采样。
- 每张图都必须声明 `focus_context=stage_visible_bbox/v1`：先以临时简化表示中的阶段 occurrence 集 Refit/中心化，再用原生 ZOOM 强化安装活动区。完整设备轮廓不是硬门；为了放大安装对象、接收位置和箭头，非关键已装背景允许在最多两个画幅边缘适度出画。安装箭头必须完整留在画面内并距边缘至少 40 像素，绿色有效像素不少于 120、最长跨度不少于 24 像素；超过两个边缘被裁、箭头丢失或贴边视为放大过度。绝不通过新增第三相机方向换取特写。
- 只有主体和箭头中心硬门已经通过后，才允许依据主体占幅调整倍率。`centered-span-zoom/v1` 直接按 `新 ZOOM = 当前 ZOOM × 目标占幅 / 实测占幅` 推导，目标占幅为 55%，正式通过线仍为 54%，ZOOM 限制在 `[0.4,3.2]` 且最多两轮。换倍率后必须重新执行中心硬门；上一倍率的 PAN 响应不得复用。禁止为某个设备写入固定倍率或固定 PAN。
- 构图测量必须先屏蔽版本化的 Creo 固定消息区；当前 1600×1600 方图只忽略左下角 `[0,1250]-[500,1600]`。禁止让消息文字连通域参与主体边界，否则会把右侧小模型误判为“横跨全图的大主体”。
- 根原点对齐后仍须执行 `adaptive-screen-center/v1`。正常路径先使用 CAD 活动 occurrence 原点中心作为零成本初值，首张正式图已经过中心硬门时不增加任何探针。只有中心硬门失败，才在同一 ZOOM 下用两个相差 0.1 的正交 PAN 探针测得最终 JPEG 中主体中心与箭头中心中点的二维响应，解 2×2 线性方程，把活动中心移到 `[800,800]`。探针可围绕能看见活动区的有界初值展开，不要求从零开始；PAN 每轴绝对值不得超过 1.0，且该范围只允许用于居中，不能替代相机方向或倍率选择。响应矩阵仅按“Creo/导出环境 + 相机基底 + ZOOM + 画幅”缓存；同一键的后续步骤可直接从首张图求一次修正，不再重复两个探针。修正图必须重新测量；受 Creo 非线性影响仍超界时，以这张真实修正图作为新样本，对原响应做一次 Broyden/割线更新并再修正一次，不重新启动第二组探针。不得跨倍率复用响应。主体边界中心和箭头边界中心距 `[800,800]` 均不得超过 120 像素，否则分别报 `ACTIVITY_NOT_CENTERED` 和 `ARROW_NOT_CENTERED`，禁止发布。矩阵奇异、PAN 超界或修正后箭头不完整时同样不得发布。探针响应属于本次 Creo 环境校准产物，禁止由 Qwen 猜测，也禁止把某台电脑或某个设备的常数写入通用规则。
- 当两个固定视角及临时简化表示仍无法读出接收面时，才允许 `receiver_normal_only/v1` 的临时剖切回退。剖切平面必须以接收点和接收面法向为证据；禁止透明壳体、任意剖切或持久化写回源 CAD。
- 123/456 的角度和画面滚转均来自 Creo 默认规范视图，不再做等权化、重新配平或自定义 `UP=+Z`。紧固件步骤仍必须目视区分全部 BOM 数量对应的接收孔；若不能区分，检查接收面归类或装配根坐标变换，不得改用第三相机。
- 固定 123 与固定 456 使用同一版本化构图合同。正式 PAN 只能来自 `adaptive-screen-center/v1` 的同缩放测量与有界求解，不能来自固定预设、Qwen建议或人工凭感觉输入；相机方向不得为构图偏移而改变。
- 每张爆炸图使用 `same_cad_point/v1`：同一活动 occurrence 的同一局部 CAD 锚点分别经过爆炸态与完整态变换，箭头严格从爆炸态锚点指向完整态锚点。
- 所有正式安装图先统一为 Creo 原生 1800×2400 px（9×12 英寸、200 DPI）画布并在 Creo 内 Refit，再执行固定中心的 1600×1600 单次裁切。裁切不检测几何、不改变主体比例、不缩放，JPEG 质量 100，并排除底部会话文字。
- 父装配中的活动子装配必须以其根 occurrence 整体平移，并在合同中记录其全部内部 descendant；父层不得把这些 child occurrence 留在原位、重复平移或作为独立后续件显示。渲染前后均须核对根 occurrence 与 descendant 的相对位姿不变。
- 每张安装底图必须通过递归 occurrence 可见性与 Creo 原生显示配置隔离实体零件。不得按颜色、名称或图像后处理猜测/删除焊缝；所有基准、注释、透明平面、线条、焊接符号及其它非零件对象必须在 Creo 会话内原生屏蔽。安装箭头只在原生纯零件底图上叠加。
# 相机合同描述格式

人工说明统一写成：`接收面=5（默认可见 Y 面的反面，轴向=-Y）；相机位置方向=[px,py,pz]；向上方向=[ux,uy,uz]`。不得再写“转到背面”“转到底面”或“沿 Y 转 180°”。

123 的 `ABS` 与 `UP` 必须直接取自 `assembly-camera-basis/v4` 的 `fixed_123_position_direction_root` 和 `up_reference_root`，执行器不得自行猜测或接受模型任意改写。456 使用 `ABS=-fixed_123.ABS` 且保持同一 `UP`。执行器按 Creo 列语义一次性构造右手正交矩阵并调用 `SetCurrentViewTransform`；结果与此前步骤、当前 Creo 视图及旋转顺序无关。

绝对方向、构图与输出画幅必须解耦。两个固定相机都先由 Creo 原生 Refit/CENTER 得到基线，再只按编译后的 presentation variant 调整 ZOOM/PAN。构图硬门以画面边框中位色估计背景，忽略小于版本化连通阈值的消息区残留；它不动态裁切、不缩放或修改最终 JPEG。任何新 PAN 标定不得把某一缩放下的像素响应外推到另一缩放。

# CAD matching rule

Resolve a BOM item against Creo occurrences in this order: (1) BOM drawing number, then (2) BOM model number when no drawing-number match exists. Record the winning key and value in the stage contract. A model-number match is an explicit fallback, not an inferred substitution; if neither key maps uniquely, mark the occurrence unresolved rather than guessing.
