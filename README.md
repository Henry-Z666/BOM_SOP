# Creo 原生装配 SOP（首版）

这是一个**自动规划驱动**的安装图生成闭环：BOM 给出层级，Creo 原生装配抽取器提供 occurrence、变换与约束事实，AI 据此自动规划并批量生成安装图。

正式出图以最终总装为唯一几何源，采用产品级固定 123/456 双视角、正向阶段可见性、纯平移爆炸和同 CAD 点箭头。中间 ASM 仅用于结构与约束核对。

## Codex Skill

仓库内置可复用 Skill：`skills/generate-creo-assembly-sop`。它封装了 BOM 规划、权威总装锁定、批量 Creo 渲染、箭头审计、硬校验及 SOP 发布流程。

在当前仓运行预检：

```powershell
./skills/generate-creo-assembly-sop/scripts/preflight.ps1 -ProjectRoot .
```

安装为个人 Skill 后可用 `$generate-creo-assembly-sop` 触发。

## 快速开始

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
python -m sop_pipeline.cli plan
python -m sop_pipeline.cli discover "零件图/jh9919000534.asm.1" data/cad_graphs/jh9919000534.json
python -m sop_pipeline.cli auto-plan data/contracts/pilot.internal-water-tank.json data/cad_graphs/jh9919000534.json
```

不需要人工审核；首版默认生成两个 pilot：

- `pilot.internal-water-tank`：子装配内部构建；
- `pilot.attach-water-tank`：子装配整体安装至父装配。

它们会先处于 `awaiting_cad_discovery`：这是自动抽取尚未执行，不是待人填写。系统不会伪造 Creo 图片。

## 工作流

1. `plan`：从 `BOM.xlsx` 解析层级，生成 `data/contracts/*.json`。
2. `discover`：Creo 原生抽取器自动输出 assembly graph（occurrence、变换、约束与接触面）。
3. `auto-plan`：AI 结合 BOM 和图谱自动确定活动件、接收件、平移向量与相机。
4. `validate`：执行渲染前的合同与图谱审计校验。
5. `render`：只对自动规划完成的合同调用 Creo 原生执行器；源 CAD 不会被修改。
5. `annotate`：对执行器输出的同相机完整/爆炸图叠加箭头与气泡。
6. `publish`：只对通过所有校验的步骤调用 Excel 发布器。

## Creo 执行器接口

设置 `CREO_RUNNER_COMMAND` 后，`render` 会在隔离工作目录写入请求 JSON 并调用该命令。命令必须接受请求文件路径作为第一个参数，并写出由请求指定的 `result_manifest`：

```json
{
  "step_id": "...",
  "assembly_file": "...",
  "complete_image": "absolute/or/workdir-relative.png",
  "exploded_image": "absolute/or/workdir-relative.png",
  "camera": {"name": "oblique", "matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]},
  "occurrences": [{
    "id": "...",
    "role": "moving|receiver|retained",
    "complete_matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
    "exploded_matrix": [[1,0,0,20],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
  }],
  "projection": {"moving_point_exploded": [120,80], "moving_point_complete": [220,160]}
}
```

渲染器必须是 Creo 原生 API/内嵌脚本实现（例如 J-Link 或 Creo Toolkit），不得使用屏幕坐标点击。

## 固定 123/456 两视角相机

正式渲染不再使用 `Y:180`、`X:-90` 等相对旋转，也不生成俯仰或相邻八分体候选。先从权威 ASM 的默认打开视图保存完整规范方向。Creo 视图 right/up/back 位于矩阵前三列；固定 123 重放该矩阵，固定 456 保持 up 不变并把 right/back 取反：

```powershell
./creo_java/run_camera_calibration.ps1 `
  -AssemblyFile ./零件图/jh9919000534.asm.1 `
  -OutputJson ./data/camera-bases/jh9919000534.camera-basis.json
```

随后由接收面法向和爆炸向量创建结构化相机合同：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
python scripts/plan_absolute_camera.py `
  --basis data/camera-bases/jh9919000534.camera-basis.json `
  --output data/runs/example.camera.json --bom-level 30.1.4 `
  --receiver-occurrence C_47 --receiver-normal 1 0 0 `
  --normal-evidence "Creo planar receiver face" --explosion-vector 158.244 7.536 -22.406
```

J-Link 接收的方向语义为 `ABS:px:py:pz,UP:ux:uy:uz,ZOOM:n,CENTER`。每一步只能在 `fixed_123` 与 `fixed_456` 中二选一，以活动件和接收位置同时清晰可见为硬条件；不得生成第三视角。`ABS` 是根 ASM 中从模型中心指向相机的绝对方向；同一合同从任意初始 Creo 视图执行都会得到同一矩阵。旧 v2/`CameraRotate` 仅保留兼容并标记 legacy。

456 构图校准使用 `scripts/refine_absolute_camera_framing.ps1`：首次调用从 `ZOOM=1` 预览计算目标缩放并返回两个探针 PAN，目标缩放下两张探针完成后才更新合同。123 不运行 PAN 标定，只记录主体 ZOOM。全程保留 Creo 原生像素，不做二次动态裁切。

## Excel 发布

`scripts/build_grouped_published_sop.mjs` 使用 `@oai/artifact-tool` 导入“一主工序一工作表”的 Excel 模板，将当前 42 张已验收安装图按 8 个 BOM 主工序分组嵌入。发布器只处理已通过合同、渲染和标注校验的步骤；模板永远不是步骤真值。

模板是项目输入，不随仓库提交。发布前指定它的路径：

```powershell
$env:SOP_REFERENCE_PATH = 'D:\\path\\to\\one-process-per-sheet-template.xlsx'
node scripts/build_grouped_published_sop.mjs
```

最终 Excel、图片、CAD 文件、Creo 隔离会话和本地构建缓存均由 `.gitignore` 排除。
