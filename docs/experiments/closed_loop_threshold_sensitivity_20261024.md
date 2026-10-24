# 闭环代理 near-miss 阈值敏感性

日期：2026-10-24

本轮把闭环回放里的 near-miss 代理判定拆成两个显式参数：距离余量阈值 `near_miss_margin_m`，以及碰撞概率相对硬安全阈值的比例 `near_miss_collision_probability_fraction`。原先的固定逻辑等价于 `0.5 m` 和 `0.5`，但报告没有把这个口径单独暴露出来，容易把代理事件误读成真实碰撞。

## 实验设置

- 输入：`artifacts/occlusion_pass_20261023/pass_manifest.json` 中的 5 个 `validation_status=PASS` episode，共 1,500 个决策帧。
- 读取方式：HDF5 只读，`load_images=False`；未启动或修改 CarlaAir 采集、上传、锁、端口、共享配置或真实飞行任务。
- 客户端：离线 Mock Jev 与 utility；本轮没有发起网络请求。
- rollout：透明 `KinematicRolloutModel`，未加载训练模型；因此这是阈值和指标管线实验，不是动力学性能结论。
- 采样：`stride=1`、`max_decisions=512`，FOV 半角 75°，最小障碍余量 3 m。
- 区间：`episode_statistics` 给每个 episode 一个权重，`ci95` 是 episode-level 值的正态近似描述区间，不是总体性能保证；比例/率类区间裁到 `[0, 1]`，时间和能耗不裁。

## 结果

| 配置 | `margin_m` | `p_fraction` | Mock near-miss | Mock rate | utility near-miss | utility rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| strict | 0.0 | 1.00 | 0 | 0.000 | 0 | 0.000 |
| default | 0.5 | 0.50 | 300 | 0.200 | 300 | 0.200 |
| conservative | 1.5 | 0.25 | 1072 | 0.715 | 1093 | 0.729 |

可见率和重捕获时间不随 near-miss 口径改变：Mock 的 frame-weighted proxy 可见率为 `0.689`、平均重捕获时间 `1.286 s`；utility 分别为 `0.734` 和 `0.911 s`。按 episode 计算，Mock 可见率均值为 `0.689`，描述区间 `[0.502, 0.877]`；utility 为 `0.734`，区间 `[0.575, 0.893]`。重捕获时间的 episode-level 均值分别为 Mock `2.913 s`（`[-0.281, 6.107]`）和 utility `1.907 s`（`[0.048, 3.765]`），说明困难 episode 的差异远大于帧数本身带来的精度。

## 解释与限制

阈值从 strict 到 conservative 会把同一批选中动作从 0 个标成 1,072--1,093 个 near-miss，证明这类事件目前主要是风险口径的敏感性诊断，不能称为碰撞或近失事故。所有三组的 `selected_unsafe=0`；硬安全过滤仍然独立生效。后续只有在 CARLA/AirSim replay 提供真实碰撞、距离和接触标签后，才应校准这个代理阈值并把物理事件单独报告。

episode-level 区间也暴露了当前数据覆盖的主要问题：身份交叉仍为 0，5 个 episode 的 `identity_coverage.status` 都是 `NOT_AUDITED`；因此身份保持率为 1.0 不能解释为 crossing 鲁棒性。频繁转向的 Town10HD episode 贡献了最长重捕获时间，后续应优先补充真实身份交叉和自运动变化的 PASS 数据。

## 复现

```powershell
$env:PYTHONPATH = "src"
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --output artifacts\threshold_sensitivity_20261024\default.json `
  --stride 1 --max-decisions 512 `
  --near-miss-margin-m 0.5 `
  --near-miss-collision-probability-fraction 0.5
```

完整三组 JSON 保存在被 Git 忽略的 `artifacts/threshold_sensitivity_20261024/`，分别是 `strict.json`、`default.json` 和 `conservative.json`。
