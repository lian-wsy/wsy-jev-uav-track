# 困难 PASS 覆盖与三方固定帧对照

日期：2026-10-23

本次从 trimmed HDF5 中显式选择困难 episode，建立只读 PASS manifest，并在同一组候选 outcome 上比较 Mock Jev、真实 Jev 和 utility。真实 Jev 只收到结构化 belief/history/candidate JSON，没有发送 RGB。

## 困难 PASS 覆盖

manifest：`artifacts/difficult_pass_20261023/pass_manifest.json`

- episode 数：6
- `validation_status=PASS`：6/6
- schema：`carlaair_uav_v2_candidate`
- 每 episode：300 帧
- `visibility_drop`：3/6
- `out_of_frame`：3/6
- `high_acceleration`：6/6，阈值为目标加速度 p95 ≥ 3.0 m/s²
- `occlusion`：0/6
- `identity_change`：0/6

因此这次确实补上了出框和运动突变覆盖，但仍没有遮挡或身份交叉证据。manifest 由 [`build_difficult_pass_manifest.py`](../../scripts/build_difficult_pass_manifest.py) 通过适配器只读生成；脚本只接受显式 episode 路径，不扫描或写回采集目录。

训练使用其中 4 个 episode，验证使用另外 2 个 episode。验证 outcome 校准误差为：visibility RMSE **0.1515**、image-center RMSE **0.1594**、range-error RMSE **2.750 m**、covariance RMSE **0.1515**。这仍是离线 outcome calibrator，不是反事实动力学模型。

## 固定事件帧

对验证 episode：

```text
Town10HD_bicycle_frequent_turns_long_seed26094434.hdf5
```

固定采样参数为 `start_frame=198`、`end_frame=211`（exclusive）、`stride=4`，得到帧 `198, 202, 206, 210`。四帧的真实标注均为 `target_visible=false`、`target_in_frame=false`、`target_occluded=false`，因此它们是出框/不可见事件，不是遮挡事件。

## 三方结果

| 客户端 | 评估帧 | fallback 率 | utility regret | unsafe 选择率 | 选择分布 |
| --- | ---: | ---: | ---: | ---: | --- |
| Mock Jev | 4 | 1.00 | 0.000 | 0.00 | `scan_reacquire` 4 |
| utility | 4 | 0.00 | 0.000 | 0.00 | `scan_reacquire` 4 |
| 真实 Jev `jev-1.14.0` | 4 | 0.00 | 0.0334 | 0.00 | `descend` 4 |

逐帧选择：

| 帧 | Mock Jev | 真实 Jev | utility | 真实 Jev confidence |
| ---: | --- | --- | --- | ---: |
| 198 | `scan_reacquire` | `descend` | `scan_reacquire` | 0.61 |
| 202 | `scan_reacquire` | `descend` | `scan_reacquire` | 0.71 |
| 206 | `scan_reacquire` | `descend` | `scan_reacquire` | 0.68 |
| 210 | `scan_reacquire` | `descend` | `scan_reacquire` | 0.67 |

选择一致率为：Mock–utility **1.00**、Mock–真实 Jev **0.00**、真实 Jev–utility **0.00**。四帧都通过硬安全门控，真实 Jev 请求和响应均成功，未发生 API fallback。

## 解释边界

真实 Jev 选择 `descend` 并不代表它已经恢复了目标，也不代表 utility 的扫描动作在闭环中一定更好；本实验没有执行候选动作，不能观测反事实可见性、重捕获时间或身份保持率。下一步应在闭环仿真中执行同一事件状态的候选动作，并在新增遮挡/身份交叉 PASS 数据后重复三方比较。

完整 JSON（含三方逐帧记录、概率和模型响应元数据）保存在被 Git 忽略的 `artifacts/difficult_pass_20261023/jev_mock_utility_event_frames.json`，API key 未写入文件。
