# D6：显式仿真能耗预算与单位修复

2026-10-31，北京时间。用户明确批准：仅隔离仿真可使用标注的能耗预算，不冒充电池测量；碰撞、实际障碍间距、过期、所有权及资源门槛不降低。基于`12e8b6f`，只在本聊天独立clone修改。本项是D6准备链路修复，**不是D1–D6整体通过、真实执行或跟踪收益**。

## 修复与边界

- `UAVState`新增可选`battery_capacity_wh`和`energy_source`，默认`None/UNKNOWN`。百分比减Wh修为`SOC - 100 * E_Wh / C_Wh`；容量/来源未知拒绝，负数/NaN/Inf能耗拒绝，达到储备边界不放行。没有全局默认容量。
- `energy.py`提供`constant_time_resource_v1`，需显式容量、初始百分比、功率。所有经过时间都扣预算，包含等待、悬停及NO_COMMAND；候选预测不重复扣费，重复读取不重复计费。时间倒退/异常锁定拒绝；控制器reset不补电。新实验run才创建新预算。
- `BoundedControlLoop`需显式`synthetic_simulation`或`isolated_simulation`及预算对象；没有opt-in仍走原生测量路径，要求电量、容量与MEASURED来源。模拟预算与原生电量同时提供会拒绝，不能隐式覆盖测量。执行域标签不是所有权证明，live调用仍须通过原owner/preflight。
- 模拟预算只覆盖能耗，不填写collision或clearance。几何缺失、无效、过期、碰撞或间距不足仍拒绝；50ms前缀、0.3m/s、15deg/s、200ms过期和原1s动作寿命不变。分发末端再次读取预算，防止计算耗时期间跌破储备。
- 同一预算模型生成当前SOC及候选Wh；请求快照保存配置、来源、剩余量，候选保留原启发式Wh供审计，每个tick标记能耗来源。Mock适配器不再把固定90%/动作计次扣电塞进`MeasuredSafety`。

这只是**未校准的时间资源模型**，不是实际电池、续航估计、物理耗能上界或动作能效排序。下面20Wh/90%/120W是显式smoke参数，不是无人机硬件参数；所有同1s候选成本相同，不能从本实验归因动作节能。

## 合成六组结果

两解释器均用seed17、45tick、dt=50ms、相同低层控制与预算；正常/延迟/超时采用受控50/150/250ms调度，不是实测网络延迟。完整最终trace各约1.52MB保留在`artifacts/d6_energy_20261031_verified_py314/`、`artifacts/d6_energy_20261031_verified_py310/`，机器摘要/指纹见[证据](evidence/d6_energy_budget_20261031.json)。复查前及中间版本的输出也保留，分别与最终输出字节一致，不删除早先证据。

| 方法 | 情形 | 请求 | 接纳 | 置信度回退 | 超时 | 已完成过期回复 | 指令前缀 |
|---|---|---:|---:|---:|---:|---:|---:|
| utility | normal | 3 | 3 | 0 | 0 | 0 | 42 |
| utility | delayed | 3 | 3 | 0 | 0 | 0 | 36 |
| utility | timeout | 3 | 0 | 0 | 3 | 2 | 0 |
| Mock Jev | normal | 3 | 3 | 3 | 0 | 0 | 42 |
| Mock Jev | delayed | 3 | 3 | 3 | 0 | 0 | 36 |
| Mock Jev | timeout | 3 | 0 | 0 | 3 | 2 | 0 |

每组经过2.25s，均消耗`120 * 2.25 / 3600 = 0.075Wh`，从18Wh到17.925Wh，SOC为89.625%。超时组零指令仍扣同样预算；最后一次请求尚未返回，保留`in_flight_at_close=true`。所有可及时执行的Mock选择实际为置信度回退，未调低阈值。

各自固定种子/完整动作轨迹重放一致；跨Python命令、计数和预算完全相同。原始JSON有23个末位浮点差异，最大`3.552713678800501e-15`，**不宣称跨版本字节相同**。代码中的独立单测另覆盖预算耗尽、单位相反误判、控制reset与末端检查。

## 兼容性与历史证据

- 旧`demo.py`与`closed_loop.py`也有同类单位错误，一并改正。demo显式声明20Wh合成示例；代理回放需要配置`simulated_battery_capacity_wh`或初始状态已明确的合成容量，可用CLI `--simulated-battery-capacity-wh`指定。二者仍使用旧动作启发式成本，**不含D6时间预算的完整idle模型**，回放报告明确`legacy_action_heuristic_no_idle`。未知容量不静默补值。
- 未改HDF、训练权重、旧报告或旧trace。旧选择证据缺来源/容量时，不获得新安全契约下的认证。需要历史数值时使用原提交与原环境；不能拿新预算输出覆盖旧实验。
- 10/28的16帧全拒绝现场输入证据在新schema下只读投影缺失字段为`UNKNOWN/None`，投影数16写入复现报告；已存在字段不忽略，伪造MEASURED/容量会失败。Python3.10严格文本重放通过：16帧、128候选、32安全拒绝，原证据SHA仍`ccab9258aa97abbeb877c299705b0423952933c03e3f3bdaeeb3a7262fe59906`。
- 临时合成测试夹具明确填写容量/来源。HDF往返测试在缺能耗来源时现在要求4帧全部拒绝，原显式关闭安全门的离线消融另测，不靠关闭门控修复生产路径。

## 验证与最小复现

TDD先复现13个单位/输入拒绝失败，再加入预算与集成失败用例后实现。独立复查发现预算检查自身耗时会再次越过分发期限、极小正功率/时长下溢为零成本；主代理新增9项测试均先失败，再分别修复。随后再补3项RED回归，修复最后观察时间未成为单调下界、实际dispatch节拍可早于50ms的问题。现在所有预算/安全回调结束后才最后读时钟，以固定能源期限一并约束动作/观测/能源；最终时间下界/错误锁定跨reset保留，下一前缀按实际dispatch时刻计时，正耗费不可下溢为0。没有引入任意物理功率下限。最终独立复审未发现剩余重要缺陷；这是限定范围代码复查，不是物理安全认证。

主环境最终完整回归`562 passed, 1 skipped in 14.85s`；针对性控制/能耗119项通过。Python3.10没有pytest，未安装或改动环境；该环境只认证真实运行的六组smoke、原现场证据重放与编译检查。两环境compileall与Git空白检查通过。初次3.10重放遗漏PYTHONPATH的失败保留说明，补`src`后成功，不称初次成功。

在仓库根目录运行，输出目录必须是全新路径：

```powershell
python -m pytest -q
python scripts/run_d6_mock_smoke.py --output-dir artifacts/d6_energy_new --seed 17 --ticks 45 --energy-capacity-wh 20 --energy-initial-percent 90 --energy-power-w 120
& 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' scripts/run_d6_mock_smoke.py --output-dir artifacts/d6_energy_new_py310 --seed 17 --ticks 45 --energy-capacity-wh 20 --energy-initial-percent 90 --energy-power-w 120
$env:PYTHONPATH='src'
& 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' scripts/check_live_belief_evidence.py --evidence docs/experiments/evidence/live_belief_20261028/belief03.json
python -m compileall -q src scripts tests
git diff --check
```

## 对D1–D6验收的实际贡献

已关闭“百分比减Wh”代码缺陷，完成经用户批准的仿真资源契约及Mock执行回归。**D6仍PARTIAL**：新鲜的实际几何/碰撞输入、真实控制transport及停止/确认回执尚待打通；本轮没有开模拟器或发送动作。4GiB启动/768MiB运行门槛和隔离要求不变。

模拟器启动0、真实Jev请求0、新PASS episode0、训练0；Town03保留测试PASS仍0。D5困难区间覆盖负结果不变，模型不自动升级；Jev概率解析新方案未混入本改动。下一关键项是在开发地图的独立实例验证真实安全测量与受限运输层，随后方法无关Town03数据接纳；不进入D7矩阵，也不联系视频聊天。
