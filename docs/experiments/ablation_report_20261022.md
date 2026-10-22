# CarlaAir 多环境训练与消融报告

日期：2026-10-22

本次实验只使用 `validation_status=PASS` 的 trimmed HDF5。训练集和验证集完全按 episode 分开，各包含 6 个 episode；每个 episode 取最多 48 个训练样本，验证集每个 episode 评估 12 帧。

覆盖的验证环境包括：Town02 bicycle frequent-turns、Town05 bicycle normal-motion、Town05 pedestrian frequent-turns、Town06 bicycle normal-motion、Town07 pedestrian normal-motion 和 Town10HD pedestrian frequent-turns。所有验证 episode 的原始可见性标注在本次抽样中均为可见，因此本报告不能评价遮挡恢复能力。

## 汇总结果

| 方案 | 验证 range-error RMSE (m) | 平均预测 range error (m) | 平均 selected utility | 平均 oracle regret | fallback | 主要选择 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| kinematic baseline | — | 4.415 | 2.659 | 0.0030 | 1.000 | descend 48 / climb 24 |
| full | 0.835 | 6.446 | 2.898 | 0.0000 | 1.000 | hold_center 72 |
| no_motion | 1.214 | 5.867 | 2.899 | 0.0000 | 1.000 | descend 72 |
| no_action | 0.834 | 6.449 | 2.898 | 0.0000 | 1.000 | hold_center 72 |
| no_visibility | 0.835 | 6.446 | 2.898 | 0.0000 | 1.000 | hold_center 72 |

`range-error RMSE` 是 outcome 校准模型对未来距离误差标签的跨 episode 验证误差；`平均预测 range error` 是候选 rollout 中被选动作的预测值。它们都不是执行动作后的真实闭环误差，因为单条记录只提供实际发生过的轨迹。

## 观察

完整模型的跨 episode 距离误差 RMSE 为 0.835 m，去掉运动信息后升到 1.214 m，说明短时目标/UAV 速度对当前 outcome 校准有贡献。去掉动作特征几乎不改变 RMSE，说明当前采集数据中“相机实际速度”与候选动作之间的反事实覆盖不足，不能据此证明动作信息没有价值。去掉可见性特征与完整模型完全相同，因为本次样本缺少可见/遮挡变化。

Mock Jev 在严格置信度门控下所有帧都 fallback，因此动作分布主要反映安全 utility 排序，不是 Mock Jev 的高置信度选择能力。此前的真实 Jev 4 帧 smoke 已在同一仓库记录：真实 Jev 有 2/4 帧通过门控，和 Mock 的逐帧选择一致率为 0.25。下一轮应加入有遮挡、出框、目标身份切换和不同 UAV 机动的 episode，再比较闭环可见率、身份保持率、重捕获时间和安全事件。

完整 JSON 和各消融模型保存在本机 `artifacts/ablation_20261022/`，该目录被 Git 忽略；源 HDF5 以只读方式使用。
