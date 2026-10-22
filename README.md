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
- `primitives.py`：保持、预测跟随、左右绕飞、升降、扫描重捕获和安全悬停等行为原语。
- `rollout.py`：开发阶段的常速度短时预测。
- `safety.py`：碰撞概率、障碍物余量、速度、动作时长和电池余量检查。
- `jev_client.py`：TypeSafe Jev API 适配器和离线 `MockJevClient`。
- `selector.py`：Jev 结果校验、低置信度 fallback 和动作滞回。
- `pipeline.py`：把 rollout、过滤和选择串成一次闭环步骤。

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

