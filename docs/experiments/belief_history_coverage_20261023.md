# TargetBelief 短时历史与数据覆盖检查

日期：2026-10-23

本次完成路线图 B 阶段的第一项代码切片：用 `TargetBeliefHistory` 保存最近若干个状态，并将摘要接入离线 Jev 评估和 action-conditioned rollout。历史条目包含目标 actor id、目标位置/速度、可见性、遮挡风险、身份置信度，以及 UAV 位置/速度/yaw；窗口额外计算身份一致性、遮挡比例、目标速度、UAV 速度和目标加速度。rollout 使用这些摘要调节重捕获信息增益、身份不确定性协方差和加速度不确定性。

## 代码验证

- 新增 `src/jev_uav_track/history.py`。
- `src/jev_uav_track/rollout.py` 根据历史可见率、遮挡比例、身份置信度和目标加速度调整候选 outcome；没有 history 时保持当前帧兼容默认值。
- `OfflineEvaluationConfig.history_window_size` 默认是 8。
- `use_short_history=False` 时不向选择器附加窗口摘要，记录中的 `history_length` 为 0。
- `use_identity_confidence=False` 时将当前窗口的身份置信度中性化为 0.5，避免只替换最新帧而留下不一致的历史。
- 合成测试覆盖稳定 actor、出框/遮挡代理、身份切换和目标加速；新增 rollout 回归覆盖身份信息增益、历史遮挡触发扫描和加速度协方差。
- 基础测试结果：`15 passed, 1 skipped`。

## 已验证 PASS 数据覆盖

本次使用的 manifest 为 `artifacts/module_ablation_20261022/pass_manifest.json`，包含 6 个训练 episode 和 6 个验证 episode：

| 检查项 | 结果 |
| --- | ---: |
| episode 数 | 12 |
| `validation_status=PASS` | 12/12 |
| schema | `carlaair_uav_v2_candidate` |
| 每 episode 帧数 | 300 |
| target visible fraction | 1.000 |
| target occluded fraction | 0.000 |
| 每个 episode 的已知 actor id | 1 个 |

这说明当前已验证子集适合验证正常跟踪和跨环境拟合，但不适合评价真实遮挡恢复、出框重捕获或身份交叉鲁棒性。合成测试只验证了 history 计算逻辑，不能代替真实场景证据。

## 重新运行的模块报告

历史接入后，nominal 和 7.8 m 安全门控压力报告均能重跑，固定 outcome 模型的验证 range RMSE 仍为 **0.835 m**；安全压力结果仍为开启门控 36 帧评估/36 帧跳过，关闭门控 unsafe 选择率 0.5。新的 `no_identity_confidence` 变体在 nominal 数据上选择 `scan_reacquire`，说明身份摘要已经能改变候选信息增益和 fallback 选择；由于真实 PASS 子集没有遮挡或身份变化，这只是模块连通性证据。

详细表格见 [`history_rollout_integration_20261023.md`](history_rollout_integration_20261023.md)。

下一步是把真实采集中的出框、遮挡、身份交叉和目标加速 episode 纳入新的 PASS manifest，再重新运行 history/identity 消融，并报告身份保持率和重捕获时间。当前 PASS 子集仍不足以证明这些困难场景上的收益。
