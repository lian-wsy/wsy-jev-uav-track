# D6：已接通传感器为什么还不能直接提供安全间距

2026-10-31。基于`508983b`及既有`town02_sensors02_20261031`返回，**只读离线再分析，无新仿真、RPC、训练或控制改动**。上一轮确实完成传感器接口接通；本轮检查怎样才能正确使用其数据，而不是再次运行同一探针。

## 1. 现场预览符合配置，但未覆盖四个通道

直接读取原始`api_probe.json`（28,821字节，SHA `9f6406b210b6d36f4d0b1cb5e6bde571f247cdc83d3e2ea1c7b264614093f6a0`），对6帧各4个保留点计算：

`azimuth = atan2(y,x)`，`elevation = atan2(-z,sqrt(x*x+y*y))`（本段正elevation朝上，原点云为NED）。

| 观察 | 结果 | 能支持的结论 |
|---|---|---|
| 24个预览点的elevation | 9.9999976992°—10.0000160166° | 均与+10°通道一致，不是四个通道都被验证 |
| 同一帧3组相邻azimuth差，共18组 | 3.6000040364°—3.6001181083° | 与名义3.6°间隔一致，不能据此证明全部扫描射线/点均正确 |
| 保留点距传感器范围 | 6.9609948316—26.5889498300m | 只是24个预览点，不能拿最小值6.96m当整帧或环境最近障碍 |

完整点数组只保留hash和点数，无法从hash还原遗漏点。这个保留方式适合上轮“是否获得非空响应”的授权目标，但不足以复核几何安全；不是本轮把旧诊断重新标为失败。

## 2. 即使补齐完整点云，稀疏命中也不等于全空间间距

重新读取固定上游commit `1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41` 的[UnrealLidarSensor.cpp](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/Unreal/Plugins/AirSim/Source/UnrealSensors/UnrealLidarSensor.cpp)：垂直角按通道均分，逐射线查询`ECC_Visibility`，只把命中点放进返回数组。该碰撞查询通道是否等价于机体碰撞仍需验证。源码不是当前本机二进制身份的证明；下面反例是**给定配置及上游理想射线模型下的数学分析**，不是在现场放置障碍后的实验。

四个配置通道为+10°、−3.333333°、−16.666667°、−30°。在理想0.1s周期中，4000点/s、4线、10转/s给出每线约100条射线/一周，即名义3.6°横向间隔；实际每轮射线数还受时间、取整和FOV影响，不能把391–393个命中点当成完整覆盖率。

构造一个半径0.05m的小球，球心距传感器4m，水平角1.8°、elevation0°。在传感器局部NED中球心为 `(3.9980262415, 0.1256430363, 0)`m：

- 对四个Lidar通道，允许任意azimuth后，最近可能射线到球心的垂距仍至少`4*sin(3.333333°)=0.2325793156m`，大于球半径；所以即使横向无限加密也会漏掉它。
- 对前向距离传感器，射线到球心的垂距为`4*sin(1.8°)=0.1256430363m`，同样大于球半径。
- 球表面距传感器最近仅3.95m；该小球可以不改变理想传感器命中结果。因此不能用“已命中点的最小距离”证明整个周围空间都这么空旷。

**这不表示现场存在这个小球，也不表示现有真实点云发生了漏检。** 它证明的是观测不充分：单看这些传感器返回，不能排除另一种更近的障碍布局。简单增加横向点数或把范围饱和值当自由空间，不能解决垂直盲区和碰撞几何差异。

以下纯数学复现已实际执行（不连接仿真器）：

```powershell
$radius = 0.05
$distance = 4.0
$nearestPitch = [Math]::Abs(10.0 - 40.0/3)
$lidarGap = $distance * [Math]::Sin($nearestPitch * [Math]::PI/180)
$frontGap = $distance * [Math]::Sin(1.8 * [Math]::PI/180)
if ($lidarGap -le $radius -or $frontGap -le $radius) { throw 'Counterexample invalid' }
@{lidar_gap_m=$lidarGap; front_gap_m=$frontGap; nearest_surface_m=($distance-$radius)}
```

## 3. 初碰与坐标缺口来自保留字段不足，不能猜

逐条核对当前原始回执，12条state投影仅保留timestamp；6条collision仅保留has_collided、time_stamp、penetration_depth。没有碰撞对象/接触点/法向，也没有同一采集轮的机体姿态和速度。任务本次输出目录下没有预期`logs/CarlaUE4.log`，递归未发现其他`.log`；已有stdout/stderr就是上述诊断摘要，不能补出丢弃的原始字段。因此**不能把初碰解释为正常落地、把0穿透深度解释为没有碰撞，或声称完成了机体与传感器位姿标定**。

固定上游[LidarSimple.hpp](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/AirLib/include/sensors/lidar/LidarSimple.hpp)在获取点云前组合安装位姿与机体位姿，之后打时间戳；自身注释也保留了运动中的位姿精度限制。返回pose在vehicle-inertial frame，不是简单安装外参。若按该契约把SensorLocalFrame点转到vehicle-local NED，必须使用配套旋转和平移；不能把它直接当CARLA坐标，也不能把旧实例的原点偏移照搬到新实例。

静态传感器局部→机体局部的刚性外参检查可以先离线完成，不必为了这一数学步骤解锁/移动。但世界坐标动作与当前机体朝向、传感器/机体采样时间及碰撞几何对应关系仍需要实际字段和后续独立校验。原有`AirSimOriginBridge`的跨位置原点检查不能拿一组静止pose伪装通过。

## 对下一步的实际影响

不把现有`min(point ranges)`接成`MeasuredSafety.clearance_m`，也不继续盲目加密扫描。应先补足可审计输入：完整受限点云、配套机体位姿/速度、碰撞对象与接触几何；保留来源时刻与本机采样窗口，先做离线坐标与漏检反例校验。之后才确定受限smoke采用的几何覆盖证据及真实分发/停止条件。这是待批准的下一项有界改动，本轮未实现、未再次启动。

本轮[机器摘要](evidence/d6_geometry_observability_20261031.json)将实测预览再计算、上游源码结论和合成反例分开。D1–D6整体仍PARTIAL，D1独立Town03 PASS仍0；没有放宽原验收或将新分析当成live控制通过。

验证：主代理按原SHA重新读取并逐项复算24个点的投影、18个间隔、全部state/collision投影字段以及两项射线-球垂距，机器摘要一致。`python -m compileall -q src scripts tests`通过；完整回归641 passed、1 skipped（18.16s）；`git diff --check`无空白错误。只提交本分析、机器摘要及日报，不改运行配置、源码、旧产物或模型。
