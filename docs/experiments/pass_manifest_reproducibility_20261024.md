# PASS manifest 可复现性复核

日期：2026-10-24

本轮为当前闭环对照使用的 5 个显式 PASS episode 生成带 SHA-256 的 manifest，并让批处理在打开 HDF5 前验证每个文件。哈希是流式计算，源文件只读，未扫描或修改 live 采集/上传目录。

## 结果

- manifest：`artifacts/occlusion_pass_20261024/pass_manifest_sha256.json`
- `manifest_version=2`，`integrity.algorithm=sha256`
- 5/5 条目是 `validation_status=PASS`，每个摘要长度 64
- `run_closed_loop_batch.py --verify-manifest-sha256` 成功完成，报告写入 `artifacts/occlusion_pass_20261024/sha_verified.json`
- 哈希验证前后 Mock/utility proxy 可见率保持 `0.689` / `0.734`

哈希验证只证明本次读取的文件内容与 manifest 生成时一致，不证明 HDF5 的视觉标注、物理动力学或身份 crossing 覆盖已经充分；这些仍由 PASS 审核和独立实验报告负责。

## 复现

```powershell
$env:PYTHONPATH = "src"

# 对显式列出的 episode 计算 SHA-256；不会扫描目录
python scripts/build_pass_manifest.py `
  --episode "<PASS episode 1>" `
  --episode "<PASS episode 2>" `
  --expected-schema carlaair_uav_v2_candidate `
  --sha256 `
  --output artifacts\manifests\pass_manifest_sha256.json

# 运行前验证每个条目
python scripts/run_closed_loop_batch.py `
  --manifest artifacts\manifests\pass_manifest_sha256.json `
  --verify-manifest-sha256 `
  --output artifacts\manifests\verified_batch.json
```
