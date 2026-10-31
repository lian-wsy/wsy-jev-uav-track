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

## 现场状态

此段将在独立复核和唯一一次预检查/诊断后以实际回执填写。当前未启动现场实例，不将离线测试计为live通过。

## 验收边界

D6仍PARTIAL：本切片不认证几何覆盖/时空对齐、受限动作transport或物理停止。D1独立Town03 PASS仍0；D3/D4/D5冻结训练和负结果不重跑。没有新增训练、数据准入、真实Jev调用或收益声明。
