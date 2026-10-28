# 2026-10-28：D3 因果 belief、源坐标核验与重训对照

## 结论

修复了代理回放在遮挡期间仍输入目标真值、初始 yaw 对准真值目标以及 HDF5 中心差分使用未来行的问题。17 个冻结 PASS episode 的图像/状态时间戳逐帧对齐、相机 yaw 均可读。68 个测试通过。

重新训练的 residual 在记录动作验证上距离 RMSE 为 **0.193334 m**，透明基线为 **0.261848 m**（约降低 26.2%）。但 5 个困难 development episode 的因果代理回放只出现很小的可见率差异，成功事件重捕获耗时变长，**尚无整体跟踪收益证据**。没有真实 Jev 请求或 CARLA/AirSim 闭环运行。

## 生产端证据与实现

只读核对本机 `D:/CarlaAir/repos/carlaair-research/src/capture/run_independent_uav_episode.py`：

- `transform_values` 第 543 行附近写出位置 x/y/z 和旋转 `[roll, yaw, pitch]`，yaw 为第 1 列。
- `carla_to_ned` 第 725 行附近使用 `(x+ox, y+oy, -z+oz)`；相邻逆变换与角度转换一致。不能套用泛化 ENU/NED 的 x/y 互换。
- 第 3340–3398 行附近从同一 CARLA world snapshot 取得 actor/camera 状态后调用 `writer.state`。写入器不重排位置轴；数据的 state/image axis 注记指采样轴，不是地理坐标轴。

新增 `CarlaAirAlignment` 与候选命令纯编译器，需要显式配准偏移；候选速度保持世界 x/y 并翻转 z。局部生产端实现不替代未来自有实例的现场配准，也未证明所有 CarlaAir 版本均采用同一约定。

新增 `CausalTargetEstimator`，仅在代理可见时接收位置；速度来自同身份的历史可见位置。不可见期间常速度外推，位置协方差随失联时长增长。第一次看到目标以前不引用目标真值。identity 的两帧确认不延迟速度对新观测身份的重置。

回放不再复制 oracle environment，改用白名单；history 和 selector 都接收因果状态。回归测试对隐藏位置、距离、方位、身份、oracle 速度和环境字段施加扰动，验证 selector 输入和最终选择不变（包含开场不可见的情形）。真值仍可用于观测生成与指标计算，不能把该代理描述为真实 RGB 感知系统。

HDF5 现在拒绝缺失时间戳/映射、非有限或不递增时间、非整数行号、图像与状态错配。对齐容差 1 微秒不允许未来行或过期行；后向差分不读取未来位置。现有动作按到期停止，之后理想零速度保持，避免稀疏决策延长命令时长。

## 数据、协议与复现

冻结 split：6 train / 6 validation / 5 development_stress，没有独立 final test。训练仅用前两组；困难组不调参、不回流。本轮两种 rollout 固定同一候选库、history、identity、安全门控和客户端配置；residual blend=1，未启用可选 OOD 阈值，不按结果寻找更有利阈值。困难组输入每批顺序 SHA-256 校验后只读打开，5/5 一致，RGB 未载入。运行环境：Python 3.14.7、NumPy 2.5.4、h5py 3.16.0。

```powershell
python scripts/audit_episode_contract.py --split artifacts/experiment_split_20261026.json --output artifacts/causal_belief_20261028/episode_contract_audit.json
python scripts/compare_recorded_rollouts.py --split artifacts/experiment_split_20261026.json --output artifacts/causal_belief_20261028/calibration --samples-per-episode 24
python scripts/run_closed_loop_batch.py --manifest artifacts/occlusion_pass_20261024/pass_manifest_sha256.json --output artifacts/causal_belief_20261028/stress_replay.json --verify-manifest-sha256
python scripts/run_closed_loop_batch.py --manifest artifacts/occlusion_pass_20261024/pass_manifest_sha256.json --output artifacts/causal_belief_20261028/residual_stress_replay.json --model artifacts/causal_belief_20261028/calibration/baseline_residual.npz --verify-manifest-sha256
python -m pytest -q
python -m compileall -q scripts src
git diff --check
```

原始 JSON、逐帧记录和 NPZ 均保留在上述本机 artifacts；该目录被 Git 忽略，随代码提交本报告及复现脚本，不上传原始 HDF5、模型或任何密钥。外部读者需要已授权数据和本地冻结 manifest 才能复跑。

## 记录动作预测结果

每个 episode 24 样本，train/validation 各 144 样本，实际 horizon 约 1 秒。模型保存重载后验证推理时域一致。

| 模型 | validation 距离误差 RMSE |
| --- | ---: |
| 透明 rollout | 0.261848 m |
| absolute ridge | 1.438395 m |
| residual ridge | 0.193334 m |

这是 oracle-state 的记录相机速度后果预测；训练状态的目标速度虽为后向差分，仍不等于按可见性门控的感知输入。候选反事实标签缺失，validation 全可见，因此不能用此表证明遮挡预测或可见率改善。旧中心差分数据下的 0.174/0.242 m 不再作为当前结果。

## 困难场景回放结果

同一 5 episode，每方法 1500 决策帧，共 6000 次本地决策。使用新的 belief v2 和记录 yaw，不能与旧隐含真值协议声称改善。下表可见率/耗时/fallback 是 episode 等权均值；耗时仅统计恢复成功事件，再按 episode 平均，**不是冲刺方案要求的 10 秒受限、连续 0.5 秒正确跟踪主指标**。末尾未恢复事件单列，避免只看成功例。

| rollout + selector | 代理可见率 | 成功事件平均重捕获 | 末尾未恢复事件总数 | fallback 比例 |
| --- | ---: | ---: | ---: | ---: |
| 透明 + Mock Jev | 68.27% | 2.747 s | 4 | 80.13% |
| 透明 + utility | 71.53% | 1.966 s | 3 | 0% |
| residual + Mock Jev | 68.47% | 3.160 s | 4 | 83.47% |
| residual + utility | 71.87% | 2.873 s | 3 | 0% |

四方法 selected_unsafe 均为 0，预测 near-miss 均为 20% 帧，集中在 Town05 close episode；它是预测安全阈值事件，不是实际碰撞传感器结果。真实身份交叉均为 `NOT_AUDITED`，单 actor 保持率不作为交叉能力证据。报告中 n=5 的正态近似区间仅供描述，不是论文所需成对 episode/seed bootstrap 结论。

Mock 大部分帧由系统 fallback 决策，不能把系统效果归因于 Mock/Jev；本轮没有真实 Jev，旧 API smoke 不能代表新协议。raw residual 没有展示更快重捕获，因此当前不升级为部署默认。下一轮应先统计训练/候选的动作与状态支持域，并对齐 causal belief 训练输入；不在这 5 个 development episode 上反复挑阈值制造收益。

## 独立仿真阻塞与下一验收

只读核对 `D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/CarlaAir.ps1`：第 254–275 行会覆盖用户 Documents/AirSim/settings.json、编辑发行目录 DefaultEngine.ini，并调用 Stop-CarlaAirProcess；该函数读取共享 PID 文件并 Stop-Process。脚本还写本目录 PID/log 并可能启动交通进程。直接运行会越过本任务隔离边界，故未执行。

收尾时只读进程快照仍有 carlaair 环境 python PID 64920（父 62908），未查看可能含敏感参数的命令行，也未向进程发送信号。2000/2001/2002/41451/41452 未发现监听；这不证明控制链释放或本任务取得所有权。与早先快照相比进程已变化，进一步说明不能靠瞬时端口空闲认领资源。

状态仍是 `BLOCKED_PENDING_RESOURCE_OWNERSHIP`，没有修改共享配置、锁、端口、采集上传或真实飞行。下一项：查证当前构建能否显式使用任务专属 AirSim settings/UE 配置与工作目录；准备独立二进制/可写目录、全部 RPC/streaming/traffic 端口、PID/日志/输出目录和进程所有权清单；只读核验 GPU/内存余量后才能执行独立健康检查和配准。若构建不支持配置隔离，使用独立 Windows 用户/机器或任务专属完整实例，而不覆盖共享设置。

D3 已补齐本地源坐标数学映射、因果输入与时间对齐回归，**未完成真实实例验收**。D4 继续支持域、动作覆盖及 causal 输入校准；D6 仍缺真实低层执行/延迟/过期响应回归；最终指标的固定事件窗、独立测试和真实 crossing 数据也待补齐。
