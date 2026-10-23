# wsy-jev-uav-track

一个面向无人机目标跟踪的 Jev 决策原型。系统先由本地感知模块形成目标 belief state，再对预先定义的语义行为做短时 rollout，经过硬安全约束过滤后，把候选行为及其预测后果交给 Jev 选择下一步行为。Jev 只负责高层离散选择，位置、速度和姿态仍由飞控或 NMPC 的快速内环执行。

当前仓库用于算法验证和接口设计，`KinematicRolloutModel` 是透明的常速度近似，不是经过验证的飞行动力学模型，不能直接用于真实飞行。

## 结构

```text
感知/跟踪器 -> TargetBelief + UAVState
                    |
                    v
             候选行为库（8 个语义动作）
                    |
                    v
          外部 rollout + 风险/能耗预测
                    |
                    v
                硬安全过滤
                    |
                    v
        Jev choice（结构化单选 + 概率）
                    |
                    v
             置信度/滞回门控与 fallback
                    |
                    v
             飞控适配器 / NMPC / PID
```

核心代码在 `src/jev_uav_track/`：

- `models.py`：目标 belief、无人机状态、候选行为、预测结果和选择结果。
- `history.py`：有界的 TargetBelief 时间窗口，保存 actor id、身份置信度、可见性、遮挡、目标/UAV 运动和加速度统计。
- `primitives.py`：保持、预测跟随、左右绕飞、升降、扫描重捕获和安全悬停等行为原语。
- `rollout.py`：开发阶段的常速度短时预测；可选读取短时 history 摘要，把遮挡、身份不确定性和目标加速传入候选 outcome。
- `safety.py`：碰撞概率、障碍物余量、速度、动作时长和电池余量检查。
- `jev_client.py`：TypeSafe Jev API 适配器、离线 `MockJevClient` 和 utility-only 对照客户端。
- `selector.py`：Jev 结果校验、低置信度 fallback、动作滞回和可配置安全门控。
- `pipeline.py`：把 rollout、过滤和选择串成一次闭环步骤。
- `closed_loop.py`：在固定 PASS 事件窗上更新独立 UAV 运动学状态，按记录可见性和几何 FOV 代理统计重捕获、身份保持和安全事件。

## 本地运行

```powershell
cd "D:\WPS Cloud\WPSDrive\1574147233\WPS云盘\wsy-jev-uav-track"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m jev_uav_track.demo
pytest -q
```

HDF5 适配和离线实验需要数据依赖。建议在 CarlaAir 的 Python 环境中安装或直接使用已有的 `numpy`、`h5py`：

```powershell
pip install -e ".[data,dev]"
```

示例使用离线 mock，不需要 API key。若要接入 TypeSafe Jev，可在自己的感知循环中创建 `TypeSafeJevClient`：

```python
import os

from jev_uav_track import JevTrackPipeline, TypeSafeJevClient

client = TypeSafeJevClient(api_key=os.environ["JEV_API_KEY"])
pipeline = JevTrackPipeline(client)
result = pipeline.step(tracking_state, previous_action_id=last_action_id)
command = result.action.command_mps
```

也可以把本地 `.env` 按 `.env.example` 配置后运行真实接口 smoke test：

```powershell
python examples/live_jev_choice.py
```

`.env` 已被 Git 忽略，只用于本机测试；不要把真实 key 写进 Python、README、提交记录或 CI 日志。客户端默认请求 `jev-latest`，也会用账户的模型列表发现结果确认可用模型。

`command_mps` 只是高层行为意图，实际工程中应交给经过验证的轨迹生成器和飞控。不要把 Jev 输出直接映射成电机 PWM 或未经约束的姿态力矩。

## 下一步接入点

1. 用检测器、ReID、深度/光流和滤波器替换 `TargetBelief` 的示例数据。
2. 用 AirSim、Isaac Sim、Gazebo 或实机辨识模型替换 `KinematicRolloutModel`。
3. 把 `CandidateAction.trajectory` 接到 NMPC / PX4 / ArduPilot 适配器，并用真实障碍物地图计算碰撞概率。
4. 将 Jev 调用放在约 1--2 Hz 的高层循环，内层控制保持 20--100 Hz；每次动作执行短时间后重新观测和决策。
5. 用闭环指标评估：目标可见率、身份保持率、重捕获时间、距离误差、碰撞/近失事件、能耗、Jev 超时率和置信度校准误差。

算法说明见 [`docs/algorithm.md`](docs/algorithm.md)，论文和方向整理见 [`docs/research.md`](docs/research.md)。
长期研发排程见 [`docs/roadmap.md`](docs/roadmap.md)。

## CarlaAir 小规模离线实验

适配器以只读方式打开 HDF5，默认不加载约 400 MB 的 RGB 张量，只读取状态、时间戳和可见性标注。训练脚本拟合一个小型 ridge outcome calibrator，用来校准候选 rollout；它不会修改源文件，也不会启动采集器或上传器。

```powershell
$env:PYTHONPATH = "src"
python scripts/train_rollout_model.py `
  --episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261003_formal\Town05_bicycle_frequent_turns_close_seed26094435.hdf5" `
  --output artifacts\small_experiment\rollout_model.npz `
  --max-samples 128

python scripts/evaluate_offline.py `
  --episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261003_formal\Town05_bicycle_frequent_turns_close_seed26094435.hdf5" `
  --model artifacts\small_experiment\rollout_model.npz `
  --output artifacts\small_experiment\offline_jev_report.json `
  --max-frames 32
```

这项实验是离线代理评估：Mock Jev 在候选行为之间选择，报告同时保存实际可见性标注和预测 rollout 指标。单个记录回放不能证明未执行动作的反事实效果，后续需要按 episode 划分训练/验证集，并接入真实动力学或仿真器。

本次已验证的小规模运行记录见 [`docs/experiments/small_run_20261022.md`](docs/experiments/small_run_20261022.md)。

跨 episode 训练时，脚本要求每个文件的 `validation_status=PASS`，并把完整 episode 留作验证集，避免相邻帧泄漏：

```powershell
$env:PYTHONPATH = "src"
python scripts/train_cross_episode.py `
  --train-episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261003_formal\Town05_bicycle_frequent_turns_close_seed26094435.hdf5" `
  --train-episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261003_formal\Town07_bicycle_normal_motion_close_seed26094440.hdf5" `
  --train-episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261003_formal\Town10HD_bicycle_frequent_turns_long_seed26094433.hdf5" `
  --validation-episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261004_formal\Town02_bicycle_frequent_turns_long_seed26094450.hdf5" `
  --validation-episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261004_formal\Town05_bicycle_frequent_turns_long_seed26094453.hdf5" `
  --output artifacts\cross_episode\rollout_model.npz `
  --max-samples-per-episode 64
```

如果要做真实 Jev 与 Mock Jev 的小规模对照，先确认 `.env` 只在本机存在，然后只取少量记录帧：

```powershell
python scripts/compare_jev.py `
  --episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261004_formal\Town05_bicycle_frequent_turns_long_seed26094453.hdf5" `
  --model artifacts\cross_episode\rollout_model.npz `
  --env-file .env `
  --output artifacts\cross_episode\jev_comparison.json `
  --max-frames 4 --stride 75
```

该 smoke test 只把 belief state 和候选 outcome JSON 发给 Jev，不发送 RGB；报告会并列保存真实 Jev、Mock Jev 和 utility 三种客户端的动作、fallback、置信度和逐帧选择差异。使用 `--start-frame`、`--end-frame` 和 `--stride` 可以锁定一个出框/遮挡事件窗口。API key 不会写入报告、日志或 Git。

本次跨 episode 与真实 Jev 对照记录见 [`docs/experiments/cross_episode_jev_smoke_20261022.md`](docs/experiments/cross_episode_jev_smoke_20261022.md)。

困难事件帧三方对照见 [`docs/experiments/difficult_pass_three_way_jev_20261023.md`](docs/experiments/difficult_pass_three_way_jev_20261023.md)。

为了固定每次实验的输入，可以用 `build_pass_manifest.py` 对显式列出的 episode 做只读检查。它不会扫描 live 上传目录，也不会写回 HDF5：

```powershell
$env:PYTHONPATH = "src"
python scripts/build_pass_manifest.py `
  --episode "<PASS train episode>" `
  --episode "<PASS validation episode>" `
  --expected-schema carlaair_uav_v2_candidate `
  --output artifacts\manifests\pass_manifest.json
```

manifest 会保存 schema、PASS 状态、帧数、文件大小/mtime、可见性/遮挡比例和 actor id，供训练、验证和报告脚本复用。

困难场景使用显式 episode 路径生成额外 manifest，不扫描 live 目录：

```powershell
python scripts/build_difficult_pass_manifest.py `
  --episode "<PASS crossing/frequent-turns episode>" `
  --expected-schema carlaair_uav_v2_candidate `
  --acceleration-threshold-mps2 3.0 `
  --output artifacts\difficult_pass\pass_manifest.json
```

它会额外记录出框、可见性下降、遮挡、actor-ID 变化和目标加速度 p95，避免把普通全可见数据误称为困难覆盖。

评估默认把最近 8 个状态压缩成短时 history summary；每条选择记录还保存 history length、持续时间、identity consistency、遮挡比例和目标加速度。通过 `OfflineEvaluationConfig(use_short_history=False)` 可以关闭这部分上下文做模块消融。

候选 rollout 也会读取同一份 history summary：历史遮挡会提高重捕获动作的信息增益，较低身份置信度会增加协方差并鼓励扫描/绕飞/上升，目标加速会增加预测不确定性。该逻辑只影响候选后果预测，不会绕过安全过滤；没有 history 时退回当前帧兼容默认值。对应验证见 [`docs/experiments/belief_history_coverage_20261023.md`](docs/experiments/belief_history_coverage_20261023.md)。

要比较不同信息源的作用，可以运行消融报告脚本。默认包含 `kinematic_baseline`、`full`、`no_motion`、`no_action` 和 `no_visibility`；它只读取验证集 episode，并把每个模型保存到被 Git 忽略的 `artifacts/`：

```powershell
python scripts/run_ablation_report.py `
  --train-episode "<PASS episode 1>" `
  --train-episode "<PASS episode 2>" `
  --validation-episode "<different PASS episode 1>" `
  --validation-episode "<different PASS episode 2>" `
  --output artifacts\ablation\report.json
```

报告同时给出跨 episode outcome RMSE、每个验证环境的预测指标、fallback、utility regret 和动作选择分布。由于记录回放不能观测未执行动作的真实反事实后果，这些结果用于比较当前 rollout/选择器原型，不能替代闭环仿真。

本轮多环境结果见 [`docs/experiments/ablation_report_20261022.md`](docs/experiments/ablation_report_20261022.md)。

还可以固定同一个跨 episode outcome model，只消融决策模块本身。模块报告包含 `jev`、`utility`、`no_safety_gate`、`no_short_history` 和 `no_identity_confidence` 五行；默认配置适合观察 nominal 行为，另可用较高障碍余量做安全门控压力测试：

```powershell
python scripts/run_module_ablation_report.py `
  --train-episode "<PASS episode 1>" `
  --train-episode "<PASS episode 2>" `
  --validation-episode "<different PASS episode 1>" `
  --validation-episode "<different PASS episode 2>" `
  --output artifacts\module_ablation\report.json

# 仅用于门控压力对照，不改变源 HDF5
python scripts/run_module_ablation_report.py `
  --train-episode "<PASS episode 1>" `
  --validation-episode "<different PASS episode 1>" `
  --output artifacts\module_ablation\gate_stress.json `
  --minimum-obstacle-margin-m 7.8
```

`UtilityRankingClient` 是候选 utility 的确定性排序上限；`MockJevClient(identity_weight>0)` 只用于离线检验身份置信度是否能改变信息寻求倾向，真实 Jev smoke 仍通过 `TypeSafeJevClient`。模块报告会同时统计 fallback、跳帧、安全门控下的 unsafe 选择率、utility regret、可见性和各环境动作分布。

本次 6 环境模块报告见 [`docs/experiments/module_ablation_report_20261022.md`](docs/experiments/module_ablation_report_20261022.md)。

## 真实遮挡 PASS 与闭环三方回放

`scripts/audit_trimmed_pass_coverage.py` 只读审计一个明确的 trimmed HDF5 根目录，统计 PASS、遮挡、出框和身份交叉覆盖，不访问采集器、上传器或 live staging。当前已验证数据包含 4 个真实遮挡 PASS，但身份交叉 PASS 为 0；代码和报告会把单 actor episode 的身份保持率标为 `NOT_AUDITED`，不会把它当成交叉结论。

`scripts/run_closed_loop_replay.py` 在同一固定帧窗顺序运行 Mock Jev、utility 和真实 Jev。它把记录目标轨迹作为只读参考，用选中高层动作更新独立 UAV 常速度状态，再用记录可见性/遮挡和几何距离/FOV 代理构造下一次 belief。它不是 CARLA/ AirSim/实机动力学仿真，安全事件仅表示回放中预测的 unsafe/near-miss 事件。

```powershell
$env:PYTHONPATH = "src"
python scripts/audit_trimmed_pass_coverage.py `
  --root "D:\CarlaAir\data\trimmed\v3" `
  --output artifacts\coverage_audit_20261023.json

python scripts/run_closed_loop_replay.py `
  --episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20260930\Town10HD_bicycle_normal_motion_long_seed260922365.hdf5" `
  --model artifacts\occlusion_pass_20261023\models\rollout_model.npz `
  --env-file .env --start-frame 168 --end-frame 225 --stride 8 `
  --output artifacts\occlusion_pass_20261023\closed_loop_three_way.json
```

本轮结果见 [`docs/experiments/closed_loop_occlusion_replay_20261023.md`](docs/experiments/closed_loop_occlusion_replay_20261023.md)。

