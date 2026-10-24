# 透明 rollout 与 learned outcome rollout 的闭环代理对照

日期：2026-10-24

本轮把已有跨 episode `LinearOutcomeModel` 接入同一个闭环回放，检查“训练过的候选后果校准”是否真的改善选择结果。报告同时保留透明 `KinematicRolloutModel` 基线和 learned 模型，避免只看训练误差。

## 模型与数据边界

- 模型文件：`artifacts/occlusion_pass_20261023/models/rollout_model.npz`。
- 训练 episode：4 个 PASS；留出验证 episode：`Town10HD_bicycle_normal_motion_long_seed260922365.hdf5`，没有与训练 episode 重叠。
- 训练记录的 RMSE：visibility `0.1099`、image-center `0.0763`、range `1.477 m`、covariance `0.1004`；留出验证 RMSE：visibility `0.2209`、image-center `0.0922`、range `2.917 m`、covariance `0.1920`。
- 所有回放仍是运动学/FOV 代理，不是 CARLA、AirSim 或实机动力学；near-miss 仍按默认 `0.5 m` / `0.5` 代理阈值统计。
- API key 未用于本轮实验，未发起网络请求；HDF5 只读打开。

## 留出 episode 结果

| rollout | 客户端 | proxy 可见率 | 平均重捕获时间 | 重捕获次数 | fallback 率 | near-miss | 能耗 (Wh) | 主要动作 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| transparent | Mock Jev | 0.453 | 5.267 s | 3 | 0.647 | 0 | 56.83 | orbit_left 134 / scan 166 |
| transparent | utility | 0.463 | 5.067 s | 3 | 0.000 | 0 | 56.35 | orbit_left 124 / scan 176 |
| learned | Mock Jev | 0.307 | 未完成 | 0 | 1.000 | 0 | 0.00 | hold_center 300 |
| learned | utility | 0.307 | 未完成 | 0 | 0.000 | 0 | 34.94 | hold_center 92 / scan 208 |

“未完成”表示固定窗口结束时仍处于 proxy 丢失状态，不能把它当成 0 秒重捕获。四行的身份保持率都只能标为 `NOT_AUDITED`，因为留出 episode 只有一个 actor id。

## 结论

当前 learned outcome 模型的离线拟合误差并没有转化为闭环代理收益。留出 episode 上，Mock Jev 的 learned rollout 由于候选排序和置信度门控退回 `hold_center`，可见率下降约 0.146；utility 也从 0.463 降到 0.307。这个结果更像是暴露了 rollout 校准与动作执行代理之间的分布偏差，不能把模型称为已验证控制器。

五 episode 汇总 `model_default.json` 只用于诊断，包含模型训练所用的 4 个 episode，因此不作为独立泛化结果。下一步应在更多、完全按 episode 隔离的 PASS 数据上重新校准，并把动作执行后的真实可见性/距离标签纳入训练；在此之前，透明 rollout 只能作为可解释基线，learned 模型只能保留为研究对照。

## 复现

```powershell
$env:PYTHONPATH = "src"

# 留出 episode 是 manifest 的第一项，--max-episodes 1 只取它
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --max-episodes 1 `
  --output artifacts\threshold_sensitivity_20261024\heldout_kinematic.json

python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --model artifacts\occlusion_pass_20261023\models\rollout_model.npz `
  --max-episodes 1 `
  --output artifacts\threshold_sensitivity_20261024\heldout_learned.json
```

完整 JSON 保存在被 Git 忽略的 `artifacts/threshold_sensitivity_20261024/`。
