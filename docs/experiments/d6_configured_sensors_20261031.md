# D6：独立实例临时传感器配置与无运动诊断

日期：2026-10-31，Asia/Shanghai。

## 授权与范围

用户明确批准：仅在本聊天 `D:/JevUavRuns/20261031_d6_probe01` 临时启用距离/Lidar，三份任务settings先备份后恢复，并在测试与预检查通过后运行一次不解锁、不移动的诊断。独立端口3200/3201/3202/42461；不访问录像实例，不修改共享配置、采集、上传或真实飞行。此批准不包含控制器接入、起飞/移动、reset、取图或第二次重试。

上一轮[默认名称探针](d6_live_sensor_probe_20261031.md)的两个RPC_ERROR和初始碰撞记录保留，不覆盖其原始输出。本轮改变的是明确的传感器配置和查询名称；不是将旧失败重新标成成功。

## 配置语义依据

以 Microsoft AirSim 固定commit `1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41` 的[传感器文档](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/docs/sensors.md)、[Lidar文档](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/docs/lidar.md)、[距离传感器文档](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/docs/distance_sensor.md)及[配置解析源码](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/AirLib/include/common/AirSimSettings.hpp)核验字段。上游默认multirotor包含IMU、磁力计、GPS、气压计，不包含距离/Lidar；车辆Sensors按已有类型覆盖，再补缺失类型的默认传感器。本次仍显式保留四个默认类型及默认参数，以兼容替换式旧fork。**上游语义不是本机二进制源码身份的证明。**

唯一配置增量是 `Vehicles.JevDrone.Sensors`：

| 名称 | 明确参数 / 用途 |
|---|---|
| imu / magnetometer / gps / barometer | 类型2/4/3/1，Enabled=true，保留默认参数 |
| JevDistanceFront | 类型5，0.2–40m，前向，安装位置(0,0,-0.5)m，零旋转 |
| JevLidarDiagnostic | 类型6，4线、4000点/秒、10转/秒、40m；同安装位置，垂直+10°/−30°，水平0°/359°，SensorLocalFrame |

两个新增传感器关闭debug绘点和external-controller feed。未改无人机Z=-3的出生点、相机、时钟或端口。参数只用于控制诊断负载；不承诺完整障碍覆盖。局部点云坐标、传感器pose、无人机原点仍需独立校准；单射线、有点云或空点云都不是全方向最小间距。

## 代码、恢复与测试

新增 `sensor_configuration.py`，只允许Town02+safety显式opt-in。固定三个路径必须为独立普通文件、原始字节相同；拒绝链接/reparse/hardlink、目录逃逸、既有custom Sensors/DefaultSensors、错误vehicle/port和歧义JSON。所有备份先exclusive-create、flush/fsync/readback，再写PREPARED回执，之后才逐份stage+replace。替换前再次比较当前字节；不覆盖检测到的外部改动。备份及prepared/applied/restored回执保留，不删失败现场。

所有者预检查通过后才应用；进程句柄确认退出后才恢复。部分apply失败可恢复已变动副本；某份恢复失败不放弃另外两份，整体记AUDIT_FAILED。默认未opt-in路径不启用传感器。客户端比较完整加载settings与预期，再验证确切sensor block，查询明确名称并写入测量回执。

TDD：24项事务新用例先失败；18项命名/接线新用例先失败（另30项旧用例通过），实现后127项针对性通过；补充reparse祖先、stage期间并发改写、applied回执失败三项。当前完整 **641 passed, 1 skipped in 17.73s**。Python3.14与现有CARLA Python3.10均compileall及CLI帮助检查通过；3.10没有pytest，不声称在那里运行全套测试。

测量预算仍为6轮、最多30次测量RPC、8s软限制、每RPC timeout1s、原owned外层90s限制；运行内存门槛不变。使用高精度perf_counter并记录元信息。所有输出仍保持 `control_ready=false` / `clearance_m=null` / `NOT_CERTIFIED`，不修改生产低层控制器。

## 一次真实诊断结果

独立安全复核通过后，将实际运行源码冻结为 `42436d5022fb16875bf81bfa8bb612f2be805cde`，仅运行一次 `town02_sensors02_20261031`，退出0。原始输出在 `D:/JevUavRuns/20261031_d6_probe01/runs/town02_sensors02_20261031/`；[机器证据](evidence/d6_configured_sensors_20261031.json)记录5份运行源码及16份现场文件的SHA/大小，不覆盖上一轮。

| 项目 | 本次实测 |
|---|---|
| 地图/加载配置 | `Carla/Maps/Town02`，完整settings与预期匹配；两个明确名称贯穿查询 |
| 所有权 | 独立Shipping PID69556，3200/3201/3202/42461全部归此PID |
| 内存 | 启动前4,639,832KiB；最后保存的运行快照2,811,508KiB（非连续最小值）；退出4,853,568KiB |
| 调用 | 完成6轮、30次测量RPC：state12、collision6、distance6、Lidar6；全收到响应，无重试/超时；握手/版本等不计入30 |
| 距离 | 六次39.6019、40.3563、40.4269、39.9473、39.8901、40.0168m；3条VALID_FIELDS、3条SATURATED_RANGE；上限40m |
| Lidar | 六帧均非空，点数391、391、393、393、393、393；字段/pose结构通过，不认证完整覆盖 |
| 传感器数值时龄 | 相对after-state：距离约6–21ms、Lidar约60–99ms；跨轮时间戳递增，均为NUMERIC_RECENT_NOT_CLOCK_CALIBRATED，不是时钟/坐标标定通过 |
| 碰撞 | 首条true、后五条false，事件时间全部为`1793433215963986688`ns；初始原因NOT_DIAGNOSED |
| 控制边界 | `control_ready=false`，`clearance_m=null`，NOT_CERTIFIED；arm/运动指令/reset/图像/Jev/训练/新增PASS均0 |
| 恢复清理 | 三份settings原字节恢复、地图配置恢复、8份保护文件未变，owned句柄退出且监听清空 |

三份原始与恢复后的settings SHA均为 `67cd1ed7eab1a67b24586ce45a63364bc68a69d37e9e49eadb1ee04006ef2f1c`，临时配置SHA为 `22543ed1b20bd24ed8bc4d54832ddcfd6d5344becf8e41a009b410ec20494de4`。另外在07:54:47 UTC只读重新核对：8/8保护文件、3/3settings一致，PID69556不存在、四个独立端口无监听。没有关闭其他应用或控制外部任务。

无运动指令不等于仿真物理状态从未变化。碰撞事件比本次首条state早约3.765s，不据此推断碰撞对象/成因，也不抹去事件。6次Lidar pose一致，但vehicle-origin与全局几何尚未在此实例标定。六个距离值都在40m附近，**VALID_FIELDS只表示字段结构/取值检查通过，不证明真正射线命中**；已有[上游语义审计](d6_sensor_semantics_20261031.md)显示无命中最大量程可叠加噪声，不能凭略低于40m的读数认证通路畅通。本机具体命中原因未验证。

本轮保留每帧点数、前4点预览和完整返回点数组的规范化SHA，**不是完整点云归档**；不能由这些摘要重算全方向最近障碍。下一步几何认证需要完整点云/命中与坐标、机体包络及动作扫掠空间的对应证据，不用当前摘要代替。

## 实测计时与重现

Python3.10实际报告 `time.perf_counter / QueryPerformanceCounter()`，分辨率1e-7s；30条RPC耗时均非0，范围0.1931–2.7266ms；nearest-rank p50=0.4445ms、p95=2.3561ms。6轮采集区间4.4789–5.9907ms；owner整体约16.5402s。与先前粗粒度0值相比，高精度计时已在本次实际运行验证；这是30条本机读RPC的小样本，**不是Jev延迟、20Hz执行达标或闭环时延**。

以下为本次实际命令，输出目录已存在，不可直接重复运行；本次一次live授权已经用完，没有第二次启动：

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_sensors02_20261031 --profile safety --map Town02 --configure-diagnostic-sensors --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

通用回执仍包含历史默认 `bootstrap=takeoff`，但safety分支不读取/执行该bootstrap，不是起飞证据。服务器报告CARLA client/server `7a7fdb3-dirty`、AirSim API版本1，不据此宣称本机二进制与上游commit一致。

## 验收边界

本次关闭了“配置中没有拿到距离/Lidar有效载荷”的具体接口缺口；原始初碰、近量程和几何/时钟限制保留。D6仍PARTIAL：本切片不认证几何覆盖/时空对齐、受限动作transport或物理停止。D1独立Town03 PASS仍0；D3/D4/D5冻结训练和负结果不重跑。没有新增训练、数据准入、真实Jev调用或收益声明。

发布前主代理自动核对16份本次文件、5份运行源码、全部数值汇总/恢复标志及9份旧run证据；独立审阅者另核对原始文件/时间戳/计时/恢复并批准发布。最终回归再次得到641 passed、1 skipped（17.88s），生产代码未在现场后改动。代码与结果保留在既有功能分支，不合并main，不将整个目标标完成。
