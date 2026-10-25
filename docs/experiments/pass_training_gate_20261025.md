# 单 episode 训练 PASS 门控

日期：2026-10-25

## 问题与修复

跨 episode 入口已有 PASS 检查，但单 episode 的 `build_training_dataset` 没有检查审核状态，`train_rollout_model.py` 因而可能拟合 FAIL、PENDING 或无审核状态的数据。现在两个入口都要求 `validation_status=PASS`，在读取训练帧状态和构造标签前拒绝未审核输入；absolute 与 baseline_residual 都执行相同检查。

此检查使用 adapter metadata 中的审核结果，不替代独立数据审核或文件哈希验证。HDF5 adapter 仍可用于独立诊断；直接提供数组的底层拟合接口不验证源 HDF5。

## 验证

- 8 个参数组合覆盖 FAIL、PENDING、缺失、空状态 × 两种 target mode；metadata-only stub 验证单/多 episode 入口都在访问帧状态前拒绝。
- 全量 `python -m pytest -q`：34 passed；`python -m compileall -q src scripts` 通过。
- 最小真实数据 smoke：只读 `Town10HD_bicycle_normal_motion_long_seed260922365.hdf5`，先与已有 `artifacts/occlusion_pass_20261024/pass_manifest_sha256.json` 第一项比对 SHA-256，通过后运行 residual 单 episode 训练。
- 源摘要：`535a7e32ea3f5650dfe82330fccb7f90fc5c71b091f542457a0872c00a498c1d`。
- 32 样本（22 训练 / 10 时间顺序验证）训练成功；模型和 JSON 写入 `artifacts/pass_training_gate_20261025/rollout_model.npz` / `.json`。

这是数据准入与训练流程回归，不是新的跨 episode 泛化实验；该模型不替换已有四训一留出模型，不据此声称跟踪性能提升。未调用真实 Jev，未操作采集、上传、锁、端口或共享配置。

## 最小复现

```powershell
$env:PYTHONPATH = 'src'
$manifest = Get-Content artifacts/occlusion_pass_20261024/pass_manifest_sha256.json -Raw | ConvertFrom-Json
$entry = $manifest.episodes[0]
if ($entry.validation_status -ne 'PASS') { throw 'Manifest entry is not PASS' }
$actual = (Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $entry.sha256) { throw 'SHA mismatch' }
python scripts/train_rollout_model.py --episode $entry.path --target-mode baseline_residual --max-samples 32 --output artifacts/pass_training_gate_20261025/rollout_model.npz
```
