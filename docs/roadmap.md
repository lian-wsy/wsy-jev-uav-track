# Jev UAV Track 长期研发计划

更新时间：2026-10-30

**10/30最新优先级：先补D1–D3原验收。** 逐项状态见[优先复核](experiments/d1_d3_priority_20261030.md)，真实Jev小规模调用已有[独立记录复核](experiments/d2_real_jev_smoke_review_20261030.md)。正在补旧oracle训练与live可见belief不一致的输入契约，见[D3因果训练协议](experiments/d3_causal_training_20261030.md)。D1 Town03 PASS尚缺；不以D6/D7视频替代，也不把旧oracle模型改名为causal。

**当前授权范围（覆盖下方历史排程）：** 用户 10/29 将本轮任务收敛为补齐 D1–D5，10/30 继续执行。不得自动进入 D6–D14。D5 校准与因果复核见 [10/30 日报](experiments/daily_20261030.md)；下方两周/长期路线仅为历史背景，未满足项不得按日期记通过。

2026-10-29 审计修正：首版“文件存在即PASS”判定已撤回；v2仅读公开证据、文档项记REQUIRES_REVIEW，并重放完整16帧belief。原Python3.10重放通过，3.14文本差异保留，整体仍PARTIAL。见[日报](experiments/daily_20261029.md)。下条为首版记录。

2026-10-29 复查：新增 `scripts/audit_d1_d3_acceptance.py`，从已提交证据逐项生成 `artifacts/acceptance_20261029/d1_d3_audit.json`。D1 开发 split/指标、D2 独立 runtime/origin/budget、D3 坐标/时间/horizon/隐藏真值隔离分别通过；独立 final-town PASS 仍缺，真实 Jev 请求仍为 0，现场 tracking action 保持未审计。详见 [日报](experiments/daily_20261029.md)。

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

**2026-10-30 D5与因果复核：** 已修复整段最小障碍间距泄漏；34/34 PASS/SHA只读复验，18fit/10calibration/6validation完成区间诊断。距离区间总宽5.804m，576记录动作/4608候选均100%回退，等价透明基线；raw residual距离RMSE0.233791m略劣于透明0.230013m。遮挡/出框验证样本0，NOT_EVALUATED；无真实Jev、仿真或模型动作。287测试通过/1跳过，详见[D5报告](experiments/d5_calibration_20261030.md)与[因果审计](experiments/causal_audit_20261030.md)。当前范围仍D1–D5，未整体完成。

**2026-10-29 Town03 API-only支持：** 显式地图选择与实际地图双重检查已实现，267测试通过/1跳过；保留地图动作profile在启动前拒绝。首轮请求因RAM约3.09GiB未达到原4GiB门槛而未启动，失败证据保留。资源满足后再做有界API检查，不把未加载地图当PASS数据。见[预检查报告](experiments/town03_api_preflight_20261029.md)。原目标active，D1–D3整体未完成。

**2026-10-29 Town03保护落地：** 经用户批准，训练与开发split入口拒绝可识别Town03来源，保留只读审核；独立审查补齐baseline-only消融与记录动作对照入口，250测试通过、1符号链接权限用例跳过，39集开发元数据通过且未打开源HDF。来源被剥离的匿名数组等边界明确保留。见[报告](experiments/town03_guard_20261029.md)。此项补齐D1防误用能力，不是新增PASS测试数据；后续D5及开发地图执行链按原计划推进。

**2026-10-29 Town03选择：** 用户指定Town03作为独立测试地图，已登记[保留协议](town03_holdout_protocol_v1.md)，不进入训练/调参。独立副本地图资产存在且记录SHA；本次可用RAM约2.75GiB，未达4GiB启动门槛，不启动仿真。尚无Town03 PASS数据，不把“已选地图”当D1通过。D5离线校准及开发地图上的D6/D7不再等待测试集或真实Jev对账；正式Town03测试待方法冻结及PASS接纳。

**2026-10-29 D4最新：** 扩展至28 train/6 validation，34/34 SHA验证、2688/576样本、同约1秒预测时域；透明/absolute/residual距离RMSE=0.230013/1.539928/0.222220m。残差平均仅改善3.39%，逐集3改善/3退化；5/8候选类别动作越界，缺高遮挡输入，不能当闭环收益。217测试通过。下一项D5校准与回退诊断；D1独立test、历史Jev对账及真实动作后果数据缺口保留。见 [D4报告](experiments/d4_rollout_20261029.md)。以下为历史检查点。

**2026-10-28 belief续作：** 独立实例接通可见oracle→因果belief→8帧历史→rollout→硬过滤，16/16观测通过，12可见/4出框且恢复；隐藏时0次位置查询。因缺障碍/电量测量，两条选择流程全部安全拒绝，未执行模型动作，不是方法效果比较。205测试通过、原Python3.10严格离线重放通过，跨Python文本舍入差异明确拒绝/标记。见 [现场报告](experiments/live_belief_20261028.md)。D1独立test及正式受限执行仍未完。

**2026-10-28 原点续作：** 实测GLOBAL object NED与vehicle-local NED相差2.80465m竖直偏移，新增显式桥接，防止静态地标标定误用于无人机state；185测试通过，现场两姿态验证及12/12时间窗口通过，保留失败/资源拒绝尝试。D1独立test及D3正式可见测量→belief→selector仍未完。见 [原点联验](experiments/live_origin_20261028.md)。

**2026-10-28 观测续作：** 已补不同域时钟/图像时间括号与200ms过期拒绝，12/12真实读取接纳，新鲜动作确认而延迟动作未进入运动RPC；相机平移和朝向通过，目视旋翼遮挡消除。167测试通过。下一项是可见测量→因果belief→utility的正式接入；D1独立test仍缺，不把固定诊断指令当跟踪闭环。见 [观测联验](experiments/live_observation_20261028.md)。

**2026-10-28 最新：前三天尚未整体验收，但 D2 独立仿真可行性已通过。** 用户授权使用空闲本机后，完整独立副本完成 API/settings/所有权/清理核验，以及坐标、双时钟、限速上升、四轴和约19.87Hz客户端刷新。145测试通过。标准起飞False、双时钟非锁步、约16Hz遥测保留为限制；D1独立town test仍缺，D3 live adapter接入仍未完成。下一切片是任务内相机外参与带时间边界的真实观测/动作链，不再等待“有没有可用仿真器”。见 [联验报告](experiments/isolated_carla_smoke_20261028.md) 和 [逐条验收](experiments/d1_d3_acceptance_20261028.md)。以下为历史进展，旧阻塞以最新检查点为准。

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
16. **已完成 D2 条件核验切片**：只读确认本机有 CarlaAir v0.1.7 Windows 发行目录，但没有证明本任务拥有独立实例、端口和输出目录；新增无副作用 `sim_adapter.py` 契约与 4 个回归测试，D2 live simulator 仍标记 `BLOCKED_PENDING_RESOURCE_OWNERSHIP`，见 [2026-10-27 日报](experiments/daily_20261027.md)。
17. **已完成 D3 时域切片**：核对 17 个冻结 episode 均为约 5 Hz，修正 rollout 默认 horizon 为 1.0 秒，训练会从时间戳解析并拒绝显式错位；42 个测试通过，6/6 train/validation 的更新 ablation 已完成。坐标转换和真实动作语义仍待仿真适配，见 [D3 日报](experiments/daily_20261027_time_contract.md)。
18. **已完成 D3 坐标切片**：新增 ENU/NED/BODY_FLU 的纯转换与 `SimAction.command_frame` 校验，覆盖 yaw 缺失、未知坐标系和可逆性测试；46 个测试通过。实际 CarlaAir/AirSim 轴约定仍需 adapter 现场核验，见 [2026-10-28 日报](experiments/daily_20261028.md)。

## 完成定义

2026-10-28 D1–D3复核续作：已补39 PASS开发manifest、固定事件指标和费用/隔离证据；再补8/32请求持久化预算、禁重定向、环境取key及API覆盖报告，136测试通过。额外数据清单仍无未使用独立town PASS；D2独立实例与D3现场联验未通过。目标未完成，下一关键依赖是独立测试数据和证明资源隔离，见 [验收表](experiments/d1_d3_acceptance_20261028.md)。

2026-10-28 再续作：完成本地生产端 x/y 保持、z 翻转配准契约；补齐 visibility-gated causal belief、后向速度、显式同快照时间映射与命令到期保持，68 测试通过。17 PASS 时间/yaw 审核通过，6/6 重训 residual 距离 RMSE 0.193 m（透明 0.262 m）；5 个 SHA 校验困难 episode 的四方法回放未显示整体收益，继续保留负结果。旧隐藏真值协议不再作当前对照。独立仿真仍被共享启动器覆盖配置/停止旧进程等隔离风险阻塞；下一项是独立配置可行性与 D4 状态/动作支持域、causal 训练输入，见 [完整报告](experiments/causal_belief_20261028.md)。

2026-10-28 续作：补齐模型保存/加载后的推理时域绑定、轨迹终点和坏时间戳拒绝，53 测试通过。固定 6/6 PASS 的记录动作验证中，透明/absolute/residual 距离 RMSE 为 0.242/0.875/0.174 m；仅是离线拟合证据。D3 整体验收仍缺源坐标/动作一致性与遮挡真值隔离；下一项以这些依赖为先，不能仅凭接口完成就跳到 live。

2026-10-25：补齐单 episode 训练的 PASS 准入检查，absolute/residual 均在访问训练状态前拒绝未审核数据。34 个测试通过，一份 SHA 校验 PASS episode 的 32 样本训练 smoke 成功。见 [实验记录](experiments/pass_training_gate_20261025.md)。

长期目标不是让某一次离线报告的 utility 更高，而是形成一条能被复现和审计的闭环链路：感知与身份 belief → 短时历史与自运动 → 候选 rollout → 硬安全过滤 → Jev/utility 选择 → 飞控执行 → 可见性、身份和安全结果回写。每个模块都要有独立开关、测试、消融结果和已知限制。
