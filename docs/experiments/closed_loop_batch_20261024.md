# 多 episode 闭环代理回放汇总

日期：2026-10-24

本轮在上一轮三方固定事件窗之外，增加了一个不调用网络的批量回放切片。脚本读取上一轮的显式 PASS manifest，逐 episode 运行 Mock Jev 和 utility，使用每帧采样（`stride=1`）和同一运动学/FOV 代理。真实 Jev 仍只在上一轮固定 8 帧窗口中调用，避免把批量离线统计变成未经控制的 API 压测。

运行入口：[`run_closed_loop_batch.py`](../../scripts/run_closed_loop_batch.py)

## 输入与结果

- manifest：`artifacts/occlusion_pass_20261023/pass_manifest.json`
- episode：5 个，全部 `validation_status=PASS`，共 1500 个决策帧
- 其中 4 个含真实遮挡，身份交叉覆盖仍为 0；五个 episode 的 `identity_coverage.status` 均为 `NOT_AUDITED`
- 配置：`stride=1`、`max_decisions=512`、最小障碍余量 3 m、FOV 半角 75°；未加载训练模型，使用透明的基础运动学 rollout

| 客户端 | proxy 可见率 | 平均重捕获时间 (s) | 重捕获次数 | 身份保持率* | safety events | selected unsafe | fallback 率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mock Jev | 0.689 | 1.286 | 37 | 1.000 | 300 | 0 | 0.768 |
| utility | 0.734 | 0.911 | 47 | 1.000 | 300 | 0 | 0.000 |

\* 身份保持率是 sticky actor-id 在当前 episode 的记录帧上的保持率；所有 episode 都只有一个 actor id，不能说明身份交叉鲁棒性。

Mock Jev 的 fallback 较多，来自离线置信度门控；utility 没有 Jev 置信度门控。两者的 `selected_unsafe` 都为 0，但 `safety events=300` 主要由当前保守 near-miss 代理阈值触发，不能解读为真实碰撞事件。这个结果用于暴露阈值和场景分布影响，后续需要真实仿真器中的碰撞/近失标签校准阈值。

四个遮挡 episode 的代理重捕获时间差异较大，Town10HD frequent-turns episode 仍是主要困难来源；出框/几何 FOV 丢失和记录遮挡在当前指标中合并为 proxy visibility，报告保留了 `annotated_occluded` 与 `geometry_in_frame` 字段用于拆分。

完整 JSON 保存在本机被 Git 忽略的 `artifacts/occlusion_pass_20261024/mock_utility_batch.json`。源 HDF5 只读打开，没有启动或修改 CarlaAir 采集、上传、锁、端口或共享配置。

## 复现

```powershell
$env:PYTHONPATH = "src"
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --output artifacts\occlusion_pass_20261024\mock_utility_batch.json `
  --stride 1 --max-decisions 512
```
