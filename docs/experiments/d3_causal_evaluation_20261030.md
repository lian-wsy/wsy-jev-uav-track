# D3 因果训练模型的离线评估输入修复

2026-10-30。接续 `b781a7b` 的因果训练工作。本次发现训练与评估的输入契约没有贯通：`evaluate_episode()` 总是调用 oracle `tracking_state()`，短时历史也从 oracle 状态构建。加载 causal_visible 权重并不会改变这一行为。

## 复现与修复

先写回归并运行：5 failed / 1 passed。修改不可见帧的目标位置、距离和身份，以及目标真值速度后，旧评估的预测可见率从约0.901变为0，历史身份一致性从1变为0.6。这证明评估仍能使用隐藏真值；不是训练权重发生变化。

现在评估默认 `input_mode=auto`，从模型 `config/dataset/train_dataset` 声明解析输入模式；互相冲突或显式不匹配即拒绝。无模式声明的旧模型按旧oracle解释，并在报告中标明。无模型时可显式选择 `causal_visible`。

因果模式复用训练的可见性门控估计器，从第0帧顺序消费所有过去观测，抽样只控制评估帧，不截断估计器的历史；短时历史也只由这些因果状态构建。报告中的实际标注在选择结束后读取，不作为选择输入。缺少显式可见性/相机位姿来源时拒绝。未观测电量、间距不补成乐观值；无安全候选的帧写入 `blocked_records`，不再只给一个空记录列表。

CLI新增 `--input-mode auto|oracle|causal_visible`。这是修复离线评估入口，不是所有独立脚本或RGB感知的无泄漏认证。旧oracle模型仍保留其限制，不自动重写旧实验结果。

## 已验证数据上的最小复现

使用冻结开发manifest中的首个validation条目，不按结果挑选：

- 文件：`Town02_bicycle_frequent_turns_long_seed26094451.hdf5`，300帧，322642409字节。
- 完整SHA256：`6bb267360cde0eab6b379d5206ba6e88e4233ee7e46a9d2cb30b3b3bbab63307`。
- 复验使用 `scripts.run_d4_rollout.load_verified(entry, 16.)`：只读、16MiB/s限速，SHA/大小/PASS/帧数及读取前后mtime一致。
- 模型：`artifacts/d3_causal_training_20261030/baseline_residual.npz`，SHA256 `a3542d8e3f2730f8c0a597fa83b3c9fc225c50e147aa047c7b56f5f0fd01b560`，未重训或替换。
- 固定帧0、75、150、225；自动识别causal_visible，历史长度1、8、8、8。
- 四帧全部因无安全候选被拒绝，报告缺少 `minimum_clearance_m`、`battery_percent`。0次选择、0次真实Jev请求、0次动作；不能将此结果称为跟踪收益。
- 结果保存在本机 `artifacts/d3_causal_evaluation_20261030/report.json`；未读取stress或final test。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python scripts/evaluate_offline.py --episode D:/CarlaAir/data/trimmed/v3/quota_grid_20261004_formal/Town02_bicycle_frequent_turns_long_seed26094451.hdf5 --model artifacts/d3_causal_training_20261030/baseline_residual.npz --output artifacts/manual_causal_report.json --max-frames 4 --stride 75
```

针对性测试53 passed；全套385 passed / 1 skipped，编译与diff检查通过。独立只读代码审查没有阻断项；保存重载后的真实cross-episode模型已由上述CLI复现覆盖。

## D1的实际依赖

本轮只读检查外部 `D:/CarlaAir/repos/carlaair-research/src/capture/run_independent_uav_episode.py`：地图参数不含Town03，且约1309行直接连接CARLA 2000端口。文件SHA256 `b8268059b824f992ce4650ecfccb28ffdb057d0dbc93a810f4176ffda10f5524`。因此不能通过传一个新地图参数直接复用它接入本任务3100端口的独立实例，也没有修改或运行外部采集器。

现有HDF适配器要求同快照图像/状态时间相差不超过1微秒；本任务异步RPC观测的时间窗口不是同快照证据，不能改时间戳强行写成PASS HDF。需要任务独立采集路径及对应真实时间契约，再审核接纳。另一聊天仍保留仿真实例运行权；本轮没有启动、停止、解锁或修改仿真/采集/上传。Town03 PASS仍0，D1–D3整体验收仍未完成。
