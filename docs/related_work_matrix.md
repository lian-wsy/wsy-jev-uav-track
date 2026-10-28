# D1 一手来源对照表（2026-10-28 核验）

本项目的待检验假设是：因果短时belief、动作条件residual预测与可信度回退可改善丢失重捕获，而Jev只评价已由代码给出的候选后果。尚未证明新颖性或性能优势。下面区分设计依据、适用基线和已复现内容；不是“这些论文已经实现Jev无人机跟踪”。

| 一手来源 | 与任务的关系 | 本仓库对应与差异 | 验证状态 |
| --- | --- | --- | --- |
| [Code Owns the Simulation, Jev Owns the Evaluation, v2](https://arxiv.org/abs/2610.01834v2) | 预印本讨论由外部代码预测，再让Jev评价候选；是当前模块边界的依据 | `rollout.py`/`learned_rollout.py`预测，`jev_client.py`请求，`selector.py`安全过滤与回退；UAV重捕获收益仍须独立实测 | 已核验摘要与版本；没有复现其机器人实验，不能移用其数值 |
| [SayCan 作者项目页](https://say-can.github.io/) | 高层技能的任务相关性与可执行性结合 | 对应有限动作库与安全/后果评价；本项目不是语言模型生成长任务序列，也没有同样技能value训练 | 设计对照，未复现；作者页提供桌面仿真代码 |
| [Luo 等，ICML 2018主动跟踪](https://proceedings.mlr.press/v80/luo18a.html) | 视觉帧到相机控制的ConvNet-LSTM/RL，涉及丢失后恢复，适合列为主动跟踪参考基线 | 本项目目前可见oracle位置+运动学代理，不是端到端RGB策略；必须共享环境/观测/控制后才可比较 | 已核验论文与[作者代码入口](https://sites.google.com/site/whluoimperial/active_tracking_icml2018)，训练仓库为[active_tracking_drl](https://github.com/whluo/active_tracking_drl)，环境为[gym-unrealcv](https://github.com/zfw1226/gym-unrealcv)；尚未安装或复现CARLA版本 |
| [TypeSafe 官方文档](https://docs.typesafe.ai/introduction) | state与typed question输入，choice/probabilities/confidence输出的产品接口依据 | Jev为外部服务，不是本项目训练出的模型；confidence也不能直接视作无人机成功概率 | 接口历史smoke存在；本轮没有联网推理，不能当新协议真实Jev对照 |

论文比较重点：透明+utility检验是否只是启发式控制收益；residual+utility检验学习预测收益；同residual+真实Jev检验决策器独立贡献；history、identity、uncertainty分别消融且保留安全约束。若Jev不优于utility，应报告负结果，而不是用API接入充当论文贡献。

可复现主动跟踪基线仍欠环境迁移与运行证据，留作D7前后闭环任务。以上仅是D1的来源和设计对照验收；不宣称SOTA、不使用不同感知权限的结果比较。
