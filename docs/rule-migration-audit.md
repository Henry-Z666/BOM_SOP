# 对话与旧水箱成果迁移审计

本文件回答一个具体问题：哪些经验已经成为干净工作区可执行的正式规则，哪些只是历史试验，哪些仍明确冻结。正式运行不得依赖聊天记忆、旧图片、旧 `work/output` 或水箱 occurrence 常数。

| 成果 | 当前正式落点 | 状态 |
| --- | --- | --- |
| BOM 两项输入、总装版本锁定、完整 occurrence 路径 | `intake-preflight` 至 `map-bom-cad` Skill；`assembly-lock/v1`、`creo-cad-graph/v3` | 已迁移并执行 |
| 子装配先内部完成、父层再整体安装 | `formal_render_planner.py` 的 scope、receiver producer 和依赖图；生成前范围确认 | 已迁移并执行 |
| 顶板前先装密封件 | `interface-physical-precedence/v1`：同工序/同阶段密封先于封闭件 | 本轮从旧水箱脚本提升为通用规则 |
| 垫片 → 端盖 → 卡箍 | 同一 CAD 接口上的 seal → closure → retainer 依赖 | 本轮从旧水箱脚本提升为通用规则 |
| 压力传感器密封圈先于传感器、阀前/阀后密封分开 | 密封件按接收 occurrence 所属安装层拆组；唯一共享接口密封先于部件 | 本轮从旧水箱脚本提升为通用规则 |
| U 型夹先于其紧固螺钉 | 接收 occurrence 的 producer dependency | 已迁移并执行 |
| 同 CAD 点箭头、箭头随真实纯平移方向 | `same_cad_point/v1`、Creo `DisplayList3D`、箭头审计 | 已迁移并执行 |
| 不同步骤不能复用写死的三根向下箭头 | 每个 render job 的 moving occurrence、anchor 和 translation 独立编译；栅格/审计数量核对 | 已迁移并执行 |
| 固定 123/456 相机，当前不放大 | `default_refit/v1`，`Zoom=1/PAN=0` | 已迁移并执行 |
| 左下角 Creo 状态文字不参与主体测量 | `raster-composition-gate/v2` 的版本化 ignored region | 已迁移并执行 |
| 纯构图警告不能冒充结构失败 | 结构硬门与 presentation warning 分离；保留真实图交给 Qwen/人工复核 | 本轮迁移并执行 |
| 相机尺度签名、PAN/Zoom 探针推导 | `centered-span-zoom/v1`、`adaptive-screen-center/v1` 接口及测试 | 明确冻结；当前正式路径不调用 |
| 水箱 42 张的手写顺序、occurrence ID、距离和相机 JSON | 仅作为历史回归证据，不进入正式运行 | 不迁移具体常数 |

## 本轮暴露的运行时规则缺口

PyInstaller 单文件包会把内置 J-Link 资源解压到临时 `_MEI...` 目录。旧实现允许常驻 Creo Worker 和恢复任务继续引用该临时路径，GUI 重启或临时目录清理后会产生大量 `CREO_RENDER_FAILED`，即使磁盘上还留有已生成图片。正式路径现将版本化 Creo/J-Link 小运行时原子复制到每个 run 的 `internal/bundled-runtime/`，发现、渲染、重试和恢复只引用该稳定内部路径；它不进入用户交付目录。

## 规则进入正式路径的门槛

一项经验只有同时满足下列条件才算“已迁移”：

1. 位于 Agent/Skill 调用链的唯一正式实现中；
2. 输入来自版本化 Artifact 或 CAD/BOM 事实，不读取旧产物；
3. 没有产品专用 occurrence、坐标或固定 8 步假设；
4. 有确定性测试或真实运行诊断可复现；
5. 结果进入计划诊断、渲染诊断或步骤状态，GUI 可以解释。
