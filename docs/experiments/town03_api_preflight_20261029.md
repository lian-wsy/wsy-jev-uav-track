# Town03 API-only启动支持与受限预检查

2026-10-29，Asia/Shanghai。落实用户已选择的Town03保留地图：为现有本任务独立启动器增加地图参数和实际地图核验。本轮不采集、不训练、不执行跟踪策略，不把基础设施检查当final test。

## 实现

- `--map Town02|Town03`，默认Town02不变。仅接受精确名称，不接受任意路径、URL附加选项或Town03_Opt。选择Town03时，launcher与API client均在任何启动/连接前拒绝contract、observation、belief；只允许api。
- launcher把同一地图传给Shipping与client；client在读取观测之前要求CARLA实际地图名精确等于Town03或`/Game/Carla/Maps/Town03`，其他地图及Opt变体拒绝。launcher再次检查client的实际地图、requested_map和map_match，再决定API_SMOKE_COMPLETED。
- api沿用原来的12次状态/时钟读数、版本/车辆/settings查询，不解锁、不发动作、不reset、不生成RGB/目标观测，也不调用Jev。不是完全无副作用：成功启动引擎时会创建本任务自己的仿真世界和缓存；本次未走到启动。
- 专用端口、完整settings匹配、保护文件SHA、Job与原始进程句柄清理、启动前4GiB和启动中768MiB内存门槛不变。每次新run-name保存run_request.json和preflight.json，失败目录不复用。

## 本轮真实预检查结果

| 项目 | 证据/结果 |
| --- | --- |
| 独立根 | `D:/JevUavRuns/20261028_d2_probe01`，已有完整副本，不重新复制 |
| 只读准备核验 | 副本二进制SHA等于prepared；原包/共享AirSim保护文件SHA与准备记录一致；任务settings和Town03资产存在 |
| 运行 | `town03_api01_20261029`，profile=api，map=Town03 |
| 启动前RAM | 3,237,528KiB，约3.09GiB，低于4,194,304KiB门槛 |
| 进程/端口 | 预检查只出现本次launcher Python PID46468，无相关监听；没有外部实例被借用或操作 |
| 判定 | Exit1，`Less than 4 GiB physical memory free; no launch`，未创建Shipping/owner.json |
| 收尾只读检查 | 未列出Carla/UE4/Unreal进程；未重试、不降低门槛、不结束其他程序释放资源 |
| 验收 | Town03实际加载仍NOT_VERIFIED，PASS episode仍0，D1独立数据接纳未完成 |

[机器证据](evidence/town03_api_preflight_20261029.json)保留本轮源码原始字节SHA；Git在Windows切分支可能转换换行，比较时应区分字节换行与代码变更，不能对数据/二进制SHA采用文本规范化。

## 测试与复现

新增16项先失败后通过的地图/客户端行为回归，再补低内存保留请求证据且不创建进程的测试，共17项通过。全套 **267 passed, 1 skipped**（既有符号链接权限用例跳过），compileall/diff检查通过。客户端测试用严格假RPC表面，没有运动/图像API；错地图在观测前拒绝，Town03动作profile在连接前拒绝。独立只读代码审查无阻断问题；launcher成功报告字段核验还主要由代码审查覆盖，不能据此声称live成功。

```powershell
python -m pytest -q tests/test_map_probe.py tests/test_isolated_runtime.py
python -m pytest -q
python -m compileall -q src scripts
$env:PYTHONPATH=(Resolve-Path src).Path
python scripts/probe_isolated_carla.py smoke --source D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor --task D:/JevUavRuns/20261028_d2_probe01 --client-python C:/Users/20748/miniconda3/envs/carlaAir/python.exe --run-name town03_api02 --profile api --map Town03
```

最后一条会尝试在所有既有门槛满足后启动本任务实例，非只读命令；重跑使用新的名字，当前不执行重试。资源不足时先由用户自行结束不需要的普通应用或另提供隔离资源；不授权本任务停止采集/上传或改动系统配置。

下一步是资源满足后有界API-only实测，不是Town03策略调参。训练与D5校准仍用原开发集，执行回路开发仍在开发地图；正式Town03测试仍须冻结模型/配置/场景/seed和预算，审核PASS后接纳。Jev历史attempt对账不因地图代码就绪而解决。
