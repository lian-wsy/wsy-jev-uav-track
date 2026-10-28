# D3：GLOBAL 与 vehicle-local NED 原点联验（2026-10-28）

## 结论

独立 Town02 实例实测：`simGetObjectPose('JevDrone')` 与 `simGetVehiclePose` / `getMultirotorState` 的位置原点不同，不能把静态地标标定直接套到无人机状态上。本实例 **vehicle-local NED = GLOBAL object NED + (0, 0, 2.8046522141) m**。新增显式 `AirSimOriginBridge`，不把该数值写成跨实例常量。

这补上 D3 的真实坐标原点证据，不是目标跟踪或效果对照。D1 独立 town final test 仍缺；D3 可见目标测量→belief→rollout→selector 的正式 live 接入仍未完成。没有读取源 HDF5、训练、真实 Jev 请求或修改共享环境。

## 原因与验证方法

本机 AirSim 源码 `NedTransform.cpp` 为物体使用 `global_transform_`，为 vehicle-local 使用出生点减去机体包围盒高度的 `local_ned_offset_`。`WorldSimApi::getObjectPose` 使用 GLOBAL；`PawnSimApi::toPose` 使用 LOCAL。源码只支持假设，不能代替 dirty 发行二进制的行为证据。

现场要求场景清单包含精确 actor `JevDrone`，不猜测名字或用其它 actor 凑配对。分别在已有开发动作之前、之后暂停本任务世界，读取同一 actor 的 GLOBAL pose、vehicle-local pose 和 estimated state。完整四元数方向应一致；local pose 与 state 位置误差须 ≤0.02 m、方向误差 ≤1°。任何读取异常都恢复本次暂停；原本已暂停的实例拒绝进入，不擅自恢复。

数值桥接至少需要两个配对点、相对首点至少0.25 m位移，拒绝NaN/Inf、缺失配对和超过0.02 m的偏移不一致。它只验证平移假设，不能自行证明输入 actor 身份或同时性；这些由现场采样负责。重置、重生或切图后必须重新标定，速度不加位置偏移。

## 全部尝试

[机器可读汇总](evidence/live_origin_20261028/summary.json) 按事先指定的 `origin` 前缀导出，保留全部三次尝试，不按成败筛选。

| 尝试 | 结果 | 说明 |
| --- | --- | --- |
| origin01 | FAILED，移动前结束 | Python客户端方法实际是 `simIsPause()`，不是RPC端点名 `simIsPaused`；错误已通过安装包源码确认并补回归 |
| origin02 | 启动前拒绝 | 可用物理内存4,111,356 KiB，低于原4 GiB门槛；未放宽门槛、未关闭用户程序。仅有preflight，无完整controller结果，导出明确记 `INCOMPLETE_CONTROLLER_RECORD` |
| origin03 | API_SMOKE_COMPLETED | 后续只读检查内存恢复后重新通过原门槛；原点桥接、相机、12/12观测读取、新鲜动作和过期拒绝通过 |

origin01/03均记录保护配置不变、所属进程退出和监听清理完成。origin02没有创建引擎；该结论来自控制脚本执行位置及终端错误，单独的preflight文件本身不构成完整生命周期证明。导出保留preflight及源文件SHA，不补造缺失的controller JSON。

## origin03 实测

| 量 | 动作前 | 动作后 |
| --- | --- | --- |
| GLOBAL NED z (m) | 3.8886747360 | 1.7880840302 |
| vehicle-local NED z (m) | 6.6933269501 | 4.5927362442 |
| local − global z (m) | 2.8046522141 | 2.8046522141 |
| local pose / estimated state位置误差 (m) | 0 | 0.0000032517 |

动作前后同时包含x/y/z位移和yaw变化，GLOBAL/local的完整朝向误差最大约0.000002415°。第一对确定平移，第二对检查不变性，桥接残差为浮点读数下0 m；这不是传感器零误差声明，也不是多地图标定结果。

原12个静态地标给出的是 **CARLA→GLOBAL object NED**：

```text
global.x = CARLA.x - 108.2799987793
global.y = CARLA.y - 307.8599929810
global.z = -CARLA.z + 4.3100001216

vehicle_local = global + (0, 0, 2.8046522141)
vehicle_local.z = -CARLA.z + 7.1146523356
```

若混用两种原点，本实例会引入约2.805 m的高度偏差。历史报告和原始JSON保留，新增说明纠正其泛称“AirSim坐标”的歧义。旧HDF的转换规则不凭此一实例的数值重写。

本轮相机外参误差0.519 mm / 0.001882°；12/12时间窗口通过，过期窗口在运动RPC前拒绝；动作阶段碰撞时间戳未增加。所有动作仍是开发诊断指令，不是模型选择结果。标准takeoff False与双时钟非锁步限制仍保留。

## 复现

沿用已准备的独立副本，不再次复制发行包；run-name和导出目录必须不存在。

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python scripts/probe_isolated_carla.py smoke --source D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor --task D:/JevUavRuns/20261028_d2_probe01 --client-python C:/Users/20748/miniconda3/envs/carlaAir/python.exe --run-name origin04 --profile observation --bootstrap velocity
python scripts/summarize_carla_probe.py --root D:/JevUavRuns/20261028_d2_probe01 --output artifacts/live_origin_reproduction --run-prefix origin
python -m pytest -q
python -m compileall -q scripts src
git diff --check
```

本轮 **185 tests passed**；编译与diff检查通过。新增7个原点数值回归、9个RPC边界回归、2个导出回归。原点桥接、客户端方法名和导出行为均先复现缺失行为/失败，再实现；只读审查后另补6个已有拒绝分支回归。修复了同时指定profile/prefix时漏掉preflight-only尝试的问题。证据中记录实际运行代码SHA，复现后发生的报告/导出改动不回填为运行时源码。

下一项：用这一桥接统一自身状态与可见目标测量，并接因果belief及候选选择。仿真器可见性/身份真值只能作为明确标注的oracle诊断，隐藏真值不进入目标估计；缺障碍测量时不能把默认10 m当真实安全间距。完整目标仍保持未完成。
