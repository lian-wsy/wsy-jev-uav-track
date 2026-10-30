# 2026-10-30 续作：Jev 决策与真实后果数据 pilot

用户批准：取消历史对账作为新 Jev 调用前提，开发地图小规模采集 6 episode / 8 个真实决策点；Town03 留作测试。此授权不是将旧历史次数改成零，也不是扩大为完整 D6–D14 矩阵。

## 已完成并验证

- 发现另一个聊天正在共享仓库编写真实视频执行链。获用户授权后协调分工：对方唯一启动仿真，本聊天负责预算/数据记录器。原生 worktree 拒绝嵌套仓库路径，采用独立 Git clone；未覆盖共享未提交改动。
- 实现新 scope 的持久化总计 8 次门控，含发现、失败、重试。一个账本供两任务使用，历史 preparation 仍 UNKNOWN。14 项新测试，连同原预算测试共 31 项通过；独立审查批准。预算提交 `b7e998f`，主仓库集成为 `0aa0516`。
- 实现单写者、独占输出的因果记录器：protocol→input→advice/outcome 哈希关联；拒绝未来/无序时间、Town03 来源、显式 map/seed/split 冲突、篡改或覆盖。Jev 建议不作真值，缺 API 概率不把兼容 one-hot 当置信度。未知安全来源强制无可执行候选，未知指标保留 null，100ms 前缀不伪称 1s 实测。
- 审查发现的候选元数据测试地图漏检、episode 来源冲突、protocol 未被哈希绑定，均先复现失败再修复。记录器 33 项测试独立复核通过；提交 `0523692`，主仓库集成为 `0dbf1f2`。
- 独立副本最终全套 **334 passed / 1 skipped**（原 Windows symlink 权限跳过），compileall/diff 通过。两次明确标 SYNTHETIC 的 smoke 保存本地，无 API、无仿真动作、不可训练、不可算 PASS。
- 每日自动任务保留 09:30，更新了本次新授权、唯一仿真运行方、统一 8 次账本和数据准入边界，避免旧历史对账指令再次阻塞。

## 实际运行与未完成项

只读核对第一轮 `camera_recording01_20261030/smoke_result.json`：FAILED，client_exit_code=1，尚无 client_probe 结果；cleanup_confirmed=true，protected_unchanged=true，postflight listeners=[]。运行方报告原因是二进制地图名 `Carla/Maps/Town02` 未被旧精确允许列表识别，并已回归后修复。首轮失败保留，不重命名为成功。第二轮由唯一运行方继续，不能仅凭发送启动命令认定录制成功。

本次账本只读核对 scope 正确、预约次数 0。当前尚没有本次真实 Jev 对照、六集动作后果或新的 PASS 训练数据。没有对 Town03 训练/调参，没有重新训练 D4/D5。

真实执行仍需完整安全输入。执行侧确认 AirSim API 缺电量字段；不能虚构 100% 放行。相机诊断即便成功，也只证明记录链；安全拒绝必须写 NOT_EXECUTED/metrics=null，不能作零碰撞/跟踪成功/模型收益证据。

下一项：唯一运行方完成现场诊断并提供实际终态，集成已交付记录器；独立审核实际输入/动作/标签及完整 episode 证据后才谈训练。原 D1–D3 尚未整体验收，Town03 PASS 仍缺；目标保持进行中，不伪称已通过。

## 第二轮现场结果核对

本聊天随后只读解析 `camera_recording02_20261030/smoke_result.json` 及编码报告，核对到：API_SMOKE_COMPLETED，实际地图 Carla/Maps/Town02，100 帧，总录制工作耗时 19.849954s；100/100 capture 都是 NOT_EXECUTED、tracking_metrics=null，动作数与真实 Jev 请求数均 0。cleanup_confirmed/protected_unchanged=true，postflight listeners=[]。这是独立实例真实相机诊断，不是跟踪或动作后果数据集。

编码报告 VIDEO_INTEGRITY_VERIFIED：100 输入/100 解码帧，源帧时间跨度 19.622087s，视频呈现跨度 19.64s，差 0.017913s；encoder/decoder exit code 均 0，未请求插帧或复制。输出 SHA256 `d6e313ffe517aa25a21496dac58fa1eda207b69b9c776d84e53d0a62a09d4241`。本聊天检查了报告和全帧状态字段，没有重新运行编码或把编码完整性当模型效果认证。

运行方正在处理真实安全输入和执行链。电量没有测量来源时保持未知；任何另建的仿真资源模型应有明确授权与 simulator_model 标签。六集/八决策 pilot、PASS数据准入和新训练仍未完成。
