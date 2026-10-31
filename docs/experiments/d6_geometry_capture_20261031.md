# D6：完整受限点云、机体状态与接触几何的独立现场诊断

2026-10-31，Asia/Shanghai。运行源码冻结于`dbc09027411df5d14c829e427db46241fc7faf4e`；独立目录`D:/JevUavRuns/20261031_d6_probe01/runs/town02_geometry03_20261031`。本次仅一次无运动诊断，不是跟踪闭环或PASS episode。前两次现场原始文件继续保留，不改写其失败/限制。

## 授权与实现

用户恢复目标并明确“以后不用问，都允许”。本任务D1–D6范围内的常规实现、测试和隔离实验不再逐切片请示；原资源、外部任务、数据隔离和密钥边界不变。这次执行已经提出的受限几何采样方案，不接飞行控制、不进入D7矩阵。

新增`--capture-sensor-geometry`，默认关闭；必须同时指定临时命名传感器、Town02和safety。启动器/客户端在任何进程、RPC或文件变更前验证组合。新增的是已有返回字段的归档，不增加测量接口或次数：仍为6轮、30次测量RPC、8s软预算、1s单RPC timeout，原拥有者外层期限不变。

- state：保留原timestamp，另存收到的position、orientation、linear/angular velocity、landed_state。
- collision：原事件标志/时刻/穿透深度独立保留，另存normal、impact_point、position、object_name、object_id。
- Lidar：完整保存最多10,000点/30,000标量的原顺序数组及其规范化SHA；超限拒绝，不截断成“完整”。预览继续保留供比较。
- 几何缺字段、非有限、非单位姿态等明确标记；不借SDK类默认值补数据，不把几何缺失抹成无碰撞。对象名仅接受有界字符集，未知字段和异常文本不写入报告。
- 完整点云仅进入专用JSON，stdout保留小摘要。始终`control_ready=false`、`clearance_m=null`、`NOT_CERTIFIED`。

27项新归档用例先RED、启动器接线21项RED/5项旧路径通过；补两项非单位Lidar姿态RED后修复。现场前完整693 passed/1 skipped，Python3.14/3.10编译及CLI检查通过。独立审查确认无解锁、起飞、移动、reset或图像调用路径。通用回执的`bootstrap=takeoff`是历史参数，safety分支不执行它。

独立审查还指出离线摘要器对bool/int和缺失null的校验不严，主代理复现3项RED后修复；这是现场结束后的离线分析器硬化，运行源码/原始数据未改变。

## 一次真实运行

| 项目 | 实际结果与边界 |
|---|---|
| 地图 / 配置 | `Carla/Maps/Town02`，加载settings匹配；传感器配置与上轮相同 |
| 所有权 | Shipping PID65596，3200/3201/3202/42461均归本任务；随后退出 |
| 资源 | 启动前4,395,672KiB；最后运行快照2,473,152KiB；退出4,506,524KiB。最后快照不是连续最小值，未降低4GiB/768MiB门槛 |
| 测量 | 6轮30/30返回：state12、collision6、distance6、Lidar6；24条附加几何字段有效但未标定 |
| 距离 | 39.7317、40.1766、39.8979、39.8888、40.0282、40.1933m；3条字段有效、3条饱和，仍不认证障碍命中或畅通 |
| 点云 | 393、393、393、392、391、392点，共2354点；6个完整数组与各自原返回hash/count一致 |
| 时间 | Lidar相对after-state的数值时龄93.002、96.002、99.002、6.000、15.000、21.000ms；仅数值差，不是时钟标定 |
| 时延 | 30次读RPC非零；0.2039–2.6884ms，nearest-rank p50=0.5139ms、p95=2.5728ms。拥有者约28.057s；不是Jev/20Hz控制延迟 |
| 清理 | 三份settings和地图原字节恢复、8份受保护文件SHA未变、拥有者句柄退出、监听清空；18:23:56北京时间再次只读复查一致 |

本次解锁、运动指令、reset、取图、真实Jev、训练、源HDF读取及新PASS episode均为0。原始16文件及5个运行模块SHA、恢复与资源证据见[机器记录](evidence/d6_geometry_capture_20261031.json)。

## 新获得的诊断结论

### 1. 本次接触对象已定位，但没有倒推旧运行或启动过程

碰撞标志为`true,false,false,false,false,false`，六条均保留同一事件时刻`1793442065290211328`ns。收到的对象名为`Road_Road_Town02_135`、object_id为1，法向约`(0,0,-1)`，接触点约`(0.38848,0.21031,6.89465)`；对象id仅按服务器字段记录，不作为CARLA actor绑定证明。

12条机体样本均报告`landed_state=0`（本机SDK为Landed）、线/角速度0，位置约`(0.03140,-0.00320,6.69262)`。事件比首条state数值上早3.087065856s。证据支持**本次有道路对象接触记录，采样时报告落地且静止**；没有早期连续轨迹，不能声称已观察完整降落/坠落过程、判定“正常接触无风险”，或把这个对象套给前两次缺少接触几何的运行。

### 2. 预览不是最近命中；完整命中也不是自由空间证明

同一次运行中，各帧前4点预览的最小距离为10.4265–25.1372m；完整数组最近命中却是1.195660–1.195663m。完整记录实际补上了预览遗漏的信息，不能拿预览最小值控制安全门控。

按配置四条pitch带（+10、−3.333333、−16.666667、−30度）计算，6帧hit counts分别为`[87,102,102,102]`三帧、`[86,102,102,102]`、`[85,102,102,102]`、`[86,102,102,102]`；2354点均在预设0.05度带内，最大偏差0.00046545度。该统计证明命中数据与四条扫描带相容，**不证明射线之间没有障碍，也不证明完整方位覆盖**。最近命中没有逐点物体身份，不能直接断言全是地面，更不能设置成经过认证的`clearance_m`。

### 3. 静止安装关系相容，不是全坐标标定

使用完整四元数，将假设安装偏移`(0,0,-0.5)m`应用到每轮before/after机体pose，12次与Lidar报告pose的位置/角度计算残差均为0（当前浮点精度、同一静止姿态，不是物理量无限精确相等）。它支持本次静止样本的安装关系相容；传感器数值时间仍早于state，不能将“同轮”当“同时”，不能据此认证运动中的对齐、CARLA世界原点、机体碰撞包络或扫掠空间。

## 复现

以下现场命令已执行，原run目录不可重用；不需要再启动即可复核已有数据。

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_geometry03_20261031 --profile safety --map Town02 --configure-diagnostic-sensors --capture-sensor-geometry --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
python scripts/summarize_sensor_geometry.py --input 'D:/JevUavRuns/20261031_d6_probe01/runs/town02_geometry03_20261031/api_probe.json' --output artifacts/d6_geometry_capture_20261031_summary.json
```

离线输出也采用exclusive-create；再次复算需换新输出路径。Python3.14与现有Python3.10分别实际执行了同一离线分析，结果字节相同，SHA `5140d0e8b0f3ab78502177dca48afccf199b4782b643bfed1160bef362667d2b`。原始api为279,364字节，SHA `98d2b526b65606c59c6dca171c009a95de47f6b25ee6c4121c90782fa806c582`；原smoke_result为301,787字节，SHA `f121878aa37eac72a8663419d362b00d1f37d7cd0989ef2b01b6517d5927fd73`。分析脚本不接仿真、不改输入；合成单测不计作现场证据。

发布前最终回归 **696 passed, 1 skipped in 21.44s**；两解释器compileall及diff检查通过。主代理复核16个原始文件、5个运行模块、2份摘要SHA，6帧点云及精确整数时刻，公开内嵌摘要与原摘要逐字段一致。初次验证脚本直接比较Python tuple与JSON list导致断言失败，改为相同JSON表示后通过；没有改原数据或使用数值容差掩盖时间戳差异。独立审阅者另复核原始点云/静止状态/接触对象/恢复与摘要，不用本报告代替原始证据。

## 验收与下一关键项

本次补齐“完整受限几何无法事后复查”的记录缺口，并将本次初始接触定位到服务器报告的道路对象；未解决几何覆盖/动态时空标定/实际受限分发与停止，D6仍PARTIAL。Town03独立PASS仍0，D1–D6整体仍PARTIAL；原D3/D4/D5模型、冻结划分及负结果不动。

下一步应区分**地面初始化/启动**和**已在空中的受约束跟踪**：当前落地状态和近距离返回不能充当空中任务ready。先确定启动状态与碰撞事件生命周期、给动作所需的局部几何及停止条件建立证据，再接执行运输层；不为了让落地状态通过而改小原障碍间距阈值，也不继续只跑相同静态探针制造进度。用户已允许范围内自主推进，不再以重复确认阻塞这条路径。
