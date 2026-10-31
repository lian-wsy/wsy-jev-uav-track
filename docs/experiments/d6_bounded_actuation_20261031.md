# D6：有期限的动作发送、异常中和与独立停止遥测

2026-10-31，Asia/Shanghai。代码版本 `9f20a1d5c620f82bd9478053ff8735ee565c250c`，独立clone/原功能分支；按用户“以后不用问，都允许”在既定D1–D6范围自主推进。**本轮是可运行的离线运输层与停止判定实验，不是CARLA现场运动、真实制动或D6整体验收。** 没有再次运行上轮静态几何探针。

## 实际补上的控制缺口

原`BoundedControlLoop`只产生50ms命令请求，最终观测/动作/能耗截止时刻没有传给后续执行端。新`ControlTick.dispatch_deadline_s`携带三者最早期限；实际执行前复查并缩短剩余命令时长，不能拿已经过期的“编译成功”当发送许可。

新增`actuation.py`提供：

- 单工作线程、单占用槽、无运动排队/自动重试；client只由注入工厂在工作线程创建，模块不连接默认端口、不启动/解锁/起飞/重置。
- 必须有有效controller lease及新鲜机体遥测。落地、缺测、旧generation、重复样本、超限动作、碰撞事件变化或事件历史倒退均拒绝；不降低原3m间距阈值，不把稀疏点云最近值改成认证clearance。
- 每次已尝试发送都在finally内独立尝试cancel和50ms零速度/零yaw中和命令。cancel失败不会跳过中和；仅严格`True`为RPC确认，异常内容不进入结果。AirSim桥接调用`.get()`，不以仅`.join()`掩盖异常。
- 200ms事务watchdog，超时/未知结果锁定`OWNER_TERMINATION_REQUIRED`。晚回复不能解除，不能替换线程重发；只有原拥有者监督器可按其隔离规则终止自己的实例。模块不擅自杀进程。
- 中和确认仍为`stop_status=NOT_VERIFIED`。独立`StopVerifier`需要三条接收于中和之后、源时间严格推进、源/本地跨度均至少100ms的自机遥测；线/角速度分别不超过0.05m/s和0.05rad/s，碰撞历史未变。500ms内未满足就不记停止。`TELEMETRY_SETTLED`只支持该次采样窗口的遥测稳定，不证明全空间安全/持续悬停或每个采样间隙都停止。

这是**一条命令→取消→中和→观察**的受限smoke事务路径，中和和观察增加额外时间，不是已经实现持续20Hz跟踪控制。已有异步高层/相同候选时域不变，所有方法使用同一低层与时长。

## TDD与独立审查发现的真实问题

本轮基线696 passed/1 skipped。新增controller lease先3项RED，能耗期限另1项RED；运输层初版53项RED，随后碰撞事件/时钟/停止确认6个用例RED，集成CLI2项RED，再逐项实现通过。

独立审查复现初版`fa76a44`仍有跨线程时钟漏洞：提交1.0s，监督器已读到1.195s（超过1.19s lease），工作线程时钟倒退至1.18s仍发送。主代理新增RED复现，改为监督器与工作线程共享、加锁的单调时间下界；锁不覆盖任何运输RPC。同时修正“完成恰好发生在poll过程中”被误判为未来时间的相反竞态。早撤销/工厂失败的结束时间也从错误的提交时刻改成实际读钟或明确未知，原证据不覆盖。

修后针对性**152 passed**，独立审阅者另行执行同组并复核上述问题关闭。最终完整**763 passed, 1 skipped in 22.89s**；两套解释器compileall及diff检查通过。Python3.10执行了实际smoke和编译，不声称在该环境跑过完整pytest。

## 14组可运行对照

seed固定17；真实生产pipeline、selector、controller、actuator和stop verifier串联，外部运输/时间/机体遥测是显式合成。utility与Mock Jev各完整运行下列7种情况，未挑种子或删除失败；下表每行结果在两方法相同：

| 情况 | 合成外部调用数 | 实际判定 |
|---|---:|---|
| airborne | 3（prefix/cancel/neutral） | 两次确认后，三条新遥测达`TELEMETRY_SETTLED`；RPC回执本身仍NOT_VERIFIED |
| grounded | 0 | `GROUNDED_NOT_READY`，未把落地初始化当空中跟踪 |
| expired_before_send | 0 | worker实际执行时`EXPIRED_BEFORE_SEND` |
| rpc_rejected | 3 | prefix未确认，但仍中和；锁定需拥有者终止 |
| rpc_overrun | 3 | 两次都确认但耗时350ms，仍锁定需拥有者终止 |
| watchdog_before_send | 0 | 250ms监督器超时，晚工作线程被撤销；锁定不能被晚回复清除 |
| unsettled_after_neutral | 3 | 中和有确认但速度仍0.1m/s，判`STILL_MOVING`而不是停止 |

正常事务合成用时100ms，不能当实际RPC性能或20Hz证据。两方法选中`orbit_left`；utility回退0，Mock每例均回退1，因此这14组证明的是共享执行/拒绝流程，不是Mock/Jev排名收益。没有真实Jev调用或模型更新。

两解释器动作、回执、拒绝、时间与停止判定一致。完整JSON**并非字节相同**：28处差异仅在7例Mock的4个概率末位，最大绝对差`2.7755575615628914e-17`；不改写概率或冒称严格跨版本重放。相同解释器/seed的两次完整输出字节一致由集成测试验证。

## 产物与复现

```powershell
python scripts/run_d6_actuation_smoke.py --output-dir artifacts/d6_actuation_20261031_final
& 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' scripts/run_d6_actuation_smoke.py --output-dir artifacts/d6_actuation_20261031_py310_final
python -m pytest -q
```

上述目录已经实际运行并保留；再次执行必须选择新目录，脚本拒绝覆盖。每份报告约383KB，含原始请求、选择回复、控制tick、运输调用和停止遥测。最终3.14报告SHA `cbcb65522f0570399ca3936f15220602c1d3f347e43776fcc079f7d474dc0005`；3.10报告SHA `4e0a95664dd2f53e01c41206f63a0a684eae5d1fa43fb5b6ac737f2f238f0cd8`。修复前两份报告也保留，其撤销结束时刻错误见机器证据，不再用于最终计时结论。

[机器证据](evidence/d6_bounded_actuation_20261031.json)包含四份原始报告及三个运行源码的hash/大小、矩阵和测试计数。源码hash对应本机实际运行字节，Git可能进行LF/CRLF规范化。

## 原范围验收与下一依赖

**D6仍PARTIAL，D1–D6整体未完成。** 已补“控制请求到真实端口接口”的可调用代码及异常/停止遥测回归，但本轮注入的是合成端口，没有在现场执行。上轮2354完整点与道路接触记录仍不构成空中扫掠空间/动态时空标定；地面初始化到空中ready的独立生命周期、现场受限动作和实测制动仍缺。不得拿历史较长/较快bootstrap直接绕过新门控。

下一关键项是用本聊天独立开发地图建立**地面启动与空中跟踪分阶段的测量契约**：当前地面接触保留，起飞通道/机体包络必须有可核验几何，空中仍维持原间距门槛；满足后将同一运输层接入拥有者监督器做一次受限live smoke。现有无副作用AirSim桥接已就绪供该路径使用，但不预先宣称可飞。

D1 Town03独立PASS仍0；D3/D4/D5冻结模型及负结果未动。本轮真实启动/动作、Town03取图/采集、HDF读取、训练、Jev调用、外部采集上传或视频任务操作均0。没有主分支合并，没有密钥读取/落盘，自动目标不标完成、不删除。
