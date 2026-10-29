# Town03 独立测试地图登记 v1

日期：2026-10-29（Asia/Shanghai）。用户明确选择“用town3”；标准地图名记为 **Town03**。这是现有两周冲刺独立town条款的具体选择，不改动既有训练划分，也不代表已完成D1测试数据验收。

## 当前状态

- `RESERVED_PENDING_DATA_AND_RUNTIME`：保留给最终跨地图测试，当前接纳的Town03 PASS episode为0。
- 现有39集开发划分保持28 train、6 validation、5 development_stress。既有D4模型与结果不重命名为Town03测试。
- 在本次登记前，对本仓库docs/scripts/src/tests中的JSON、JSONL、Markdown与Python检索 `town0?3` 未找到匹配；现有开发manifest没有Town03。此检查只覆盖本项目记录，不能证明其他项目/外部预训练从未使用Town03。
- 本任务独立发行副本中存在Town03.umap及Town03.uexp，文件大小和SHA见[只读预检查](experiments/evidence/town03_reservation_20261029.json)。文件存在不证明成功加载，也不证明CARLA与AirSim在此地图可正常联用。
- 预检查可用物理内存2,884,404 KiB，约2.75 GiB，低于现有独立启动器4 GiB门槛；未启动实例。查询未列出外部Python/Carla/UE4/Unreal进程或相关端口监听，仅为当次快照，不是资源永久移交。

## 使用边界

1. Town03不进入train、validation、development_stress、模型校准、候选设计或OOD/blend阈值选择。D5校准与D6/D7接口/控制调试继续在原开发数据和Town02等开发地图进行。
2. Town03仅允许方法无关的资产/接口/结构/完整性检查；不要在Town03运行开发版策略后根据得分修改算法。如果发生效果暴露，必须记录并降级为development，不再声称该地图未见。
3. 正式运行前先冻结模型/配置/代码SHA、方法集合、场景与seed清单、样本量、排除规则和计算预算。当前只冻结地图选择，尚未冻结这些运行要素，因此不能执行正式测试。
4. 新数据仅写本任务独立输出目录，不修改旧trimmed HDF5、采集/上传进程、锁、端口或共享配置。每次启动重新证明独立所有权和资源充足；不为获得测试数据启动外部采集流程。
5. 新episode须经结构、时间/坐标、标注、完整性与SHA审核PASS，记录来源、地图、seed、父episode和用途后，才能进入final-test manifest；地图文件不能作为PASS episode。
6. 结果沿用[指标协议](metrics_protocol_v1.md)，失败、超时、回退和全部预定seed均入账。缺真实身份交叉标NOT_AUDITED；oracle、代理和真实仿真结果分开。一个Town03测试只能支持该地图上的留出结论，不能声称覆盖多张独立地图。

## 可继续推进的依赖顺序

| 工作 | 当前决策 |
| --- | --- |
| D1 独立地图选择 | 已按用户决定登记Town03 |
| D1 PASS测试数据接纳 | 仍待数据、完整审核及来源/未开发使用记录，不修改available标志 |
| D5 residual区间校准、回退分析 | 可以继续用现有train/validation，不依赖Town03或真实Jev |
| D6/D7仿真执行链 | 资源充足后在本任务独立开发实例推进，不借用采集实例 |
| Town03正式测试 | 方法/场景/预算冻结、独立运行及PASS审核满足后执行，不提前用它调试策略 |
| 真实Jev | 仍受环境key、完整历史attempt对账和8/32准备期门控限制；不阻塞Mock/utility离线工作 |

后续续作已为启动器补充 `--map Town02|Town03`，默认Town02不变；Town03仅允许API-only检查，客户端与launcher双重核对实际地图，禁止在此运行开发动作探测。首次Town03请求被原4GiB内存门槛拒绝，未启动，见[启动预检查](experiments/town03_api_preflight_20261029.md)。代码就绪不等于地图已加载或测试数据已接纳。

登记时本文件仅是治理协议。用户随后批准的自动保护现已实现于 `holdout.py`，见[保护验证报告](experiments/town03_guard_20261029.md)。训练样本构建、单集/跨集训练入口、D4 runner和开发清单构建均拒绝可识别的Town03；普通只读适配器保留审核能力，final_test清单仅允许元数据完整性检查，不因此接纳为已审核测试集。现有冻结39集不包含Town03。

保护识别原路径、解析后路径及结构化地图/来源元数据，支持Town3/Town03、大小写、Town03_Opt和改名后仍带地图metadata的文件。不是安全沙箱或来源鉴真：匿名数组、删除/伪造元数据的改名复制、无Town03提示的硬链接不能由本检查证明来源；低层numpy拟合与通用离线评估也不构成测试集访问控制。正式接纳仍需独立manifest、内容SHA和使用记录。未来正式测试须用独立评估路径，不添加训练override开关。
