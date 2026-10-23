# History-conditioned rollout 集成报告

日期：2026-10-23

本次把 `TargetBeliefHistory` 的摘要接入 `KinematicRolloutModel`，并在同一份已验证 PASS manifest 上重跑模块级对照。历史摘要只参与候选 outcome 预测；硬安全过滤仍然在 Jev/utility 选择之前执行。

## 实验输入

- manifest：`artifacts/module_ablation_20261022/pass_manifest.json`
- 训练 episode：6 个；验证 episode：6 个；均为 `validation_status=PASS`
- 每个 episode：300 帧；验证采样：每个 episode 12 帧，合计 72 帧
- schema：`carlaair_uav_v2_candidate`
- 数据覆盖：12 个 episode 全部 `target_visible_fraction=1.0`、`target_occluded_fraction=0.0`，每个 episode 只有一个已知 actor id
- 训练和评估只读 HDF5；没有启动采集器、上传器或网络 Jev 请求

运行脚本：

```powershell
$env:PYTHONPATH = "src"
python scripts/run_module_ablation_report.py `
  --train-episode "<6 个 PASS train episode>" `
  --validation-episode "<6 个 PASS validation episode>" `
  --output artifacts\module_ablation_20261023\report_history_rollout.json

python scripts/run_module_ablation_report.py `
  --train-episode "<同一组 train episode>" `
  --validation-episode "<同一组 validation episode>" `
  --output artifacts\module_ablation_20261023\report_history_rollout_gate_stress_7p8.json `
  --minimum-obstacle-margin-m 7.8
```

## 结果

### Nominal

| 变体 | 评估帧 | 跳过帧 | fallback 率 | unsafe 选择率 | 选择分布 |
| --- | ---: | ---: | ---: | ---: | --- |
| `jev` | 72 | 0 | 1.00 | 0.00 | `hold_center` 72 |
| `utility` | 72 | 0 | 0.00 | 0.00 | `hold_center` 72 |
| `no_safety_gate` | 72 | 0 | 1.00 | 0.00 | `hold_center` 72 |
| `no_short_history` | 72 | 0 | 1.00 | 0.00 | `hold_center` 72 |
| `no_identity_confidence` | 72 | 0 | 1.00 | 0.00 | `scan_reacquire` 72 |

### 安全门控压力配置（`minimum_obstacle_margin_m=7.8`）

| 变体 | 评估帧 | 跳过帧 | fallback 率 | unsafe 选择率 | 选择分布 |
| --- | ---: | ---: | ---: | ---: | --- |
| `jev` | 36 | 36 | 1.00 | 0.00 | `hold_center` 36 |
| `utility` | 36 | 36 | 0.00 | 0.00 | `hold_center` 36 |
| `no_safety_gate` | 72 | 0 | 1.00 | 0.50 | `hold_center` 72 |
| `no_short_history` | 36 | 36 | 1.00 | 0.00 | `hold_center` 36 |
| `no_identity_confidence` | 36 | 36 | 1.00 | 0.00 | `scan_reacquire` 36 |

与上一版相比，跨 episode outcome 模型的 range RMSE 和安全门控统计保持不变；身份置信度开关现在能改变候选 outcome 的信息增益和最终 fallback 选择。`no_identity_confidence` 的 `scan_reacquire` 是“将身份信息中性化为 0.5”这一代理实验的结果，不是对真实身份交叉场景的因果证明。

## 代码验证

- `python -m pytest -q`：`15 passed, 1 skipped`
- `python -m compileall -q src tests scripts`：通过
- `git diff --check`：通过
- 基础 rollout 回归覆盖：低身份置信度提高重捕获信息增益，历史遮挡触发扫描收益，目标加速度提高协方差。

## 限制与下一步

现有 PASS 子集没有遮挡、出框或身份交叉，因此历史遮挡分支和身份保持率仍只由合成回归测试覆盖。下一步应在不影响采集/上传链路的前提下，只把新的困难 episode 经过独立 PASS 审核后加入 manifest，再报告重捕获时间、身份保持率和跨环境差异。完成这一步前，不把本报告解释为真实闭环性能或飞行安全结论。
