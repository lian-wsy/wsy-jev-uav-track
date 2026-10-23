# 真实遮挡 PASS 与闭环三方回放

日期：2026-10-23

本轮在不启动采集器、上传器、CARLA 服务或真实飞控的前提下，完成了 trimmed HDF5 的只读覆盖审计、遮挡 PASS manifest、跨 episode outcome 训练和固定事件窗闭环回放。闭环回放更新独立的 UAV 常速度状态，目标轨迹和可见性来自记录，观测使用“记录标注 + 几何距离/FOV 代理”。因此它可以比较选择器的相对行为，不能替代 CARLA/ AirSim/实机动力学安全结论。

## PASS 覆盖审计

审计脚本：[`audit_trimmed_pass_coverage.py`](../../scripts/audit_trimmed_pass_coverage.py)

审计根目录是 `D:\CarlaAir\data\trimmed\v3`，文件以只读方式打开：

- 278/278 个 HDF5 可读，读错误 0；状态为 `PASS=259`、`FAIL=5`、`PENDING=14`。
- PASS 中真实遮挡 episode 为 4 个，出框为 3 个，身份交叉为 **0 个**。
- 真实遮挡 PASS 的帧区间分别为：`[176--178, 215--216, 258--261]`（Town10HD bicycle seed260922365）、`[2]`（Town05 pedestrian seed26094743）、`[296--299]`（Town10HD bicycle seed26094706）和 `[296--299]`（Town10HD bicycle seed26094707）。
- 本轮没有制造或改写 identity-crossing 数据；单 actor episode 上得到的身份保持率不能解释交叉鲁棒性，报告会明确标成 `NOT_AUDITED`。

完整审计 JSON 和本轮 manifest 保存在本机被 Git 忽略的 `artifacts/coverage_audit_20261023.json` 与 `artifacts/occlusion_pass_20261023/pass_manifest.json`。

## 训练/验证划分

outcome calibrator 用 4 个 PASS episode 训练（Town05 pedestrian normal-motion、两个 Town10HD bicycle normal-motion、Town10HD bicycle frequent-turns），用独立的 `Town10HD_bicycle_normal_motion_long_seed260922365.hdf5` 验证。训练样本 256，验证样本 64；验证 RMSE 为 visibility **0.2209**、image-center **0.0922**、range-error **2.917 m**、covariance **0.1920**。这是 outcome 校准基线，不是反事实动力学模型。

## 固定闭环窗口

验证 episode：`Town10HD_bicycle_normal_motion_long_seed260922365.hdf5`。固定参数为 `start_frame=168`、`end_frame=225`（exclusive）、`stride=8`，实际比较帧为 `168, 176, 184, 192, 200, 208, 216, 224`。其中 176 和 216 是遮挡采样点，184 和 224 是对应的可见采样点。三种客户端共享同一帧窗、候选原语、训练后的 outcome 模型和硬安全阈值。

完整 JSON：`artifacts/occlusion_pass_20261023/closed_loop_three_way.json`。API key 没有写入报告、日志或 Git；真实 Jev 只接收结构化 belief/history/candidate outcome JSON，不接收 RGB。

| 客户端 | 重捕获次数 | 平均重捕获时间 (s) | 身份保持率 | 安全事件 | fallback 率 | proxy 可见率 | 选择差异 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Mock Jev | 2 | 1.60 | 1.00 | 0 | 1.00 | 0.75 | 与 utility 一致 12.5%，与真实 Jev 一致 25.0% |
| utility | 2 | 4.00 | 1.00 | 0 | 0.00 | 0.375 | 与真实 Jev 一致 12.5% |
| 真实 Jev (`jev-1.14.0`) | 2 | 1.60 | 1.00 | 0 | 0.375 | 0.75 | 8 次请求均无客户端错误 |

这里的“安全事件”是闭环回放中选中动作的预测 unsafe/near-miss 事件；三方均为 0，不能等价为真实飞行零碰撞。utility 选择了扫描动作并改变了模拟 UAV 的视场几何，所以在该固定窗口内出现额外代理丢失，平均重捕获时间为 4.00 s；Mock Jev 保持悬停，真实 Jev 在首帧和遮挡点触发置信度门控，其余帧选择 `descend`，两者均为 1.60 s。真实 Jev 的 fallback 是置信度/滞回路径，不是 API 错误，`client_error_count=0`。

三方身份保持率都为 1.00，但 episode 只有 actor id `25`，所以三份报告的 `identity_coverage.status` 都是 `NOT_AUDITED`。下一步必须获得至少一个包含两个或更多 actor id 的真实 PASS crossing episode，再把交叉期间的 ID switch、误跟随和重捕获分开统计。

## 复现

```powershell
$env:PYTHONPATH = "src"
python scripts/audit_trimmed_pass_coverage.py `
  --root "D:\CarlaAir\data\trimmed\v3" `
  --output artifacts\coverage_audit_20261023.json

python scripts/run_closed_loop_replay.py `
  --episode "D:\CarlaAir\data\trimmed\v3\quota_grid_20260930\Town10HD_bicycle_normal_motion_long_seed260922365.hdf5" `
  --model artifacts\occlusion_pass_20261023\models\rollout_model.npz `
  --env-file .env `
  --start-frame 168 --end-frame 225 --stride 8 `
  --output artifacts\occlusion_pass_20261023\closed_loop_three_way.json
```

若 `.env` 或真实 Jev 不可用，脚本仍会完成 Mock/utility，并把真实客户端构造错误写入 `real_error`；逐帧 API 异常会计入 `client_error_count`，不能被当作真实 Jev 成功。
