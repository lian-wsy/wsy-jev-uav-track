# trimmed HDF5 覆盖复核

日期：2026-10-24

本轮重新运行 `scripts/audit_trimmed_pass_coverage.py`，只读扫描 `D:\CarlaAir\data\trimmed\v3`。审计没有启动、停止、恢复或修改 CarlaAir 采集器、上传器、锁、端口或共享配置；输出 JSON 保存在被 Git 忽略的 `artifacts/coverage_audit_20261024.json`。

| 项目 | 结果 |
| --- | ---: |
| 可枚举 HDF5 | 281 |
| 成功读取 | 281 |
| 读取错误 | 0 |
| PASS | 259 |
| FAIL | 5 |
| PENDING | 17 |
| PASS 真实遮挡 | 4 |
| PASS 出框 | 3 |
| PASS 可见性下降 | 7 |
| PASS 高加速度 | 12 |
| PASS 身份交叉 | 0 |

相比上一份 278 文件快照，本次枚举到 281 个文件且 PENDING 为 17；这只表示本次只读审计看到的目录状态，不能推断上传或采集已经完成，也不改变任何样本的接受状态。训练、验证和闭环报告仍只使用显式 manifest 中的 PASS episode。

最关键的阻塞仍是身份交叉覆盖为 0。当前单 actor episode 的身份保持率可以用于检查 tracker 的基本粘性，但不能解释 crossing 下的误跟随、ID switch 或重捕获鲁棒性；在获得新的、独立 PASS 审核的多 actor episode 前，相关指标继续标记为 `NOT_AUDITED`。

复现：

```powershell
$env:PYTHONPATH = "src"
python scripts/audit_trimmed_pass_coverage.py `
  --root "D:\CarlaAir\data\trimmed\v3" `
  --output artifacts\coverage_audit_20261024.json
```
