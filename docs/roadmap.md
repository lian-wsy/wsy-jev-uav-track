# Jev UAV Track 长期研发计划

更新时间：2026-10-25

## 当前执行优先级：两周仿真冲刺

2026-10-25 用户明确目标：每天推进，两周内进入 CARLA/AirSim 测试并验证可发表的性能提升。当前执行计划以 [两周冲刺](sprint_20261026_20261108.md) 为准，覆盖 10 月 26 日至 11 月 8 日；下方六阶段保留为长期背景，不再按原 8 周顺序等待仿真。

优先路径：冻结数据/指标 → 独立仿真条件核验与适配 → 修正预测时间与动作语义 → 可校准 rollout → 第 7 天首个真实闭环 → 独立成对评估 → 消融与论文证据包。每天完成一个影响这条路径的交付物，通用代码清理不替代实验进展。第 3、7、10 天执行 go/no-go 检查，外部资源阻塞需当天告知。

目标是获得真实仿真结果和可审阅的论文初稿；显著提升、方法新颖性和论文录用均需验证，不能预先保证。现有采集/上传继续受保护，只允许在证实隔离的本任务仿真环境中执行实验。

当前基线已经包含 CarlaAir trimmed HDF5 适配器、跨 episode outcome calibrator、Jev/utility 选择器、安全门控、短时 history builder、身份置信度开关、6 环境模块消融报告，以及固定事件窗的运动学闭环回放。下一阶段的目标是把“候选行为后果预测 + Jev 高层选择”从离线代理推进到可复现、可诊断、可在真实仿真器中比较的跟踪算法。

## 研发原则

1. 只使用 `validation_status=PASS` 的数据进入训练和验证；训练集与验证集按 episode 完全隔离。
2. 所有候选动作先经过 rollout 和硬安全约束，Jev 不能放宽碰撞、速度、动作时长或电池约束。
3. 记录真实观测指标和预测指标两套数值，不把单条回放中的动作选择当成反事实因果标签。
4. API key 只从本机环境读取，不能进入代码、日志、报告或 Git；真实 Jev 调用只做明确的小规模实验。
5. CarlaAir 采集、上传、锁、端口和共享配置属于外部任务；本仓库的日常任务默认只读已验证 HDF5 和写入仓库产物。
6. 没有闭环仿真和安全审计之前，不把结果用于真实飞行。

## 六阶段路线

| 阶段 | 时间盒 | 目标 | 主要交付物 | 通过条件 |
| --- | --- | --- | --- | --- |
| A. 复现与数据基线 | 第 1 周 | 固定环境、数据清单和指标口径 | 可复现运行脚本、PASS manifest、基线报告、失败样本清单 | 同一输入重复运行结果一致，训练/验证无 episode 重叠 |
| B. Belief、身份与短时历史 | 第 2 周 | 让目标状态真正表达身份、遮挡、历史和自运动 | 时间窗口 belief builder、ReID/actor 关联字段、history encoder、遮挡测试样本 | 出框、遮挡、交叉和加速场景能分别计算 identity 保持率、重捕获时间 |
| C. Rollout 与候选动作 | 第 3 周 | 从常速度近似推进到可校准的短时结果预测 | action-conditioned rollout、候选覆盖率分析、概率校准、动作可达性检查 | 预测 visibility/range/covariance 在跨环境验证集上稳定，unsafe 候选不进入 Jev |
| D. Jev 集成与鲁棒性 | 第 4 周 | 验证 Jev 相对 utility 排序的价值和系统延迟 | real/mock Jev 对照、超时重试、置信度校准、延迟统计、结构化决策日志 | 能在固定帧集复现选择结果；API 异常时 fallback 可审计且不破坏安全门控 |
| E. 闭环仿真 | 第 5--6 周 | 在统一初始状态下执行候选动作并测真实后果 | CARLA/仿真 replay harness、闭环指标、轨迹与安全事件记录 | 每个场景重复运行可比较；目标可见率、身份保持、重捕获和近失事件均可统计 |
| F. 论文级实验与发布 | 第 7--8 周 | 完成统计重复、消融和可复现材料 | 全模块消融、置信区间、图表、技术报告、运行说明、版本标签 | 结果、数据选择、失败案例和限制均可由第三方按 README 重现 |

## 每周的固定节奏

- 周一：检查仓库状态、数据 manifest、依赖和上周失败项，确定本周唯一主线。
- 周二：实现一个小而完整的代码增量，并为接口或边界条件补测试。
- 周三：在 PASS 数据上运行 targeted experiment，保存 JSON 和日志摘要。
- 周四：检查跨环境差异、失败样本和指标是否被数据偏差掩盖。
- 周五：整理报告、更新本路线图的状态、提交并推送可审阅版本。

## 每日执行协议

每日任务只做一个可验收切片，顺序如下：

1. 读取本路线图、`git status`、最近报告和当前未解决项。
2. 选择当前阶段最小的一个交付物；如果依赖数据或外部服务不可用，先完成不依赖它的代码、测试或文档。
3. 修改代码后运行相关单元测试、编译检查和最小离线实验。
4. 把结果写入 `artifacts/` 或 `docs/experiments/`，记录样本范围、配置、指标和限制。
5. 只提交已经验证的改动并推送；失败时保留诊断信息，不伪造成功数字。

## 当前排程

当前处于 E 阶段的代理回放切片，B/C/D 的数据覆盖和接口验证仍在持续补齐。接下来依次推进：

1. **已完成第一版**：`scripts/build_pass_manifest.py` 已对本轮 12 个 episode 做只读检查，12/12 为 `PASS`、schema 统一为 `carlaair_uav_v2_candidate`，manifest 位于本机 `artifacts/module_ablation_20261022/pass_manifest.json`。
2. **已完成第一版**：`history.py` 提供有界 `TargetBeliefHistory` 和 `attach_history`，默认评估窗口为最近 8 个状态，保存目标/UAV 自运动、actor id、可见性、遮挡和 identity confidence，并计算身份一致性与目标加速度。
3. **已完成第一轮覆盖**：新增稳定跟踪、出框/遮挡代理、身份切换和目标加速测试；历史 12-episode baseline 全可见、零遮挡，早期困难 manifest 曾覆盖 3 个出框 episode 和 6 个高加速度 episode。后续全量只读审计已补出 4 个真实遮挡 PASS，但仍没有身份交叉 PASS。
4. **已完成第一版**：短时历史和身份置信度已接入 action-conditioned rollout；历史遮挡/可见性影响重捕获增益，身份不确定性影响协方差与信息增益，目标加速度进入不确定性项，并有 15 个基础测试通过。
5. **已完成第一版**：在困难 PASS episode 的固定出框事件帧上复跑 real Jev、Mock Jev 和 utility；记录选择、confidence、fallback、utility regret 和安全拒绝原因。
6. **已完成当前切片**：只读审计确认 4 个真实遮挡 PASS、0 个身份交叉 PASS；新增 `closed_loop.py`、固定窗三方对照和多 episode Mock/utility 汇总。闭环仍是运动学代理，身份交叉和 CARLA/实机动力学验证继续作为后续门槛；未满足数据和安全门槛时不启动 CARLA 采集，也不接真实飞行控制器。
7. **已完成阈值诊断切片**：闭环 near-miss 的距离余量和碰撞概率比例已参数化，汇总增加按 episode 等权的均值、标准差和描述性 95% 区间；在同一 5-episode/1500 帧 PASS 集上完成 strict/default/conservative 三组敏感性报告。结果显示代理事件从 0 到约 70% 帧率变化，不能作为真实碰撞结论；身份交叉仍为 0，继续标记 `NOT_AUDITED`。
8. **已完成覆盖复核**：2026-10-24 对 trimmed 根目录做只读复核，281/281 可读，`PASS=259`、`FAIL=5`、`PENDING=17`，PASS 身份交叉仍为 0。新增数据只记录为快照，不自动纳入训练或闭环 manifest。
9. **已完成 tracker 边界切片**：身份 tracker 现在对可见 crossing 需要连续两次新 actor 观测才确认切换，并记录 `identity_confirmed_switch_events`；新增合成 crossing 回归测试，真实数据覆盖仍维持 `NOT_AUDITED`。
10. **已完成 learned rollout 诊断**：在完全留出的 Town10HD episode 上复跑透明与 learned outcome rollout；learned 模型使 Mock/utility 的 proxy 可见率降到 `0.307`，暴露校准模型与动作执行代理的分布偏差，因此暂不把 learned 模型当作改进或部署候选。
11. **已完成 OOD 保护切片**：`LearnedOutcomeRolloutModel` 增加候选级 `max_learned_feature_zscore`、状态级 `max_learned_state_zscore` 和 `learned_prediction_blend`；逐帧记录 OOD 原因。留出实验中状态阈值 2.75 可把 Mock/utility 恢复到约 `0.470/0.463` 可见率，但 92.7%/95.3% 帧仍是状态 OOD，后续需用更多独立 episode 校准。
12. **已完成 manifest 指纹切片**：显式 PASS manifest 支持流式 SHA-256，批处理可在 HDF5 打开前验证哈希；当前 5 个对照 episode 的指纹验证通过，结果与未校验批次一致。
13. **已完成 residual rollout 切片**：新增 `target_mode=baseline_residual`，留出 range-error RMSE 从 `2.917m` 降到 `0.378m`；闭环 raw residual 仍会拉长重捕获，blend `0.25` 接近透明基线，因此默认仍保持 absolute，后续需要真实动作后果数据。
14. **已完成 target provenance 切片**：`RolloutTrainingDataset` 保存 `target_mode`，省略训练 config 时自动继承，显式 mode 不一致会拒绝拟合；新增 residual API 回归测试，防止把 residual 标签误标成 absolute。
15. **已完成 D1 split 冻结切片**：新增 `scripts/freeze_experiment_split.py`，将 12 个 baseline PASS episode 固定为 6 train/6 validation，并把已用于探索的 5 个困难 episode 单独标记为 `development_stress`；当前没有可声称的独立 final test，见 [2026-10-26 日报](experiments/daily_20261026.md)。

## 完成定义

2026-10-25：补齐单 episode 训练的 PASS 准入检查，absolute/residual 均在访问训练状态前拒绝未审核数据。34 个测试通过，一份 SHA 校验 PASS episode 的 32 样本训练 smoke 成功。见 [实验记录](experiments/pass_training_gate_20261025.md)。

长期目标不是让某一次离线报告的 utility 更高，而是形成一条能被复现和审计的闭环链路：感知与身份 belief → 短时历史与自运动 → 候选 rollout → 硬安全过滤 → Jev/utility 选择 → 飞控执行 → 可见性、身份和安全结果回写。每个模块都要有独立开关、测试、消融结果和已知限制。
