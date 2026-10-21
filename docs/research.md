# Jev 与具身智能研究线索

这个仓库把 Jev 当作“候选行为的结构化评估器”，把仿真、约束和执行交给代码。下面的论文用于设计依据和后续实验，不表示它们已经完成了 Jev 与无人机跟踪的直接组合。

## 直接相关的 Jev / System One 工作

- [Code Owns the Simulation, Jev Owns the Evaluation](https://arxiv.org/abs/2610.01834)：提出让代码模拟候选后果、让 Jev 在结构化选项中作决策；这是本仓库“rollout 在外部、Jev 只选行为”的直接依据。
- [Just Ask Jev: Reinforcement Learning for Calibrated Decisions](https://arxiv.org/abs/2609.29429)：讨论 RLCD、概率输出与校准，适合用来设计 confidence gate 和离线校准实验。
- [JEV-as-a-Judge: Accept When Confident, Escalate When Unsure](https://arxiv.org/abs/2609.26550)：适合参考接受、fallback 和升级到人工/更强规划器的门控逻辑。
- [More Choices, Fewer Decisions: Ordinal-Scale Bias in JEV-like Direct-Decision Models](https://arxiv.org/abs/2609.38827)：提醒候选数量、顺序和刻度可能影响直接选择模型，应在动作库扩展时做顺序随机化和偏差评估。
- [TypeSafe: Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)：官方 System One / Jev 产品背景和结构化决策定位。

## 具身智能中的候选技能选择

- [SayCan: Do As I Can, Not As I Say](https://arxiv.org/abs/2204.01691)：用语言模型提出技能、用可执行性和价值函数筛选，和本仓库的“语义动作 + 可行性过滤”结构相近。
- [Language Models as Zero-Shot Planners](https://arxiv.org/abs/2201.07207)：展示语言模型在高层动作序列规划中的作用，可借鉴动作描述和状态提示的组织方式。
- [Inner Monologue](https://arxiv.org/abs/2207.05608)：把环境反馈加入闭环决策，支持在每个短动作之后重新感知和重规划。
- [Code as Policies](https://arxiv.org/abs/2209.07753)：强调生成的策略需要由可执行 API 和约束包住，支持把 Jev 放在飞控接口之上而不是直接控制执行器。
- [LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks](https://arxiv.org/abs/2402.01817)：提出让外部规划器、验证器和执行器承担可靠性，适合指导 Jev 与 rollout / safety 的模块边界。

## UAV 主动跟踪和主动感知

- [End-to-End Active Object Tracking via Reinforcement Learning](https://proceedings.mlr.press/v80/luo18a.html)：早期主动视觉跟踪的强化学习基线。
- [Visual Object Tracking in Drone Images with Deep Reinforcement Learning](https://ieeexplore.ieee.org/document/9413316/)：面向无人机图像跟踪的 DRL 方向，可作为视觉跟踪策略比较。
- [Hierarchical Active Tracking Control for UAVs](https://www.mdpi.com/2076-3417/11/22/10595)：分层控制与高低层动作接口的参考。
- [Perception-Aware Nonlinear Model Predictive Control for UAV Target Tracking](https://arxiv.org/abs/2302.04708)：把可观测性/感知质量放进 NMPC 目标，适合替换本仓库的简化 rollout。
- [Event-Triggered Model Predictive Control for UAV Target Tracking](https://www.sciencedirect.com/science/article/pii/S2405896317323650)：支持“有信息时重规划、其他时间由内环执行”的触发式结构。
- [Particle Filter Based Information-Theoretic Active Sensing](https://www.sciencedirect.com/science/article/pii/S0921889010000023)：信息增益、belief 不确定性和主动观测动作的理论依据。
- [Reinforcement Learning for Agile Active Target Sensing with a UAV](https://arxiv.org/abs/2212.08214)：面向敏捷主动感知的强化学习方法，适合作为动作库和信息增益设计的对照。
- [UAST: Unified Active Search and Tracking for Arbitrary Targets with UAVs](https://openaccess.thecvf.com/content/CVPR2026/html/Qin_UAST_Unified_Active_Search_and_Tracking_for_Arbitrary_Targets_with_UAVs_CVPR_2026_paper.html)：把搜索和跟踪统一起来，提醒动作库需要覆盖“丢失后重捕获”而不只是持续跟随。

## 建议的实验顺序

1. 用离线录像/仿真回放比较 utility 排序、Mock Jev 和 TypeSafe Jev 的选择一致性。
2. 对 candidate order、动作数量、预测误差和置信度做敏感性与校准实验。
3. 在同一飞控和同一障碍物场景下比较固定策略、MPC/RL 策略和 Jev-Track 的闭环指标。
4. 最后再把真实动力学、延迟、丢包和感知失败注入 rollout，验证 fallback 是否能保持安全边界。

