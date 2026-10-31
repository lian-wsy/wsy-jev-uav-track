# D6：机体位姿与登记几何诊断

2026-10-31，Asia/Shanghai。先冻结无运动诊断协议，再追加现场结果；不是跟踪实验、起飞验证或新PASS episode。

## 问题与证据边界

前一轮固定球扫掠夹具已现场通过，但仍未证明无人机自身碰撞外形与查询通道一致。完整Lidar诊断记录到道路接触；是否存在物理状态与渲染机体位姿分离，尚需同窗口测量，不能从道路对象名称推断已查明起因。

只读本地参考源码`D:/CarlaAir/repos/CarlaAir`（HEAD `d0c3fd717d5983fa488bedcdc055ebb2278c8780`）显示：`MultirotorPawnSimApi`在物理姿态应用后还有地面高度修正路径；该分支可能使Pawn与物理状态不同步。这是待检验假设，不是已证实的发行二进制根因。`ObjectRegister`返回的环境物体是登记目录，`BoundingBoxCalculator`基于可见mesh，不能当作当前碰撞完整包围体。

## 冻结协议

- 仅本聊天独立Town02，3200/3201/3202/42461；Town03在文件/RPC之前拒绝。保持4GiB启动与768MiB运行门槛及原拥有者/配置/输出隔离。
- 新`body` profile不进入旧contract/bootstrap；不解锁、飞行、设置pose、pause/reset、创建actor、拍图或修改AirSim设置。碰撞读可能消费事件标志，保留事件而不将后续false解释成零接触。
- CARLA环境目录只读一次，最多50000条、精确`JevDrone`或下划线组件前缀最多64条，不截断为“完整”。ID按十进制字符串保存避免uint64精度损失；目录始终标`REGISTRATION_CATALOG_NOT_CURRENT_POSE`。
- raw `simListSceneObjects('JevDrone')`须唯一返回JevDrone；随后固定6轮：state-before → object-global-pose → vehicle-local-pose → collision → state-after，合计31次测量/身份RPC。基础连接/版本/settings校验另计。
- 轮间100ms；AirSim每次RPC超时1s、整个诊断软预算8s，保留外层启动150s/客户端90s硬截止和拥有者进程清理。异常不重试、不填SDK默认值，只记录异常类型。
- 比较同一local frame的物理state与vehicle pose；每轮窗口<200ms。静止判据为前后线/角速度范数分别≤0.05m/s、0.05rad/s，位置误差≤0.02m、姿态误差≤1°；这些仅是诊断分类阈值，不是放宽控制安全门槛。local-global平移差保留为观测值，静止一致不叫动态原点标定。
- 无效字段/非有限数/非单位四元数/时钟倒退或无进展导致中止；窗口过期、运动、位姿不一致或未来碰撞事件保留为有效负诊断，不丢弃失败样本。
- 启动器从原始查询重验顺序、时间、字段、样本关联和比较值，不只相信完成状态。始终`control_ready=false`、`clearance_m=null`、`body_envelope_status=NOT_CERTIFIED`。

## 离线准备

回执验证19项、入口接线9项先观察失败后实现；加上已有本轮15项诊断用例，共43项body用例。相关102项测试通过。现场前另运行完整测试、Python3.14/3.10编译及独立审查；未完成这些步骤之前不启动。

首次全套868 passed/1 skipped、双解释器编译通过后，独立审查发现异常本地时钟可能继续采样并使JSON无法落盘，以及Python宽松相等会把布尔值与数字视为相同回执。有限输入相减也可能溢出为无穷。主代理分别复现RED，新增11条时钟、JSON独立副本类型、运算溢出回归；统一时钟验证、类型严格比较及派生数值有限检查后，54项body/入口用例通过。没有用初次全绿作为启动理由。

修复后主代理全套879 passed/1 skipped（23.78s）、Python3.14/3.10 compileall、3.10客户端CLI加载和diff检查通过。独立复核54项通过，并复演全部原反例；无剩余阻塞项，所有审查测试进程已退出。代码冻结后仍须新鲜所有权/资源预检查，未因审查通过跳过现场门槛。

计划命令（新运行目录，不覆盖旧证据）：

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_body01_20261031 --profile body --map Town02 --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

## 20:00正式预检查：未达原内存门槛，未启动

代码冻结提交`aac046df01ff7417eda5ebbeae0bcd1fad1643ed`。上面的命令仅执行一次，退出1；运行目录`town02_body01_20261031`保留，不覆盖旧尝试。

启动前只读快照20:00:30为4,264,736KiB，相关进程/监听空；随后正式预检查为**4,188,960KiB**，低于4,194,304KiB门槛5,344KiB（约5.22MiB）。启动器自身Python PID36520是唯一相关进程，监听0；因此是内存门槛拒绝，不是外部采集占用。小幅差距也不构成降低原门槛的理由。

拒绝发生在`OwnedJob`、地图配置事务或Shipping进程创建之前。`owner.json`、`api_probe.json`、`smoke_result.json`均不存在；实际模拟器启动、测量RPC、飞行命令、图像、Jev、HDF读取、训练和新PASS episode全部0。原`preflight.json`249字节，SHA `8eebdb741defe8ae2655c6fbb1818eaeaa0773ce92d3d17decd0369cc2e5503f`；`run_request.json`364字节，SHA `d9c98825d1a28bafa0c5f77dd21a388730298084d4dceb6e3c65ffd164546268`。

20:01:39结束核验：相关进程/监听均空，8份保护配置SHA与准备快照一致；记录该次退出核验的可用内存4,307,076KiB，不据此连续重新启动。没有关闭其他应用、修改共享资源或联系其他聊天。完整原始请求/预检查、6运行模块SHA、保护哈希及结束核验见[机器证据](evidence/d6_body_probe_20261031.json)。

## 第一次预检查后的验收变化（历史检查点）

本轮完成的是**可运行的同窗口机体诊断及严格原始回执验证**，并修复审查实际复现的时钟/类型/数值溢出缺陷；现场机体结果仍`NOT_RUN_RESOURCE_GATE`，不能用879项测试替代。

D6仍PARTIAL、Town03独立PASS仍0，原模型与划分未变。后续资源条件稳定满足原门槛时，复用这份冻结协议；不要重复已有四组扫掠或旧训练，也不要凭登记box猜测碰撞外形。静态位姿诊断无论何种结果，都不能单独认证碰撞外形、支持接触例外、起飞通道或实际制动。

## 后续资源排查：减少拥有者自身的非必要导入

20:11只读快照4,203,260KiB、相关进程/监听空，仍非常接近门槛；没有直接重复启动。测量发现包入口在导入`isolated_runtime`时就预加载训练、数据、回放及Jev客户端模块。该次并未加载NumPy/HDF5，也未读任何源HDF，不能把问题夸大成大型模型占用。

对现有包入口做局部按需导出修正，保留原48个公开对象、`from ... import *`、直接子模块导入、`dir`、未知属性异常和类pickle身份。四个独立子进程依赖隔离测试先RED再GREEN，原公开API兼容回归通过；启动器新增包入口SHA审计（缺字段RED后补齐）。body测量协议、控制/资源门槛、模型和数据划分不变。

Windows `GetProcessMemoryInfo`的单次新进程样本：

| 观测 | 修改前 | 按需加载后 |
|---|---:|---:|
| 导入前working set，bytes | 16,969,728 | 16,908,288 |
| 导入启动器后working set，bytes | 30,752,768 | 24,244,224 |
| 导入后private commit，bytes | 20,307,968 | 14,761,984 |
| 提前加载training/data/replay/Jev模块 | 是 | 否 |

导入后工作集样本差6,508,544 bytes，约6.21MiB；只是单次样本，不保证每次节省相同值，更不能据此推定已满足全机资源门槛。原命令与两个完整测量输出见[机器记录](evidence/d6_runtime_imports_20261031.json)。新的启动仍必须现场重新检查4GiB和所有权；不关闭其他应用或减去启动器内存来“修正”系统读数。

主代理最终884 passed/1 skipped（23.68s）、两解释器compileall和3.10实际轻量导入/API身份检查通过。独立复核14项通过，核对原48个公开对象及顺序、冷启动star导入/子模块/dir/pickle/未知名称语义、包入口SHA回执；审查进程已退出。裸包导入不再提前生成偶然的子模块属性，这是按需加载的有意变化，显式子模块导入兼容。

## 后续现场：6个静态窗口一致，目录未提供机体几何

冻结代码`0a8fee8b4c5f20697f926ccb1c0fd10db50c7350`后，新的独立运行`town02_body02_20261031`完成，拥有者和客户端均退出0，状态`BODY_DIAGNOSTIC_COMPLETED`。首次资源拒绝及以上旧检查点全部保留。这次是实际Town02诊断，不是Mock、Town03测试或飞行实验。

实际命令先断言导入来自本聊天独立clone；下列运行目录已存在，复现须更换新目录，且必须重新通过原资源/所有权检查。

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
& 'C:/Users/20748/miniconda3/python.exe' -c "import importlib.util; from pathlib import Path; p=Path(importlib.util.find_spec('jev_uav_track').origin).resolve(); assert p == Path('D:/JevUavRuns/repos/jev-outcome-dataset-20261030/src/jev_uav_track/__init__.py').resolve(); print('Owned source path verified')"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& 'C:/Users/20748/miniconda3/python.exe' scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_body02_20261031 --profile body --map Town02 --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

正式启动前4,233,196KiB通过原4GiB门槛；最后运行快照2,428,632KiB通过768MiB门槛，但该快照不是持续内存最小值。独立Shipping PID67352，四个监听均归该PID；任务内地图配置恢复、源/用户8份保护配置SHA未变，拥有者确认停止，无清理错误。20:27:03追加OS核验相关进程/监听均空，可用内存4,561,644KiB。运行总长16.1893s，诊断采样0.653249s；没有关闭其他应用或占用视频任务。

| 项目 | 原始结果 | 可支持的结论 |
|---|---|---|
| 同窗口测量 | 31个身份/测量RPC、6轮、每窗口9.536–18.356ms；连接/版本/settings调用另计 | 这6个短窗口内可比较静态位姿 |
| 物理state vs vehicle-local pose | 最大位置差0m；最大姿态差约0.000001708°；12份state线/角速度均0 | 6/6为`STATIC_POSE_CONSISTENT_NOT_CALIBRATION`，本次没有复现静态错位 |
| local vs object-global | 平移差均为[0,0,2.8046514987945557]m，姿态差0° | 本次静态观测偏移；不认证动态标定、不写入跨实例常数 |
| CARLA登记目录 | 共3067条，精确JevDrone匹配0条；AirSim名称查询唯一返回JevDrone | 目录路径未提供自身几何，不能解释为无人机不存在、体积为零或已知完整碰撞包围体 |
| 接触 | 首轮true，后5轮false；全部保留同一原始事件时间戳1793449489522775552、道路对象`Road_Road_Town02_135`、penetration 0 | 有初始道路接触；不能从后续消费后的false宣称零碰撞或有效间距 |

物理/vehicle-local位置为[0.03139648213982582,-0.0031835937406867743,6.69262170791626]m；object-global的z为3.887970209121704m。接触法向接近[0,0,-1]，事件接触点[0.38847655057907104,0.2103320211172104,6.894651889801025]m。接触事件早于本轮取样，单个点/法向/零penetration不能推出整个机体外形或可忽略的合法支撑面。全部source时间戳按整数原样保存，没有经过JavaScript浮点JSON往返。

启动命令前曾尝试较轻的cmd包装：一次找不到python（9009）；一次PYTHONPATH引用未生效，误解析共享editable包并在`RuntimePorts`导入处失败；另一次只读`-c`引用诊断出现SyntaxError。三者都在启动/配置/RPC前终止，未写共享文件、不算物理试验。随后停止尝试cmd包装，使用上面已验证的PowerShell和明确解释器/包来源。完整失败类别随证据保留。拥有者通用回执中`bootstrap=takeoff`是未使用的默认参数；body分支在contract/bootstrap之前独立返回，原始31调用均无takeoff或其他运动。

10份原文件大小/SHA、完整API回执、拥有者/资源/保护配置、比较统计和结束核验见[现场机器证据](evidence/d6_body_probe_live_20261031.json)。主代理用Python3.14和原3.10客户端分别重新验证原始回执，且API与拥有者内嵌副本按类型严格相等；冻结运行代码SHA一致。

**当前结论：** 静态物理/渲染错位假设本次未复现；没有因此排除动态错位或证实native ground-clamp为根因。下一关键路径是取得有依据的自身碰撞边界、地面启动/支撑接触契约和受限动作/停止实测；不重复同一目录查询或静态采样，不凭渲染box猜测碰撞外形。D6仍PARTIAL，Town03独立PASS=0，`control_ready=false`、`clearance_m=null`、body `NOT_CERTIFIED`。本次无飞行命令、取图、Jev、HDF读取、训练、actor创建或新PASS episode。

现场后主代理再次完整回归**884 passed, 1 skipped（24.16s）**；59项针对性测试通过（1.54s）。Python3.14.7/3.10.21分别复算完整body回执，核对10原文件、8保护配置、22运行模块、任务地图恢复及整数纳秒时间戳；两解释器compileall和diff检查通过。测试证明实现回归与证据可复核，不替代上述未验证的飞行能力。

独立只读复核批准发布：10原文件、完整回执类型严格一致、6轮比较/统计、8保护配置和任务地图恢复、22运行SHA及冻结Git源码（允许文本CRLF/LF规范化）均通过，没有阻断发现。该审查未重跑现场或全套测试；OS结束快照、命令包装失败和测试运行仍是主代理记录，不冒称独立重复试验。
