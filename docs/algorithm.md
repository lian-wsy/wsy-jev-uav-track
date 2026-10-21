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

