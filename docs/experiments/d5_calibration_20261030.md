# D5：episode 隔离残差区间与动作诊断

2026-10-30，Asia/Shanghai。目的不是证明跟踪提升，而是检查记录动作残差预测的可信范围、回退成分和候选排序变化。D1–D3 尚未整体完成；本实验不使用独立测试地图，不执行闭环。

## 冻结方案

- 输入为原 `development_split_v2.json`，SHA256 `7326f07e67e90ed0e6397bb553abf31e23cd8d594cf647532a5b02447b9f8e87`。
- 原 28 train 按冻结内容 SHA 升序排列，前 10 episode 用于 calibration，余 18 用于 fit；原 6 validation 不变。完整顺序在运行开始时写入 `protocol.json`，不根据表现选择。
- 新拟合一个 D5 residual ridge（alpha=1），仅 fit 子集计算标准化和权重，不覆盖 D4 28-train 模型。每 episode 96 个固定采样，stride=2，horizon_frames=5，显式核验约 1 秒时域。不做超参搜索。
- 全部 34 文件重新完整 SHA/大小/PASS 验证，串行 16 MiB/s，只读 state，不加载 RGB。不存在的 stress 路径可通过合成测试证明不会被打开；真实运行同样不读取 stress/test。
- 预测是原始四维 outcome；visibility、中心及协方差仍为标注/规则代理，不是视觉概率或实测协方差。

## 校准与回退定义

每个校准 episode、每个输出，取该 episode **96 个采样点**上的最大绝对误差作为一个分数。10 个 episode、alpha=0.1，取第 `ceil((10+1)*0.9)=10` 个次序统计量为半宽 q。区间为原始预测 ±q，逐输出分别计算。

名义 90% 不等于已证明 90% 保证：这些 episode 是人工选择的跨环境开发数据，不能假设可交换；也不是对四个输出同时覆盖、整条未采样轨迹或新候选动作的保证。报告同时给逐帧经验覆盖和逐 episode 全采样点覆盖。数量不足以得到有限次序统计量时拒绝运行，不静默裁剪分位数。

诊断回退：每个输出分别要求“状态/动作位于 fit 特征边际 min/max 内，且预测 residual 的 ±q 区间不含零”，否则该输出使用透明 baseline。边际框只是必要的支持诊断，不建立因果动作支持。轨迹、碰撞、间距、能耗、可行性始终保持透明 baseline，不被模型修改。

区间覆盖评价的是 **raw residual prediction**，不是门控后或裁剪后输出；记录动作 gated RMSE 为 raw 逐输出修正，而候选 wrapper 使用既有 learned rollout 的物理范围裁剪，二者分别标记，不混为同一分布。

## 动作与安全口径

每个相同 validation 输入生成同一 8 候选，比较 transparent、residual、interval_gated 的 utility 排名；Mock 默认 identity_weight=0，因此其 utility-shaped 排名相同是构造结果，不是 Jev 有效性证据。真实 Jev 请求为 0，历史 attempt 未对账不绕过。

同时独立记录默认硬安全过滤后的可选集合和选择。因果审计已切断整段最小间距的输入路径；缺逐帧间距时 HDF margin=0，默认约束仍为 3m。若全部拒绝，`safe_utility_choice=null`。未过滤排名只用于离线诊断，不执行，不计算“安全事件减少”或重捕获收益。

按**当前输入**的原始可见性标注分层：出框优先；框内遮挡其次；框内可见且无遮挡记 visible；其余 unknown。任一所需标注数组缺失，整个 episode 的样本记 unknown，不把适配器默认填充值当真实标注。无样本分层为 NOT_EVALUATED。

## 复现

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python scripts/run_d5_calibration.py --split artifacts/acceptance_20261028/development_split_v2.json --output artifacts/d5_calibration_20261030 --samples-per-episode 96 --calibration-episodes 10 --alpha 0.1 --hash-mib-per-s 16
python -m pytest -q tests/test_residual_intervals.py tests/test_d5_runner.py tests/test_future_invariance.py
```

重跑须新 output 目录。模型、逐文件读取凭据、每帧排名/安全/回退记录仅在本机 `artifacts/d5_calibration_20261030/`；不得覆盖原报告、修改源 HDF 或从 test 回流。

源码 SHA 记录运行时字节。Windows `core.autocrlf=true` 在切换分支/检出时可能改变文本换行；集成时另核对规范化 LF 的源码摘要，不能把换行差异误报为模型代码变化。HDF/模型 SHA 必须逐字节相同，禁止用文本规范化处理二进制证据。

## 完整运行结果

退出码 0，`report.status=COMPLETED`。34/34 源文件完整 SHA/大小/PASS/稳定性校验通过：18 fit 共 1728 样本、10 calibration 共 960 样本、6 validation 共 576 样本。每集 96，解析时域约 0.999999978 秒。模型保存重载预测一致；运行后再次核对模型与源码指纹一致，原 D4 两个模型 SHA 未改变，逐帧诊断有 576 条。公开 [完整机器证据](evidence/d5_calibration_20261030.json) 与本机 report 字节一致。

| 输出 | 区间半宽 q | 区间总宽 | validation 经验覆盖 | 全采样点覆盖的 episode |
| --- | ---: | ---: | ---: | ---: |
| visibility 代理 | 0.326211 | 0.652421 | 576/576 | 6/6 |
| 中心代理 | 0.311324 | 0.622647 | 576/576 | 6/6 |
| range error（m） | 2.902053 | 5.804106 | 576/576 | 6/6 |
| covariance 代理 | 0.652684 | 1.305368 | 576/576 | 6/6 |

**100% 经验覆盖不是准确性/实用性达标：区间很宽，所有修正都被回退。** 校准使用每集最大值且仅 10 集；距离半宽由 `Town10HD_bicycle_normal_motion_long_seed26094696` 的校准最大误差约 2.902m 决定，另一个 Town07 bicycle long 校准集最大误差约 2.66m。保留这些失败，不删除该 episode 或在验证结果出来后缩窄区间。逐集校准最大误差及支持框见 [校准产物](evidence/d5_calibration_scores_20261030.json)。

| 同一 576 验证记录动作 | 距离 RMSE（m） | visibility RMSE | 中心代理 RMSE | covariance 代理 RMSE |
| --- | ---: | ---: | ---: | ---: |
| transparent | 0.230013 | 0.134689 | 0.042546 | 0.261518 |
| D5 residual（18 fit） | 0.233791 | 0.046229 | 0.030410 | 0.092448 |
| interval_gated | 0.230013 | 0.134689 | 0.042546 | 0.261518 |

Residual 距离误差总体退化约 1.64%，仅 2/6 验证 episode 改善、4/6 退化。D4 的 28-train residual 是另一模型，不把此差异单独归因于区间算法。门控后四维结果与透明 baseline 一致：576/576 记录动作全部输出回退，4608/4608 候选全部输出回退，因此本轮 **等价透明基线，无 learned 收益证据**。

动作支持/选择诊断：

- 3174/4608 候选超出 fit 边际框；绕行左右、升降、扫描五类均为 576/576 越界，其余三类各 98/576 越界。框内也不代表有反事实动作标签。
- 未门控 residual 改变 151/576 个输入的 utility 第一名；门控后改变 0/576。改变排名本身不代表改善。
- Mock 与 utility 排名差异 0，是该 Mock 的构造性质，不归因于真实 Jev；真实请求 0。
- 默认硬安全门控在 576/576 个输入上拒绝所有候选，实际可选帧 0；没有执行动作，没有跟踪、安全事件或重捕获效果测量。
- 当前输入 visible=576，occluded=0，out_of_frame=0，unknown=0；后三项均 NOT_EVALUATED。没有从 stress/test 借样本补齐困难分层，也不称困难场景校准通过。

## 软件验收与边界

全套 **287 passed, 1 skipped**，针对性 D5/因果测试 20 项通过；compileall 和 diff 检查通过。只读审查无阻断发现，独立相关测试 37 项通过。原 CARLA Python 3.10 缺 pytest，未安装/修改共享环境；仅运行区间模块导入和数值 smoke 成功，不声称该环境全套测试通过。

本轮完成 D5 在现有已验证数据上的校准、分层缺口与回退诊断交付；没有证明跟踪提升、困难场景覆盖或可以部署。D1 独立 Town03 PASS、D2 历史真实 Jev 对账、D3/后续真实动作效果边界不改记通过。下一步只能在已批准范围内补证据/分析，不自动启动 D6–D14、重划 test 或扩大付费调用。
