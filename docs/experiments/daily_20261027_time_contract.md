# 2026-10-27：D3 时域契约切片

## 目标

修正 `horizon_frames` 与 rollout 时域的语义错位，并在冻结的 6 train/6 validation PASS episode 上重跑一轮离线模块对照。

## 发现与修正

17 个冻结 episode 的图像时间戳间隔均为约 `0.2 s`。因此默认 `horizon_frames=5` 的未来标签跨度是 `1.0 s`，而旧的透明 rollout 默认 `1.5 s`，二者不一致。

本次改动：

- `RolloutConfig.horizon_s` 默认改为 `1.0` 秒。
- `RolloutTrainingConfig` 支持可选 `rollout_horizon_s`；省略时从每个 episode 的时间戳推导实际跨度。
- 训练前检查未来跨度为正；显式时域与观测跨度超过 `0.05 s` 会拒绝。
- 跨 episode 训练检查 train/validation 的解析时域一致，并将 `resolved_rollout_horizon_s` 保存到 dataset、model summary 和 metrics。
- CLI 增加 `--rollout-horizon-s`，默认不覆盖数据时间戳。

## 验收

```powershell
python -m pytest -q
python -m compileall -q scripts src
python scripts/run_ablation_report.py ... --horizon-frames 5 --max-samples-per-episode 24
```

结果：`42 passed`，编译检查通过。离线 ablation 产物位于本机被 Git 忽略的 `artifacts/time_contract_20261027/ablation.json`，使用 6 train/6 validation、每 episode 24 个训练样本、验证每 episode 12 帧。

## 小规模结果

| 方法 | validation range-error RMSE | 平均选择 utility | 平均 oracle regret |
| --- | ---: | ---: | ---: |
| kinematic baseline | — | 2.5750 | 0.0032 |
| full calibrator | 0.8747 m | 2.8975 | 0.0000 |
| no motion | 1.2178 m | 2.9093 | 0.0000 |
| no action | 0.8739 m | 2.8966 | 0.0000 |
| no visibility | 0.8747 m | 2.8975 | 0.0000 |

这批 validation episode 全部可见，离线选择的 `oracle regret=0` 不能解释为真实闭环提升；它只确认时域修正后的训练、推理和消融流程可运行。动作特征几乎不改变 RMSE，仍说明当前记录缺少反事实动作后果覆盖。身份交叉、真实碰撞和 CARLA/AirSim 动力学仍未验证。

## 下一步

继续 D3 的坐标/动作语义审计：为 CARLA 世界坐标、AirSim NED 和机体 FLU 写明确转换边界；在未证明仿真资源隔离前不启动 live instance。
