# D1–D3 今日验收记录（2026-10-28）

目标：今天按 `docs/sprint_20261026_20261108.md` 原标准逐项复核并补缺；验收不等于保证算法提升。起点 `d9d91ea`，基线 68 测试通过。当前目标已在本聊天建立，未另开调度任务。

**最新续作：D2 有界独立实例可行性已通过，原阻塞不再全部成立。** 用户授权使用空闲本机后，独立副本验证配置/进程/端口/清理，以及坐标、双时钟、限速上升、四轴和客户端刷新；145测试通过。标准起飞False与非锁步未解决，D3 live adapter尚未完整验收，D1独立town test仍缺。详见[完整报告](isolated_carla_smoke_20261028.md)。以下原始表格保留历史上下文，不能当最新状态。

## 执行清单

2026-10-28 后续证据：真实图像/state时间门控已接入独立probe，12/12正常读取通过、过期动作RPC前拒绝；相机平移和朝向完整验证，167测试通过。D3新增现场证据见 [报告](live_observation_20261028.md)；尚未接到正式belief/selector，D1独立test仍未满足，不勾选总体完成。

- [x] D1：核验研究假设和具备一手来源的相关工作对照表。
- [ ] D1：核验 PASS、30–60 独立 episode、按场景/seed 分组、文件 SHA、独立 town test 与已使用数据隔离。旧 17 episode split 不覆盖改名。
- [x] D1：冻结指标定义、全方法公平预算、最终 test 使用禁令，明确哪些指标代码可用。
- [x] D2：核验版本/同步/资源/外部任务占用，落实专属配置/端口/输出，不运行共享启动器；有界 live probe 已通过，见最新续作。
- [x] D2：形成 Jev 调用、耗时、费用和累计上限报告；不擅自扩大付费请求。attempt完整账本未知另列阻塞。
- [x] D3：完成离线坐标/yaw、时间对齐、horizon、控制周期约定、命令到期、遮挡真值隔离和已列异常输入复核；真实时钟/控制周期仍依赖D2。
- [x] 运行针对性回归及最小可复现检查，形成可提交结果；剩余项逐条标未通过，不能用代理替代真实实例。

## 执行边界与决定

任务是落实既有已授权冲刺，保持研究假设和标准，不要求用户重新批准相同设计。使用现有计划执行、测试驱动和完成前验证技能；数据与仿真只读审查按并行核查技能交给两个独立审查任务，代码集中修改。

原生 worktree 创建因 sourceRoot 是聊天工作区内的嵌套仓库被工具拒绝；仓库原本干净，改在专用 `codex/d1-d3-acceptance` 分支工作，不改原采集工程。已有提交推送授权沿用。读取源码不等于证明发行二进制具有相同行为；没有资源所有权与写入隔离证据不启动模拟实例。

当前缺口：D1 原 17 episode 均为开发数据，无 final test；缺完整原始文件 SHA 和正式相关工作表。D2 共享启动器会改用户 AirSim settings、DefaultEngine.ini 并 Stop-Process 旧 PID。D3 离线时域/坐标/因果回归存在，但 adapter 仍可能接受 NaN/Inf，需用真实 API 边界回归核验。

## 本轮验收结果

| 日程/标准 | 本轮实际证据 | 判定 |
| --- | --- | --- |
| D1 研究假设与相关工作表 | [一手来源对照表](../related_work_matrix.md)，明确Jev不是自训练模型，主动跟踪已发表代码入口核验；尚未复现其基线 | 文档通过；论文新颖性不作通过声明 |
| D1 30–60 PASS与SHA | 39文件、11700图像帧，28 train / 6 validation / 5 stress；旧17分配保留，39内容SHA唯一、seed唯一、规范路径唯一；5旧stress指纹一致 | 数量、文件级去重与指纹通过；未证明不同seed轨迹绝不相关 |
| D1 独立town/test | 审计发现24个效果暴露seed；现有259 PASS仅五个town且均已开发使用，未发现独立town来源 | **未通过**，final_test_available=false |
| D1 指标与公平性 | [协议v1](../metrics_protocol_v1.md)，固定窗/失败计入/稳定确认可运行；独立episode统计方案冻结 | 定义与重捕获实现通过；live安全/视觉/统计矩阵待后续 |
| D2 版本/坐标/同步/资源核对 | [隔离复核](simulator_isolation_20261028.md)：源码与exe指纹、settings解析、双时钟、低内存与外部任务证据 | 只读调查通过 |
| D2 独立实例/端口/配置/输出落实 | 有专属配置候选，但写入回落、进程所有权、内存与实际时钟尚无完整证明；没有运行launcher | **未通过**；不能靠端口无监听认领实例 |
| D2 调用/时间/费用 | [预算](jev_budget_20261028.md)，8/32准备上限、720/4320请求正式示例，明确公开费率不等于账户报价 | 估算报告通过；attempt账本未知，当前新增API调用0 |
| D3 坐标/yaw/时间/horizon | 39文件读state数组复查同快照且yaw存在；1秒模型/到期执行/隐身真值扰动回归通过 | 离线通过；二进制现场配准与真实20Hz控制未通过 |
| D3 异常输入 | 修复SimAction/SimObservation NaN/Inf，HDF目标/相机位置NaN及距离NaN/Inf/负值 | 覆盖所列字段；不声称已经完整审计所有外部schema字段 |

**整体结论：D1部分通过、D2阻塞、D3离线通过但现场联验未完成。不能勾选“前三天全部达到预期”。** 目标仍保持未完成；今日已完成可安全推进的代码、数据指纹、协议和费用/隔离证据补齐。提升效果属于后续对照验收，不是前三天接口工作能保证的结果。

## 数据扩展与最小复现

输入是固定coverage快照，仅选 `quota_grid_20261004_formal` 中PASS，排除24个已效果暴露seed，按semantic cell字典序、每cell最小数值seed取22；不依据可见率或模型结果选择。新增全部train，避免事后制造“未见测试”。`26094452`曾做质量检查，明确不是“从未看过”。现有样本没有增加真实遮挡/出框/身份交叉覆盖。

```powershell
python scripts/extend_development_split.py --base-split artifacts/experiment_split_20261026.json --coverage artifacts/coverage_audit_20261024.json --exposure docs/experiments/development_exposure_20261028.json --output artifacts/acceptance_20261028/development_split_v2.json --hash-mib-per-s 16
python scripts/audit_episode_contract.py --split artifacts/acceptance_20261028/development_split_v2.json --output artifacts/acceptance_20261028/contract_audit_39.json
python scripts/evaluate_fixed_event_replay.py --transparent artifacts/causal_belief_20261028/stress_replay.json --residual artifacts/causal_belief_20261028/residual_stress_replay.json --output artifacts/acceptance_20261028/fixed_event_metrics.json
python -m pytest -q
python -m compileall -q scripts src
git diff --check
```

冻结命令拒绝覆盖已有输出；复跑需新路径。约12.05GiB原文件只读串行限速16MiB/s，每文件检查前后大小/mtime不变并计算完整SHA。39个文件再次通过当前适配器校验；完整本机manifest SHA `7326f07e67e90ed0e6397bb553abf31e23cd8d594cf647532a5b02447b9f8e87`。可公开复核的[文件名/seed/split/SHA清单](development_split_v2_20261028.json)随仓库保存，原HDF、绝对路径manifest和旧模型仍在本机，不上传密钥或原数据。

本轮未重训39集，也没有把旧6/6模型效果改称39集训练成果。原split保持历史有效，后续显式选择v2才用新增28 train。

## 重捕获主指标复算与负结果

从已有全300帧reference源标注冻结事件，取非重叠10秒窗，连续0.5秒正确跟踪后确认；提前终止即使曾短暂恢复也计失败。共3个eligible事件、3个尾部不完整reference窗；每方法同一事件集合。

| 方法 | 恢复率（事件所属episode等权） | 10秒受限重捕获均值 |
| --- | ---: | ---: |
| 透明 + Mock | 100% | 1.867 s |
| 透明 + utility | 100% | 1.867 s |
| residual + Mock | 66.7% | 6.667 s |
| residual + utility | 100% | 1.867 s |

这是3个开发代理事件，不能解释成泛化或真实跟踪100%成功。新口径下residual+Mock明显退化，没有支持Jev/rollout整体提升的证据；和旧“只平均成功事件”指标不可混用。真实身份交叉仍NOT_AUDITED。该复算不训练、不调参、不新增网络调用。

## 测试、审查与剩余解除条件

最终全套119测试通过，编译/diff检查通过。新增测试先复现缺失行为，再修复；代码审查指出“先恢复后提前终止误判”和“距离非有限值遗漏”，两项修复及cell数量不足拒绝都经独立复核。稀疏连续性测试保持完整10秒窗，确保不是被早停分支意外覆盖。

要完成原目标仍需：

1. 具有完整PASS证据、未在本任务/相关实验调参使用的独立town episode；在取得前不挪用已开发town作为final test。
2. 明确本任务可独占的仿真进程/全部可写目录/端口与足够内存资源，或独立用户/机器。当前外部采集上传控制链不由本任务停止或重配。
3. 在上述隔离成立后进行二进制settings回落、坐标配准、双时钟/暂停、1秒动作和低层周期受限smoke；否则D3现场验收不能通过。
4. 新真实Jev批次前核对完整attempt账本，未知额度不进行自动付费调用。预算估算已完成，不需要再次发送API key。

## 续查：额外数据位置与预算代码验收

读取用户指定的“上传new”和“@继续 CarlaAir 数据采集并打开格子 在当前这个聊天里继续采集任务，让后打开格子”两个聊天后，仅对新发现目录做文件名/元数据清单核查，没有打开或hash新E盘HDF，也没有控制其他聊天任务。聊天中的暂停/失败是历史记录，不据此推断当前进程状态。

| 来源 | 本次只读观察 | 是否可解除D1 |
| --- | --- | --- |
| `E:\CarlaAir_1720\data\trimmed\v3` | 21个HDF5文件名，均Town02 | 否，文件名不是PASS证明，且town已开发使用 |
| `E:\CarlaAir_C_migrated_20261023` | 131个带town文件名：Town02=55、Town05=11、Town06=1、Town07=32、Town10HD=32 | 否，无新独立town，未做完整接纳审计 |
| `E:\CarlaAir_Town06_Town07` | 717个HDF样式文件名，多为raw/diagnostics/trial；`diagnostics\town07_v7`枚举报os error1392（目录损坏/不可读取） | 否，未把raw/诊断文件当trimmed PASS；未修复磁盘或继续探读该子树 |
| `selection/active_dataset_manifest.jsonl` | 1104条；184 SELECTED_PENDING_FULL_V3_ACCEPTANCE，920 PILOT_PENDING_FULL_V3_ACCEPTANCE | 否，selection/PENDING不是完整PASS验收 |

末次资源只读快照未见Carla/UE4进程，仍见外部dashboard python PID4668/PPID16864；可用物理内存约4.92GiB。进程未列出或端口空闲均不构成资源移交；未修改任何外部进程、配置、端口或锁。此前资源记录保留为历史快照，不能当实时状态。

另补一个实际代码缺口：此前8/32上限仅在文档约束，现已接入发送前持久化事务预算，并去掉三个入口的`.env`加载。请求失败照计、缺失/未知账本拒绝联网，部分fallback不得标完整real对照；详见 [预算实现说明](jev_budget_20261028.md)。本轮没有创建正式账本，没有请求真实Jev，没有重训或修改源数据。D1/D2/D3原验收结论不变。

## 第三轮阻塞复核（16:03 +08）

上一轮是实质进展：预算门控已提交推送`a1defee`，136项测试通过。此次从当前checkout重新读取冲刺原始D1/D2/D3条款、隔离报告和验收证据：代码工作区干净，远端main与本机均为`a1defee51763bee4820f900885e645c734220a1a`。未因文档通过而扩大成现场验收通过，也未重跑已通过测试制造进度。

新只读清单快照：D盘trimmed根287个HDF样式文件（Town02=12、Town05=100、Town06=43、Town07=69、Town10HD=63）；E盘1720 trimmed根22个，均Town02；C_migrated根131个且town分布同前。此处包括所有列出的HDF文件，不是PASS计数。selection清单mtime为16:01:13，1106条（184 SELECTED_PENDING_FULL_V3_ACCEPTANCE、922 PILOT_PENDING_FULL_V3_ACCEPTANCE）。目录/清单有变化，不能说外部链路已经停止；没有读取新增HDF、重新hash或修改这些文件。仍未发现独立town或新完整PASS接纳证据，不把PENDING纳入训练。

资源快照：可用物理内存4,287,684KiB（约4.09GiB）；进程枚举只有外部python PID4668/父16864符合python/Carla/UE4/Unreal筛选，没有本任务仿真进程；`artifacts/isolated_sim_d2`不存在。这仅证明当次观测，不能证明外部资源已经移交。原写路径回落、独立配置/进程/端口所有权与双时钟现场验收仍无证据。

同样的独立数据/独立仿真条件缺失已在原用户触发及两次目标续作中连续确认。当前D1–D3范围内可安全推进的离线实现和报告已交付；再做外围清理或提前转D4不能解除这些验收项。本日目标应标记`blocked`而非`complete`，等待可证明独立的仿真资源或明确资源移交，以及未参与开发的独立town PASS数据；取得后继续原目标，不降低标准、不重新认领外部任务。真实Jev历史attempt仍UNKNOWN，正式API账本未初始化。
