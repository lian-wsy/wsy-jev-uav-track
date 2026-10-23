# Jev-Track 算法设计

## 1. 决策边界

Jev-Track 把无人机动作分成两个时间尺度：

- 快速内环（20--100 Hz）：跟踪给定轨迹，处理姿态、速度、限幅和紧急避障。
- 高层闭环（约 1--2 Hz）：根据最新 belief state，从有限的语义行为集合中选择下一段动作。

Jev 不直接输出电机量、连续力矩或未经验证的轨迹。代码负责预测每个行为会发生什么，Jev 只比较这些候选后果并返回一个结构化 choice。

## 2. 状态和候选行为

每个周期由感知与估计模块生成：

```text
TargetBelief:
  position_m, velocity_mps, range_m, bearing_deg
  image_center_error, covariance_trace
  visibility_probability, occlusion_risk, identity_confidence

UAVState:
  position_m, velocity_mps, yaw_deg
  battery_percent, minimum_obstacle_margin_m
  gps_health, communication_health
```

候选行为是可解释的技能，例如 `follow_predictive`、`orbit_left`、`orbit_right`、`climb`、`scan_reacquire` 和 `safe_hover`。候选数量应保持在能由飞控可靠执行、且能覆盖主要跟踪情形的范围内。

动作原语的上下文偏好也会进入 rollout：`scan_reacquire` 只有在可见度低于阈值时获得重捕获增益，目标已经清晰时反而承担轻微的重复扫描代价。这样可以减少“目标已稳定却持续扫描”的策略抖动。

## 3. 外部 rollout

对每个候选行为 (a_i)，在时间窗 (H) 内用动力学或仿真模型预测：

```text
visibility_probability
image_center_error
range_error_m
covariance_trace
collision_probability
minimum_obstacle_margin_m
energy_cost_wh
information_gain
```

仓库中的基线 utility 为：

```text
U(a) = 3.0 P_visible
       - 1.8 e_image
       - 0.08 e_range
       - 0.12 trace(Sigma)
       + 0.8 I_gain
       - 0.5 E
       - 8.0 P_collision
       + safety_bonus
```

这些权重只是可复现实验的起点，应在统一场景、统一初始状态和真实任务代价下标定。Jev 的输入同时包含结构化的预测结果和每个动作的自然语言描述，避免模型自行臆造未模拟的动作。

## 4. 安全和选择门控

先执行不可由 Jev 放宽的硬约束：最大碰撞概率、最小障碍物余量、最大速度、最大动作时长和电池返航余量。被拒绝的候选不会进入 Jev 的 choice 集合。

Jev 返回后再做三项检查：

1. choice 必须对应一个仍然安全的候选 id。
2. 概率最高项和前两项的概率间隔必须达到阈值。
3. confidence 低于阈值、接口超时、响应格式错误时，使用透明的 utility 排序作为 fallback。

如果新动作与上一动作的概率差距很小，且 utility 没有明显优势，则保持上一动作，降低高层决策抖动。所有 fallback 和拒绝 id 都保留在 `SelectionResult` 中，便于日志和离线审计。

当 Jev 低置信度或接口异常时，代码会保留原始 Jev decision（choice、概率、confidence 和 model），同时记录实际执行的 deterministic fallback，避免把模型的不确定性从日志中抹掉。上一动作 id 也会追加到下一次 Jev state 的 history 中，让模型看到短期动作上下文。

## 5. 实机闭环伪代码

```python
while mission_is_running:
    observation = sensor_stack.read()
    belief = tracker.update(observation)
    state = TrackingState(target=belief, uav=vehicle.state(), ...)

    candidates = rollout_model.generate(behavior_library, state)
    result = selector.select(state, candidates, previous_action_id)

    trajectory = flight_adapter.compile(result.action)
    controller.execute_for(result.action.duration_s, trajectory)
    previous_action_id = result.action.id
```

在第一次实机测试前，必须用仿真回放验证动作可达性、碰撞概率估计和异常 fallback；还要记录 Jev 延迟、失败率、选择概率和最终闭环指标。

## 6. CarlaAir 数据适配和离线校准

`jev_uav_track.data.HDF5Episode` 读取已验证的 CarlaAir trimmed HDF5。它把 `image_state/state_index` 对齐到 `state/positions` 和 `action/camera_positions`，用时间戳有限差分得到目标和 UAV 速度，并将 `visibility/target_visible`、`target_in_frame`、`target_occluded` 和像素高度映射为 `TargetBelief`。RGB 默认不加载，避免离线状态实验占用大块内存。

`training.py` 的 ridge calibrator 使用观测到的相机速度作为动作特征，预测未来可见率、中心误差、距离误差和协方差代理。它是校准基线，不是反事实世界模型；`learned_rollout.py` 只替换这些预测量，碰撞概率、障碍余量和能耗仍由安全基线提供。

`evaluation.py` 在记录帧上运行 Mock Jev，报告选择动作、fallback 率、与候选 utility oracle 的 regret，以及真实可见性标注。评估报告中的真实标注和预测后果必须分开解释，不能把单条记录中的动作选择当成因果训练标签。

跨 episode 训练使用 `build_training_dataset_from_episodes` 和 `fit_outcome_model_cross_episode`：每个文件先独立生成样本，再拼接训练数组；验证集由完全不重叠的 episode 组成，并强制检查 HDF5 的 `validation_status=PASS`。`compare_jev.py` 在同一组记录帧上分别调用 Mock Jev 和真实 `TypeSafeJevClient`，只发送结构化状态与候选后果，输出逐帧 choice、fallback、confidence 和选择一致率。

消融实验通过固定训练/验证 episode，只改变输入信息：`no_motion` 移除目标和 UAV 速度，`no_action` 移除候选动作指令，`no_visibility` 移除可见性/遮挡/画面中心误差，`kinematic_baseline` 完全不使用校准模型。每个消融模型会保存自己的输入 mask，推理时同样屏蔽对应特征，避免训练和评估口径不一致。

### 6.1 决策模块消融

模块消融固定同一个 `full` outcome calibrator，只改变选择层：

| 变体 | 改变 | 预期回答的问题 |
| --- | --- | --- |
| `jev` | identity-aware Mock Jev + 安全门控 + 上一动作短历史 | 完整高层选择链路的离线代理表现 |
| `utility` | 用 `UtilityRankingClient` 直接取 utility 最大候选 | Jev 语义选择相对透明 utility 排序带来什么变化 |
| `no_safety_gate` | 允许 Jev 看到并选择未通过硬约束的候选 | 安全门控对跳帧和 unsafe 选择率的贡献 |
| `no_short_history` | 不发送上一动作，并关闭滞回保持 | 短时动作上下文对抖动/fallback 的贡献 |
| `no_identity_confidence` | 将 identity confidence 中性化为 0.5 | 身份不确定时，信息寻求偏好是否改变 |

安全门控始终先于 Jev；`no_safety_gate` 只绕过候选过滤，仍按原任务约束记录 `selected_unsafe`，因此不会把越界动作误报为安全。离线 Mock 的 `identity_weight` 是实验用代理，真实接口仍从 `TargetBelief.identity_confidence` 序列化该字段。若验证集全部可见且身份置信度均为 1，身份消融结果相同是数据覆盖不足的证据，而不是模块已被证明无效。

### 6.2 短时 history builder

`history.py` 将最近 `N` 个 `TrackingState` 转成 `TargetBeliefHistory`。每个条目显式保存：

```text
timestamp、target_actor_id
target position / velocity / visibility / occlusion / identity confidence
UAV position / velocity / yaw
```

窗口还提供 `identity_consistency`、`occlusion_fraction`、`target_acceleration_mps2`、目标/UAV 速度和可序列化摘要。摘要会进入 Jev 的 `history_summary`，完整窗口只保留在本地 state/environment 中，避免把源文件路径等 provenance 字段发送给远端模型。窗口构建器已覆盖稳定跟踪、出框/遮挡代理、身份切换和目标加速的合成测试；当前 12 个 PASS episode 仍然是全可见、零遮挡，因此真实数据上的 history/identity 指标还没有压力样本。

