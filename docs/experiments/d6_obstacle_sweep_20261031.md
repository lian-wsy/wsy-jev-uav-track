# D6：已知夹具的体积扫掠响应核验

2026-10-31，Asia/Shanghai。本记录先冻结诊断协议，再追加现场结果；不是训练、Town03测试数据或跟踪效果实验。

## 为什么做这一步

上一轮完整点云证实：前4点预览会遗漏约1.196m的近命中；完整命中集仍不能证明射线之间无障碍。下一步不增加相同静态点云采样，而是检查安装的CARLA能否检测中心线以外、动作体积以内的已知障碍。

本地只读源码`D:/CarlaAir/repos/CarlaAir`（HEAD `d0c3fd717d5983fa488bedcdc055ebb2278c8780`）的`ObstacleDetectionSensor.cpp`在`only_dynamics=false`时使用`ECC_WorldStatic`球体扫掠，只有命中才发送事件；`RayTracer.cpp`的射线采用不同通道。该源码与发行二进制的构建对应关系未证实，因此源码只提供待检验机制，不能代替现场响应。本次不使用原始mesh接口：静态render顶点不等价于完整碰撞几何。

## 冻结协议

- 仅本聊天独立Town02实例，3200/3201/3202/42461；旧录像实例和端口不接触。原4GiB启动与768MiB运行门槛不变。
- 新增`sweep` profile，Town03在文件/RPC前拒绝；不启用旧`contract`起飞或速度bootstrap，不改变AirSim传感器配置。
- CARLA坐标：传感器`(0,0,40)m`、朝+x；半径3m、长度16m。固定`static.prop.box01`作为终端见证物`(12,0,40)m`。
- 顺序固定为baseline、中心阻挡`(6,0,40)`、偏侧阻挡`(6,2,40)`、体积外控制`(6,6,40)`。不因结果改prop、种子、顺序或位置。
- 只对本次返回的夹具actor关闭物理模拟以保持静止；不关闭碰撞、不移动/解锁/重置无人机、不取图、不连接Jev。
- 每组最多128事件、2s回调等待；总20s软预算，外层保留原RPC/客户端期限和独立进程清理。最多11个actor创建。第一组不通过即保留负结果并停止后续组，不重试。
- 必须有至少3个不同新帧，source时间跨度至少50ms，处于本次前后snapshot括号中，回调接收时间有序；对象ID、传感器pose、距离范围符合夹具；before/after几何静止且全部本次actor销毁成功。无回调不是“畅通”。
- 偏侧夹具的世界bounding vertices必须完全在3m横截面内且不与中心线相交；体积外夹具不得侵入该范围。bounding box只用于核验夹具布局，不冒充UAV碰撞外形。
- 启动器复算原始事件和几何，不只相信`status`。任何结果均保持`control_ready=false`、`clearance_m=null`。

## 离线验证（现场前）

43项判定用例、8项runner/地图/客户端用例、4项启动器用例均先观察预期失败，再实现。针对性102项通过；完整回归818 passed、1 skipped（23.10s）。Python3.14及现有CARLA Python3.10的compileall通过，3.10客户端CLI加载通过。未声称3.10运行完整pytest。

现场前另做独立代码审查。即使四组全部通过，也仅认证该固定夹具的查询响应；不认证Pawn碰撞通道等价、全地图覆盖、真实机体间距或地面至空中启动通道，不据此修改飞行安全阈值。

审查复现了一个真实超时缺陷：原收集器在`stop()`期间仍接受回调，停止调用才送到的3条迟到事件可能把2s内无响应错误改记通过。主代理用确定性反例复现RED，再增加6条窗口边界/缺失回归；修复为listen前冻结起点/截止，所有stop路径前锁内关闭收集，回执独立验证接收时刻属于`[opened, deadline)`且不晚于closed。62项新模块/接线测试通过。初次818项通过不覆盖该漏洞，未据此提前启动现场。

修复后主代理全套825 passed、1 skipped（23.21s），两解释器compileall及diff检查通过；独立审查62项通过并重放原迟到反例，另对spawn/physics/listen/stop/destroy异常做只读故障注入，确认逐一尝试清理所有返回句柄；销毁失败保留负回执且不继续下一组，不将尝试当作销毁成功。审查无剩余阻塞项，允许按原门槛进行一次独立现场诊断。

## 当前验收边界

本协议本身不是现场完成证据。D6仍PARTIAL，Town03独立PASS仍0；D3–D5冻结模型、数据划分和负结果保持不变。现场运行结果将在下节追加，失败记录不覆盖。

## 一次真实启动前检查：内存未通过，没有启动模拟器

源码冻结在`7e0fbad1f07d950fb284d1f76a221bda6bcdbacc`。19:24北京时间执行一次下列命令，退出1；输出保留在`D:/JevUavRuns/20261031_d6_probe01/runs/town02_sweep01_20261031`。

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_sweep01_20261031 --profile sweep --map Town02 --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

| 项目 | 事实 |
|---|---|
| 可用内存 | 4,178,332KiB，约3.985GiB；小于原4,194,304KiB门槛，差15,972KiB |
| 相关进程/端口 | 预检查仅有启动器自身PID59760，监听0；不是外部Python冲突拒绝 |
| 拒绝位置 | `Less than 4 GiB physical memory free; no launch`，在创建OwnedJob、改任务配置或启动Shipping之前 |
| 原始证据 | `run_request.json`368字节、SHA `d672640512e1876588eb85de9ed3a2b7546ef4e95cf4a5a3eec09439d5762537`；`preflight.json`249字节、SHA `1deaa835f928185843b61356a2538e66b88f716e239c6379e0fd50f3ad22d7ff` |
| 实际运行 | simulator启动0、夹具创建0、事件0、飞行命令0；四组全部`NOT_EVALUATED` |
| 保护核验 | 8份共享配置/用户AirSim配置SHA与准备快照一致；随后只读复查相关进程和监听均空 |

没有`owner.json`、`api_probe.json`或`smoke_result.json`，不能将不存在的现场回执说成成功。没有第二次启动尝试、降低门槛或关闭其他应用；没有HDF读取、训练、真实Jev调用和新PASS数据。

完整原文件/6个运行模块SHA、预检查原值、保护对照和测试信息见[机器证据](evidence/d6_obstacle_sweep_20261031.json)。可在不启动仿真时复查本轮新增的62项离线测试：

```powershell
python -m pytest -q tests/test_obstacle_probe.py tests/test_obstacle_probe_runner.py tests/test_obstacle_probe_launch.py
```

### 对验收的影响

本轮交付是**完成并审查了有正命中见证的体积查询诊断代码，修复迟到回调导致误通过的漏洞**；现场查询仍未运行。不能用825项测试或本次预检查替代真实体积响应/起飞/停止验收。D6仍PARTIAL，D1 Town03独立PASS仍0。

下一步只在资源状态实质变化、所有权重新核验后运行这份已冻结的四组协议；不重复训练或增加同类静态点云探针。即使夹具通过，随后仍需区分地面启动与空中跟踪，验证真实机体/碰撞通道及本实例原点；不把球扫掠夹具直接接成经过认证的3m无人机间距。
