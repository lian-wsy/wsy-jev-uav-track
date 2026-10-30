# D2：真实 Jev 小规模调用的独立只读复核

2026-10-30，Asia/Shanghai。接续 [D1–D3优先复核](d1_d3_priority_20261030.md)；本报告产生时，该报告中的机器清单已经生成，因此不改写旧快照。

唯一运行方交付 `D:/JevUavRuns/20261028_d2_probe01/runs/tracking07_20261030` 后，本聊天在独立clone只读检查结果、逐决策记录、视频编码元数据及唯一pilot账本。未触发新请求、图像捕获或仿真动作，也未更改正在进行的录制工作。

## 实际核验结果

| 项目 | 结果 |
| --- | --- |
| 独立运行 | 记录地图 `Carla/Maps/Town02`，设置匹配，CARLA client/server `7a7fdb3-dirty`、AirSim server 1 |
| 模型 | D5 residual SHA `a4fbeb5590d3d9e154f793ee2a702ae49a44c89dff4997e712fd17ebeb451000`；不是新训练模型 |
| Jev回复 | 2条 `RESPONSE`，返回版本 `jev-1.14.0`，均选择 `orbit_left` |
| 请求延迟 | 2312.3392001725733ms、2120.994199998677ms；仅2样本，不估总体p95 |
| 实际执行 | 两条均 `NOT_EXECUTED`，拒绝原因 `Only the latest accepted observation may dispatch`；Jev动作0、模型动作0 |
| 记录器完整性 | 独立 `audit_decision_dataset.py` 输出 CHECKED，无issues；2条建议、2条未执行、0条执行记录、0条训练接纳 |
| 账本 | `requests.sqlite3` 用SQLite `mode=ro`检查：scope为新pilot，prior=0，2条choice预约（pilot01，UTC09:02:11/09:02:13） |
| 保护及清理 | 原smoke终态API_SMOKE_COMPLETED，protected_unchanged与cleanup_confirmed均true，postflight监听空；是历史终态，不证明资源今后一直空闲 |
| 视频 | 编码报告记100输入/100解码帧、14.76秒、跨度误差0.0116887秒；本聊天未重新解码，不把元数据复核称为独立视觉/跟踪审核 |

账本内容与这次2条回复在数量、batch、时间上相符；逐条advice未保存attempt_id/服务端request_id，因此不能宣称有逐请求服务端认证或计费对账。账本当次已用2/8，剩余额度是动态状态，不据此承诺后续仍有6次。历史准备期attempt仍UNKNOWN，不回填或重建旧账本。

数据完整性工具自身明确 `actual_live_jev_responses_verified=0`：它只核验文件协议和哈希，不验证外部传输。上表对真实服务的判断依据运行方现场记录、返回模型版本与共享预约账本，不冒称是完整性工具证明了联网或服务端身份。

## 对 D1–D3 验收的影响

**D2 的小规模真实服务调用现已有现场回复证据**，不应继续以“尚无新调用”或旧对账阻塞作为描述。请求超过200ms，且观测窗口已被更新，原门控拒绝执行；这是已观察到的延迟/授权问题，不是Jev动作成功。

该结果不补齐D1的Town03 PASS测试集，也不证明闭环效果、重捕获或模型收益。记录器样本仍NOT_AUDITED且没有动作后果，不能作为执行后果训练集。D3原有过期观测门控在此保留，没有放宽TTL。高层建议与新观测重新验证的设计属于独立后续变更，不能给旧建议直接换时间戳后执行。

## 可复现入口与指纹

```powershell
python scripts/audit_decision_dataset.py D:/JevUavRuns/20261028_d2_probe01/runs/tracking07_20261030/decisions
```

以下SHA256来自本聊天直接读取文件，不上传输入图像、key或原始HDF：

- `tracking_result.json`: `4160dada07f2ca1ef0ae403a12cbfb968cd36e688f7c25cdb86a995d83d5fb4c`
- `smoke_result.json`: `bbc29630be1d091bc464f48179ff3a5add6720351ff27b7f53e377b2a967e868`
- `Town02_latest_model_real_Jev_ADVICE_ONLY.encoding.json`: `1a619f01e646e1f615244fa8856554edb907b7f12ae69f55bb50a5b548df8385`
- `decisions/jev-000/advice_real_jev.json`: `0c929f1a891aef22fc0b3c67be060e541b977d3e5a0d2d14ca2b6ca6f73b0b6c`
- `decisions/jev-001/advice_real_jev.json`: `6f56b7676ea3f6d78c37fc324b1bb99404a6f45875a2b9210ca3019d76fc6362`
- 账本授权回执SHA字段：`3bd86893f0a52b630d4e5dda3bfcc25c83e96115ad8be99514bd845a109a5cfc`，不是数据库文件哈希。
