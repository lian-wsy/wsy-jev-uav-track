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
| learned + OOD guard 0.1 | Mock Jev | 0.453 | 5.267 s | 3 | 0.647 | 0 | 56.83 | 与 transparent 相同 |
| learned + OOD guard 0.1 | utility | 0.463 | 5.067 s | 3 | 0.000 | 0 | 56.35 | 与 transparent 相同 |
| learned + OOD guard 5 | Mock Jev | 0.290 | 6.800 s | 1 | 0.867 | 0 | 42.62 | orbit_left 27 / scan 219 / hold 54 |
| learned + OOD guard 5 | utility | 0.293 | 5.800 s | 2 | 0.000 | 0 | 45.29 | orbit_left 86 / scan 159 / hold 55 |
| learned + state OOD guard 2.75 | Mock Jev | 0.470 | 4.050 s | 4 | 0.677 | 0 | 56.78 | orbit_left 133 / scan 167 |
| learned + state OOD guard 2.75 | utility | 0.463 | 3.950 s | 4 | 0.000 | 0 | 53.23 | orbit_left 108 / scan 178 / hold 14 |

“未完成”表示固定窗口结束时仍处于 proxy 丢失状态，不能把它当成 0 秒重捕获。四行的身份保持率都只能标为 `NOT_AUDITED`，因为留出 episode 只有一个 actor id。

## 结论

当前 learned outcome 模型的离线拟合误差并没有转化为闭环代理收益。留出 episode 上，Mock Jev 的 learned rollout 由于候选排序和置信度门控退回 `hold_center`，可见率下降约 0.146；utility 也从 0.463 降到 0.307。这个结果更像是暴露了 rollout 校准与动作执行代理之间的分布偏差，不能把模型称为已验证控制器。

新增的 `max_learned_feature_zscore` 是可选支持域门控：候选特征超出训练均值/尺度的阈值时，单个候选回退透明 rollout，并在逐帧记录中写入 `learned_model_ood`。阈值 0.1 把所有候选都判为 OOD，因此只是安全的基线回退；阈值 5 只回退约 80% 选中动作，结果仍然较差。门控解决的是明显外推风险，不等于校准模型已经可靠。

新增的 `max_learned_state_zscore` 只检查观测状态特征；当状态 OOD 时，该帧所有 learned 候选都回退透明 rollout。留出 episode 的阈值 2.75 让 Mock 恢复到 `0.470` 可见率、utility 恢复到 `0.463`，但 92.7%/95.3% 的选中帧仍然是状态 OOD。这个阈值只作为待校准的安全保护候选，不能当成 learned 模型泛化证明。

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

# 可选支持域保护；阈值需要用独立 episode 校准
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --model artifacts\occlusion_pass_20261023\models\rollout_model.npz `
  --max-episodes 1 --max-learned-feature-zscore 5 `
  --output artifacts\threshold_sensitivity_20261024\heldout_guard5.json

# 状态特征门控；只在状态进入训练支持域时使用 learned outcome
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --model artifacts\occlusion_pass_20261023\models\rollout_model.npz `
  --max-episodes 1 --max-learned-state-zscore 2.75 `
  --output artifacts\threshold_sensitivity_20261024\heldout_stateguard2p75.json
```

完整 JSON 保存在被 Git 忽略的 `artifacts/threshold_sensitivity_20261024/`。
