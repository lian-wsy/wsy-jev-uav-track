# 当前版本最小跟随：接口修复完成，现场启动被外部占用拦截

2026-10-31，Asia/Shanghai。按用户“注意收敛，最重要的是实际跟随效果”推进。**本次没有新增真实跟随轨迹，也没有证明效果提升。** 当前模型到独立实例短跑入口已接通；正式尝试在启动前拒绝。不重复旧训练、资产审计或无变化的启动。

## 真正修复的执行问题

冻结因果D5模型的源时间戳时域为 **0.9999999776482582s**，原D6执行器要求候选严格为 **1s**。首次把真实权重接到执行器时，六个候选全部成为`INVALID_CANDIDATE`。已用真实权重先复现RED，再在加载边界显式使用1s运动学基线，经现有`LearnedOutcomeRolloutModel`的1e-6s一致性校验；不放宽执行器、不修改训练时间戳、权重或校准区间。约22.35ns的数值差不能再让整组动作被拒绝。

保留模型SHA `b67c0a70e50426dcebfff6a97adfaa3f286e1d26fe6a05e4f26e44a97f8ee189` 和原D5区间门控。它可能全部回退；不会换成验证集冠军，不把接口修复叫作模型收益。

## 只做一个最小试跑

- 独立Town02、固定seed `2026103101`、10s、脚本目标0.15m/s；Mock Jev，真实Jev请求0。
- 使用已有因果belief、当前模型、异步协调器和事务执行器；0.3m/s、15deg/s、50ms前缀、200ms回复/观测期限不变。每个前缀后cancel/零速度中和，不冒称持续20Hz控制。
- 固定动作只在初始化时转到直线路径朝向；**不是已完成转弯反馈控制**。几何选场景不看模型结果。先未解锁摆放，再读原点/姿态、几何/碰撞/落地状态；摆放不计起飞或跟随，仍落地就拒绝。
- 静态/动态登记box点距离有原始记录，另扣2m工程余量、保留3.5m阈值；这是**登记场景点距离，不是认证机体净空**。体积、未登记障碍、RGB检测、身份交叉和完整D6不因此通过。
- 记录可见率、20m距离RMSE、中心误差、实际路径、动作选择/回退和停止遥测。仅有可见率或无人机移动都不能证明跟随改善。拥有者重放`StopVerifier`，不能靠成功字符串接受缺测/仍运动/新增碰撞。

入口为`probe_current_follow.py`与`current_follow.py`，通过已有独立拥有者启动。原视频成绩仍按[旧版本报告](recorded_follow_20261031.md)解释，不挪到本次模型名下。

## 22:07:59正式尝试：没有启动

`town02_current_follow01_20261031`预检查看到外部Shipping PID29872及其Python父/子链，2000/2001/2002/41451均归该PID，不是本任务独立副本。脚本因**外部进程/监听存在**退出1；同时可用内存2,525,040KiB（约2.41GiB）也低于4GiB。后续只读确认外部链仍在且更换子进程，不能把端口瞬间变化视为资源释放。

本任务没有`owner.json`，没有启动Shipping/连接RPC/摆放/解锁/动作/取图/Jev。未停止外部任务、关闭应用或向别的聊天发消息。8份保护配置复核未变。原run request/preflight保留，见[机器证据](evidence/current_follow_20261031.json)。预检查拒绝不计模型跟随失败率。

复现：在本聊天独立clone执行，使用新的run-name，不覆盖原目录。命令再次校验原资源门槛、所有权、配置和端口；Town03禁入，不借用已有实例。

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
& 'C:/Users/20748/miniconda3/python.exe' scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town02_current_follow02_20261031 --profile follow --map Town02 --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

原`bootstrap=takeoff`为未使用的通用默认参数，follow不进入旧contract起飞/运动逻辑。

## 验证与下一步

24项针对性测试通过，含当前冻结权重→生产协调器/控制器/执行器→合成运输及停止的接线检查；这是软件集成，不是CARLA物理响应。完整 **950 passed, 1 skipped（31.45s）**，Python3.14/3.10编译和diff检查通过。独立审查23项通过并允许限定工程试验，新增当前权重session变体由主代理验证。

**下一步只有同一份当前版本短跑**：资源/所有权满足时先取得真实路径和录像，再决定修正跟随反馈还是模型。本次不扩大消融矩阵、不重复训练。D1–D6整体仍PARTIAL，Town03独立PASS仍0，当前版本跟随效果NOT_EVALUATED。
