# Jev 与选择模块消融报告

日期：2026-10-22

本次把同一个跨 episode `full` outcome calibrator 固定住，只改变决策模块。训练集和验证集分别使用 6 个完全不重叠的 `validation_status=PASS` trimmed HDF5；每个训练 episode 最多取 48 个样本，每个验证 episode 抽取 12 帧，合计 72 个验证帧。源 HDF5 以只读方式打开，没有启动采集器、上传器，也没有向 Jev API 发请求。

验证环境为：Town02 bicycle frequent-turns、Town05 bicycle normal-motion、Town05 pedestrian frequent-turns、Town06 bicycle normal-motion、Town07 pedestrian normal-motion、Town10HD pedestrian frequent-turns。当前抽样的 72 帧全部有目标可见标注，身份置信度来自适配器的已关联 actor id，均为 1.0；因此身份和遮挡模块的结果需要按“当前数据没有制造困难条件”解释。

## 固定的 outcome 模型

跨 episode 训练的验证 RMSE 如下：

| 输出 | 验证 RMSE |
| --- | ---: |
| visibility probability | 0.000 |
| image center error | 0.000 |
| range error (m) | 0.835 |
| covariance trace | 2.3e-16 |

这是校准基线，不是反事实飞行动力学模型。`LearnedOutcomeRolloutModel` 只替换可见性、中心误差、距离误差和协方差预测；碰撞概率、障碍余量和能耗仍来自透明的运动学 rollout。

## Nominal 模块消融

Nominal 配置使用 `minimum_obstacle_margin_m=0`、`maximum_collision_probability=0.05`，安全门控打开。Jev 行使用带 `identity_weight=0.8` 的离线 identity-aware Mock Jev；utility 行直接选择 utility 最大候选。

| 变体 | 评估帧 | fallback | unsafe 选择率 | 平均 selected utility | 平均 oracle regret | 主要动作 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `jev` | 72 | 1.000 | 0.000 | 2.898 | 0.000 | hold_center 72 |
| `utility` | 72 | 0.000 | 0.000 | 2.898 | 0.000 | hold_center 72 |
| `no_safety_gate` | 72 | 1.000 | 0.000 | 2.898 | 0.000 | hold_center 72 |
| `no_short_history` | 72 | 1.000 | 0.000 | 2.898 | 0.000 | hold_center 72 |
| `no_identity_confidence` | 72 | 1.000 | 0.000 | 2.898 | 0.000 | hold_center 72 |

这组结果有两个直接结论。第一，当前候选后果之间的 utility 差异很小，Mock Jev 的概率间隔没有达到默认置信度门控阈值，所以 72 帧都走了透明 fallback；`jev` 与 `utility` 的动作一致并不等于已经证明 Jev 更好，而是说明当前回放被 utility fallback 主导。第二，所有验证帧均可见、身份置信度均为 1，且 nominal 约束没有排除候选，因此短时历史、身份置信度和安全门控在这组正常数据上没有改变动作分布。`no_short_history` 的 fallback 原因从首帧后的 `confidence_gate:hold_previous` 变为每帧 `confidence_gate`，说明历史模块已经被实际关闭，但数据没有产生动作切换压力。

## 安全门控压力对照

为了验证安全门控路径本身，使用相同的 episode、模型和五个变体，仅把最小障碍余量提高到 7.8 m。这是离线压力条件，不代表当前任务的推荐飞行阈值。

| 变体 | 评估帧 | 跳过帧 | safe-action rate | unsafe 选择率 |
| --- | ---: | ---: | ---: | ---: |
| `jev` | 36 | 36 | 0.500 | 0.000 |
| `utility` | 36 | 36 | 0.500 | 0.000 |
| `no_safety_gate` | 72 | 0 | 1.000 | 0.500 |
| `no_short_history` | 36 | 36 | 0.500 | 0.000 |
| `no_identity_confidence` | 36 | 36 | 0.500 | 0.000 |

门控打开时，Town05 bicycle、Town06 bicycle 和 Town10HD pedestrian 的抽样帧没有候选通过硬约束，因此报告明确记为跳过；没有把“没有可执行候选”伪装成成功选择。关闭门控后这些帧仍被评估，但在原任务约束下有 50% 的选择属于 unsafe，说明门控对候选可执行性有实际保护作用。

## 代码和复现

- `OfflineEvaluationConfig` 提供 `enable_safety_gate`、`use_short_history`、`use_identity_confidence` 三个开关。
- `JevTrackSelector(enable_safety_gate=False)` 只用于消融；默认仍强制安全过滤。
- `UtilityRankingClient` 是 utility-only 对照，不代表真实 Jev。
- `MockJevClient.identity_weight` 让离线测试可以把身份不确定度与信息寻求候选联系起来；真实接口仍把 `TargetBelief.identity_confidence` 放入结构化 Jev state。
- 运行脚本：

```powershell
$env:PYTHONPATH = "src"
python scripts/run_module_ablation_report.py `
  --train-episode "<PASS episode>" `
  --validation-episode "<different PASS episode>" `
  --output artifacts\module_ablation_20261022\report.json

python scripts/run_module_ablation_report.py `
  --train-episode "<PASS episode>" `
  --validation-episode "<different PASS episode>" `
  --output artifacts\module_ablation_20261022\report_gate_stress_7p8.json `
  --minimum-obstacle-margin-m 7.8
```

本次完整 JSON 和模型保存在本机 `artifacts/module_ablation_20261022/`，该目录被 Git 忽略；本报告和脚本进入仓库。下一轮要让身份和短时历史真正拉开差距，需要补充出框、遮挡、身份交叉、目标加减速和 UAV 自运动变化的 PASS episode，并把真实执行后的身份保持率、重捕获时间和安全事件接入评估。
