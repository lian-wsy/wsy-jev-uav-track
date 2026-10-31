# D6：独立Town02无运动安全输入探针

2026-10-31（Asia/Shanghai）。用户明确批准新增并运行**一次**本聊天独立Town02安全探针，不解锁、不移动、不改传感器配置、不触碰视频/外部采集上传任务。设计落地后先测试、独立复核，再运行一次；没有因缺测重启碰运气。

结论：**诊断已完成，不是D6通过或控制ready。** 真实状态/碰撞接口可读；当前配置下默认距离和Lidar查询各返回一次RPC错误，没有有效障碍间距。首次碰撞记录为true，后续五次false且事件时间不变，原事件保留。不能把后续false或探针退出0解释为零碰撞、可安全飞行。

## 实现与验证范围

实际运行版本对应 `72a8d25082b2a6afbc7b62934b5d38d11fd6c33e`；运行时4个源码SHA已与该版本核对。新增 `sensor_probe.py` 只接收raw RPC，不构造 `MeasuredSafety`、不生成指令、不填写clearance。保留原始字段是否存在，拒绝bool时间戳/非数/非有限值/非法pose等，避免SDK类默认False/0伪装测量。碰撞事件时间、测距采样时间与本机RPC时间括号分开记录。空点云/量程饱和/单方向测距都不认证全方向安全间距。

固定最多6轮、30次测量RPC，8秒内部预算；AirSim每调用timeout=1s，外层进程仍受90秒上限与资源监控。内部时限只能在RPC返回后检查，真正挂起由现有所有者外层回收，不宣称8秒硬抢占。接口错误不隐式重试；超时/状态错误/本机或state时钟倒退中止。错误文本不直接写入日志，避免任意响应内容泄漏。结果始终 `control_ready=false` / `clearance_m=null` / `NOT_CERTIFIED`。

新增 `safety` profile仅允许Town02，Town03在任何RPC前拒绝。沿用既有独立目录/二进制/冻结端口/保护配置/4GiB启动及768MiB运行门槛。launcher保留 `SAFETY_DIAGNOSTIC_COMPLETED` 独立状态，拒绝伪造control-ready的子进程回执；不把它复用成控制安全认证。

TDD记录：31个新失败用例先RED再GREEN；另1项父进程坏回执RED后修复。现场前相关91项通过，完整595 passed/1 skipped（17.61s）；独立复核批准一次预检查门控后的诊断。Python3.10实际运行客户端，Python3.14运行owner；两解释器编译通过，未安装/修改共享环境。

## 一次真实运行

原始输出保留在 `D:/JevUavRuns/20261031_d6_probe01/runs/town02_safety01_20261031/`，文件/字节数/SHA见[机器证据](evidence/d6_live_sensor_probe_20261031.json)。未修改旧失败、HDF、训练产物或既有trace。

| 项目 | 实际结果 |
|---|---|
| 地图 / 配置 | `Carla/Maps/Town02`，任务settings匹配；未调用load_world/reset/tick/取图 |
| 版本 | CARLA client/server均`7a7fdb3-dirty`；AirSim server API版本1，不据此推断发行版本或上游commit |
| 进程 / 端口 | Shipping PID20040来自独立副本，3200/3201/3202/42461均归该PID；未连接视频端口 |
| 资源 | 启动前4,317,180KiB；最后保存的运行快照2,462,680KiB；退出快照4,550,440KiB。运行监控通过；最后快照不是整个运行的连续最小值 |
| 诊断轮数 / 调用 | 6轮完成，20次测量RPC：state12、collision6、distance1、Lidar1；握手/配置/版本RPC不计入这个20 |
| 碰撞 | 首次true、后5次false；6次事件时间均为`1793431127962239488`ns；`collision_event_observed=true` |
| 距离 / Lidar | 默认名称查询均`RPC_ERROR/NOT_RECEIVED`，无重试；具体错误文本未保存，不能仅凭通用RPC_ERROR证明“硬件必然不存在” |
| 状态时钟 | 从`1793431128049241344`到`1793431128586252800`ns，跨轮前进；每轮before/after相同，不称为硬锁步校准 |
| 控制 / 数据 / 费用 | 解锁/运动/reset/图像/训练/用户源HDF读取/真实Jev均0；新增PASS episode0 |
| 清理 | 本任务句柄确认退出、监听清空、保护配置SHA未变；三个任务settings副本SHA均保持`67cd1ed7...ef2f1c` |

launcher从启动记录到终止约20.7045秒。其旧通用 `bootstrap=takeoff` 参数仍出现在回执中，但safety分支不读取它、不调用takeoff或任何运动契约；这不是起飞证据。独立默认地图文件沿用既有事务备份/恢复，前/配置后/恢复后三份SHA完全相同，未改传感器设置。退出时preflight/postflight中的剩余Python是owner自身，不声称当时全机没有Python。

首次碰撞事件比第一条state早约87ms：它发生在探针发送任何运动指令之前（本次运动指令总数0），具体接触对象/初始化原因未采集，标 `NOT_DIAGNOSED`，不能抹除或归因成控制器效果。true→false且事件时间保留与上游read-and-clear语义一致，但本次没有注入碰撞做独立因果验证，故不认证服务器源码等同上游。

## 现场暴露的计时限制与离线修正

20次测量RPC和6个轮次在原始报告中耗时全为0.0。随后无RPC读取本机 `time.get_clock_info`：Python3.10 `monotonic=GetTickCount64()` 分辨率0.015625s；`perf_counter=QueryPerformanceCounter()` 分辨率1e-7s。这个结果解释了子tick耗时被量化，**不是0ms网络延迟或已通过精密时序验证**，不据此报告p50/p95。

现场之后，新增一个RED回归再将新探针默认时钟改为 `perf_counter()` 并记录实现/分辨率。该修改没有第二次live验证：仅合成RPC和本机Python3.10离线复现，实际sleep调用区间读到0.0134422s，非真实网络数据。第一版运行代码独立提交保留，原始报告与SHA不覆写；修订后的代码不能冒充已经现场跑过。

最终全套 **596 passed, 1 skipped in 16.00s**；Python3.14/3.10编译通过，现场9个原始文件的大小/SHA及汇总数字逐项复核通过。Python3.10没有pytest，未声称在其上运行完整pytest；它提供的是实际live客户端、CLI导入和离线时钟复现证据。

## 重现与后续边界

以下是**本次实际运行命令**；run目录已经存在，不能直接覆盖重跑。未来运行需新的授权/独立输出及新鲜预检查，本轮不再启动：

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_safety01_20261031 --profile safety --map Town02 --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

真实缺口现在更具体：本配置没有拿到距离/Lidar测量，初始碰撞原因未诊断，仍缺几何覆盖/时空对齐与受限动作transport/停止回执。传感器配置变更、移动或再次live不包含在本次批准中，尚未执行。下一步应设计本任务独立几何测量配置及无运动验证；不能用全局默认间距或模型预测替代它。

D1 Town03保留测试PASS仍0；D3/D4/D5原训练与负结果复用不重跑。整体仍PARTIAL，目标active；不标D6通过，不扩大到D7闭环矩阵或论文收益实验。
