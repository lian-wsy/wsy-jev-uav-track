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

## 验收变化与下一步

本轮完成的是**可运行的同窗口机体诊断及严格原始回执验证**，并修复审查实际复现的时钟/类型/数值溢出缺陷；现场机体结果仍`NOT_RUN_RESOURCE_GATE`，不能用879项测试替代。

D6仍PARTIAL、Town03独立PASS仍0，原模型与划分未变。后续资源条件稳定满足原门槛时，复用这份冻结协议；不要重复已有四组扫掠或旧训练，也不要凭登记box猜测碰撞外形。静态位姿诊断无论何种结果，都不能单独认证碰撞外形、支持接触例外、起飞通道或实际制动。
