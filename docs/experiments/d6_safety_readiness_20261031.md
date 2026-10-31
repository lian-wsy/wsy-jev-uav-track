# D6 live准备：安全输入与能耗单位根因审计

2026-10-31，基于`0763da1`。**本次是只读诊断和离线反例，不是修复、现场执行或D6通过。** 原D5压力诊断已另行提交；本轮不重复训练，不实施尚待批准的Jev概率接口新方案。

## 实际查明的三个缺口

### 1. 百分比与Wh被直接相减

`src/jev_uav_track/safety.py:21`直接比较`uav.battery_percent - outcome.energy_cost_wh`与百分比储备。`models.py`把二者明确声明为percent和Wh，候选描述也输出Wh；`UAVState`没有容量字段。因此它不是完整的单位转换。只读`git show a0e9476:src/jev_uav_track/safety.py`确认旧基线已有同一表达式，不把它归咎于本轮D6异步改动。

设容量为C Wh，耗能E Wh，则相同理想SOC定义下应先换成`100 * E / C`个百分点，再从当前百分比扣除。下面只是数学反例；C不是当前仿真器/无人机的真实参数，没有注入任何运行状态：

| 当前电量 | 构造E | 示意C | 原代码剩余数值 | 换算后剩余百分比 | 20%储备判定 |
|---|---:|---:|---:|---:|---|
| 20.005% | 0.002Wh | 20Wh | 20.003 | 19.995% | 原代码放行，正确换算应拒绝 |
| 20.005% | 0.006Wh | 200Wh | 19.999 | 20.002% | 原代码拒绝，正确换算可通过该单项 |

使用当前`CandidateAction`、`CandidateOutcome`、`UAVState`、`safety_reasons()`实际复现了这两种相反错误，而不是仅凭字段名推断。其他安全项设为可接受，仅隔离电量这一项；不代表完整动作可执行。修复不能凭空填入20Wh、200Wh或100%电量。

此外`rollout.py:164`的能耗是速度/yaw启发式，零速度零yaw时为0；这不包含可验证的悬停基础功率。Mock每次前缀按固定速率扣电是合成状态，也不是可直接移植到真实模拟器的测量。仅补一个容量常数还不足以认证能耗准确性。

### 2. 安全测量的来源尚未打通

- 对本机`carlaAir`环境的AirSim SDK做AST检查：`MultirotorState`声明9个字段，无battery；`client.py`没有名称含battery或energy的方法。**本轮没有查询服务器原始回复，不能断言服务器扩展永远不提供电量。** 当前结论仅为未找到已验证电量来源。
- 独立目录`D:/JevUavRuns/20261031_d6_probe01/settings.json`使用SimpleFlight，仅显式配置RGB。车辆/顶层Sensors及DefaultSensors均未配置；SDK虽有distance、lidar和collision读取API，方法存在不等于获得有效、时间对齐的测量，也不能证明没有服务器默认传感器。
- `MeasuredSafety`要求新鲜的battery、clearance、collision；缺失必须保持拒绝。`getRotorStates()`的转速/推力/扭矩不是电量；模型预测间距不能冒充当前实际间距。

### 3. 控制请求不等于执行与停止

`BoundedControlLoop`产生有界速度前缀，函数返回的是分发请求；它没有完成真实RPC、执行反馈或物理停止审核。已有异步/reset/超时Mock证据成立，但不能据此宣布当前实例已经能安全运行模型动作。原D6只要求准备受限smoke，不扩展为D7闭环矩阵；然而当前测量、运输层与停止证据仍不足以宣称ready。

## 资源不是本次拒绝原因

只读OS快照时间2026-10-31 05:21:43 UTC：可用RAM 6,431,464KiB；Python/CARLA/UE4/Unreal匹配进程0，所检查标准/视频保留/本任务端口监听0。本轮没有连接这些端口或操作外部进程。快照不是运行权证明，也不是长时资源保证；没有因缺少测量而反复启动模拟器。

SDK、独立settings和5个相关代码文件的完整SHA、声明字段、两个反例及快照见[机器证据](evidence/d6_safety_readiness_20261031.json)。没有读取HDF/训练/真实Jev请求/仿真启动/动作RPC/配置修改。

## 最小复现

在仓库根目录执行以下只读Python（`PYTHONPATH=src`）；它复现已知缺陷，退出0并不表示缺陷已修好：

```python
from jev_uav_track.models import CandidateAction, CandidateOutcome, UAVState, MissionConstraints
from jev_uav_track.safety import safety_reasons

for battery, energy, capacity in [(20.005, 0.002, 20.0), (20.005, 0.006, 200.0)]:
    outcome = CandidateOutcome(1.0, 0.0, 0.0, 1.0, 0.0, 10.0, energy, 0.0)
    action = CandidateAction("unit_only", "unit", "offline only", 1.0, (0.0, 0.0, 0.0), 0.0, outcome)
    actual = "battery_reserve" not in safety_reasons(action, UAVState(battery_percent=battery), MissionConstraints())
    expected = battery - 100.0 * energy / capacity >= 20.0
    assert actual != expected
    print(actual, expected)
```

输出依次为`True False`、`False True`。原有单测对合成状态的通过不能替代该缺失的单位/来源验收。

## 复核结果

主代理重新读取8个记录文件验证SHA，执行报告中的完整复现代码，并逐项核对SDK字段/方法；结果为`DIAGNOSTIC_VERIFIED_NOT_FIXED`。独立审查同样复现两个反例，确认问题早于D6、证据限制与原验收范围表述一致。最终全套回归为`495 passed, 1 skipped in 14.09s`，compileall与diff检查退出0；这些通过不覆盖新发现的单位正确性缺口，不能写成缺陷修复。

## 下一步决策边界

先保持原测量门控和缺失拒绝。继续原标准需要找到并验证原生电量来源，以及有时间/坐标来源的障碍测量，再接真实分发与有界停止。另一条可行候选是仅对纯仿真显式建立能耗预算模型，并严格标注`SIMULATED_RESOURCE`，与真实测量分开；但这是改变电量输入契约，**需要用户明确批准，尚未实施，不能在原标准下自动记PASS**。不把它伪装为实测电量，不降低碰撞/间距/过期/资源门槛。

Town03仍保留作独立测试；以上接口调试须在开发地图进行。Town03方法无关采集需要独立实现与完整接纳审核，不通过静态几帧或API成功补齐PASS数。D1–D6目标保持未完成。
