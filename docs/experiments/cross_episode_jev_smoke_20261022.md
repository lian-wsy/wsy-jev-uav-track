# 跨 episode 训练与真实 Jev smoke

日期：2026-10-22

本次只读取 `validation_status=PASS` 的 trimmed HDF5。训练集包含 3 个 episode，验证集包含 2 个完全不同的 episode；每个 episode 最多取 64 个样本，训练/验证样本数分别为 192/128。

训练 episode：

```text
Town05_bicycle_frequent_turns_close_seed26094435.hdf5
Town07_bicycle_normal_motion_close_seed26094440.hdf5
Town10HD_bicycle_frequent_turns_long_seed26094433.hdf5
```

验证 episode：

```text
Town02_bicycle_frequent_turns_long_seed26094450.hdf5
Town05_bicycle_frequent_turns_long_seed26094453.hdf5
```

跨 episode outcome 校准结果：

| 指标 | 训练集 | 验证集 |
| --- | ---: | ---: |
| visibility probability RMSE | 0.0853 | 0.0245 |
| image center error RMSE | 0.0898 | 0.0257 |
| range error RMSE (m) | 1.806 | 2.225 |
| covariance trace RMSE | 0.0853 | 0.0245 |

验证集按 episode 的距离误差 RMSE 分别为 2.831 m 和 1.375 m。由于当前模型使用观测到的相机速度作为动作特征，这仍是 outcome 校准基线，不是反事实动力学模型。

## 真实 Jev 对照

在第二个验证 episode 上取 4 帧，步长为 75。Mock Jev 和真实 `TypeSafeJevClient` 使用完全相同的 belief、候选动作和 rollout 结果；只向真实 Jev 发送结构化 JSON，没有发送 RGB。API key 从本机 `.env` 读取，未进入代码、报告或 Git。

| 指标 | Mock Jev | 真实 Jev |
| --- | ---: | ---: |
| evaluated frames | 4 | 4 |
| safe action rate | 1.000 | 1.000 |
| fallback rate | 1.000 | 0.500 |
| mean oracle regret | 0.000 | 0.0295 |
| mean actual visibility | 1.000 | 1.000 |

真实 Jev 返回模型版本 `jev-1.14.0`。4 帧中第 0 帧两者都选择 `hold_center`；第 75、150、225 帧真实 Jev 选择 `descend`，Mock Jev 选择 `hold_center`，因此逐帧选择一致率为 0.25。第 75 和 225 帧真实 Jev 通过置信度门控，第 0 和 150 帧分别因为低置信度和滞回门控 fallback。

这只是小规模接口和选择差异检查；它不能证明真实 Jev 的动作优于 Mock，也不能替代闭环仿真或飞行安全验证。完整 JSON 结果保存在本地 `artifacts/cross_episode/jev_comparison.json`，该目录被 Git 忽略。
