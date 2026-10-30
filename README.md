# wsy-jev-uav-track

一个面向无人机目标跟踪的 Jev 决策原型。系统先由本地感知模块形成目标 belief state，再对预先定义的语义行为做短时 rollout，经过硬安全约束过滤后，把候选行为及其预测后果交给 Jev 选择下一步行为。Jev 只负责高层离散选择，位置、速度和姿态仍由飞控或 NMPC 的快速内环执行。

当前仓库用于算法验证和接口设计，`KinematicRolloutModel` 是透明的常速度近似，不是经过验证的飞行动力学模型，不能直接用于真实飞行。

2026-10-28：本机独立 CARLA/AirSim 副本已完成真实 API、静态地标配准、限速上升、四轴和约 19.87 Hz 客户端命令刷新诊断；标准起飞失败和双时钟偏差均保留。尚不是目标跟踪闭环或性能提升证据，见 [完整联验报告](docs/experiments/isolated_carla_smoke_20261028.md)。

## 结构

最新现场诊断已接通可见oracle→因果belief/8帧历史→rollout→硬过滤：16/16观测通过，目标出框时不查位置，恢复后重新观测。缺少实测障碍/电量，两条选择流程全部安全拒绝，未执行模型动作；205测试通过，详见 [报告与实际画面](docs/experiments/live_belief_20261028.md)。这不是视觉跟踪或Jev效果对照。

后续坐标联验发现GLOBAL物体API与vehicle-local状态API存在约2.805m竖直原点差，已加入显式桥接并现场验证；185项测试通过，仍不代表跟踪闭环完成，见 [原点联验报告](docs/experiments/live_origin_20261028.md)。

独立实例现已进一步验证真实图像/状态的时间门控和过期动作拒绝，并解决相机自身旋翼遮挡；12/12现场读取通过、167项回归通过，仍不是目标跟踪效果结论，见 [观测联验报告](docs/experiments/live_observation_20261028.md)。

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

手动体验入口及当前实时跟踪限制见[体验说明](docs/manual_experience.md)。2026-10-30因果模型的离线评估已按模型元数据自动选择输入契约；修复、真实validation最小复现及门控拒绝结果见[D3评估报告](docs/experiments/d3_causal_evaluation_20261030.md)。

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
from jev_uav_track import JevTrackPipeline, TypeSafeJevClient

client = TypeSafeJevClient.from_environment()
pipeline = JevTrackPipeline(client)
result = pipeline.step(tracking_state, previous_action_id=last_action_id)
command = result.action.command_mps
```

真实接口仅从进程环境读取 `JEV_API_KEY`；还须设置 `JEV_BUDGET_LEDGER` 指向经完整历史核对的同一准备期账本。账本初始化、限制及当前阻塞见 [调用预算](docs/experiments/jev_budget_20261028.md)。条件满足后可运行：

```powershell
python examples/live_jev_choice.py
```

脚本不再读取 `.env`，不接受 `--env-file`；不要把真实 key 写进文件、提交记录或日志。准备期每批最多8次、累计32次，失败也计数；账本缺失、无效或额度耗尽时拒绝联网。示例的模型发现也计一次。当前历史attempt尚未核清，不创建正式账本、不自动调用真实服务。客户端默认 `jev-latest`，正式比较还需固定版本。

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

2026-10-31：新增固定6/12/18集、linear/RFF64模型与校准修正比例对照。18集RFF64在同一开发validation上的距离预测RMSE从baseline 0.243116m降至0.199700m，5集改善、1集保持基线；这只是记录动作预测，未验证闭环或Jev收益，也未自动部署。全部组合与复现见[优化报告](docs/experiments/rollout_optimization_20261031.md)。

D5校准入口现在默认使用 `causal_visible`，拟合、校准和候选诊断保持一致；原18/10/6划分已完整重跑，结果仍为全部回退、无learned收益。数值、复现命令和限制见[D5因果校准报告](docs/experiments/d5_causal_calibration_20261030.md)。复现旧D5实验须显式指定 `--input-mode oracle`。

训练输入契约：历史命令默认 `--input-mode oracle`，用于复现旧记录动作校准；新因果训练请在 `train_rollout_model.py`、`train_cross_episode.py` 或 `run_d4_rollout.py` 显式传 `--input-mode causal_visible`。该模式只在明确可见且在框、不遮挡时接收目标位置/身份，用过去可见观测估计速度；未来真值仅作标签。缺少可见性或相机位姿来源就拒绝，模型摘要保存模式，不覆盖旧权重。这仍是可见标注辅助输入，不是RGB视觉跟踪。协议和验证见[D3因果训练记录](docs/experiments/d3_causal_training_20261030.md)。

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

如果要让模型只学习相对透明 rollout 的修正量，可以使用 residual target；推理时 `LearnedOutcomeRolloutModel` 会自动把修正量加回当前候选的 baseline：

```powershell
python scripts/train_cross_episode.py `
  --train-episode "<PASS train episode>" `
  --validation-episode "<different PASS validation episode>" `
  --target-mode baseline_residual `
  --output artifacts\cross_episode\residual_rollout_model.npz
```

该模式仍然只利用记录中的相机速度，不能替代动作执行后的反事实动力学数据。

如果要做真实 Jev 与 Mock Jev 的小规模对照，先满足上述环境变量与账本条件，再只取少量记录帧：

```powershell
python scripts/compare_jev.py `
  --episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20261004_formal\Town05_bicycle_frequent_turns_long_seed26094453.hdf5" `
  --model artifacts\cross_episode\rollout_model.npz `
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

需要固定文件内容时，在显式 episode 列表上加 `--sha256`；批处理可用 `--verify-manifest-sha256` 在打开 HDF5 前流式校验摘要。这个校验只保护输入可复现性，不会把 PENDING/FAIL 文件升级为 PASS。

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
  --start-frame 168 --end-frame 225 --stride 8 `
  --output artifacts\occlusion_pass_20261023\closed_loop_three_way.json
```

本轮结果见 [`docs/experiments/closed_loop_occlusion_replay_20261023.md`](docs/experiments/closed_loop_occlusion_replay_20261023.md)。

如果要在同一份 PASS manifest 上做多 episode 的离线闭环汇总，可以运行：

```powershell
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --output artifacts\occlusion_pass_20261024\mock_utility_batch.json `
  --stride 1 --max-decisions 512
```

该批处理只运行 Mock Jev 和 utility，不发起网络请求；真实 Jev 仍通过固定小窗口脚本单独对照。批量结果见 [`docs/experiments/closed_loop_batch_20261024.md`](docs/experiments/closed_loop_batch_20261024.md)。

near-miss 只是当前运动学回放的风险代理，阈值可以显式调整；报告还会给出按 episode 等权的均值、标准差和描述性 95% 区间：

```powershell
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\occlusion_pass_20261023\pass_manifest.json `
  --output artifacts\threshold_sensitivity_20261024\default.json `
  --near-miss-margin-m 0.5 `
  --near-miss-collision-probability-fraction 0.5
```

完整阈值敏感性记录见 [`docs/experiments/closed_loop_threshold_sensitivity_20261024.md`](docs/experiments/closed_loop_threshold_sensitivity_20261024.md)。

如果传入 `--model`，批处理会加载跨 episode outcome calibrator；应优先用完全留出的 episode 评估。当前模型的留出对照结果见 [`docs/experiments/closed_loop_rollout_model_comparison_20261024.md`](docs/experiments/closed_loop_rollout_model_comparison_20261024.md)，它仍是研究基线，不是已验证动力学模型。

对明显的训练分布外候选，可以设置 `--max-learned-feature-zscore`；如果观测状态本身分布外，设置 `--max-learned-state-zscore` 会让该帧所有候选回退透明 rollout。门控只防止外推，阈值必须在独立 episode 上校准；`--learned-prediction-blend` 可用于保守混合预测。

