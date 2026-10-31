# Town03 独立实例：地图选择修复与现场核验

日期：2026-10-31。范围仅为D2/D6隔离基础设施；不采集episode、不解锁、不运动、不reset、不取图、不评估策略。旧HDF、训练权重、Jev服务和视频任务均未使用。

## 失败证据与根因

先前api01/api02在启动前发现额外Python而拒绝；随后只读检查和启动器自身snapshot均不再发现额外进程。该变化不是已证明的启动器bug，不把之前的拒绝改为成功。

`town03_api03_20261031`在原4GiB启动门槛下启动了本任务独立副本：Shipping PID25724，专属3200/3201/3202/42461。实际地图却是`Carla/Maps/Town02`，客户端按预期拒绝，原错误为：

```text
ValueError: Loaded map mismatch: expected Town03, observed Carla/Maps/Town02
```

此次已通过task-owned settings检查，但未进入12次状态采样。启动前可用5460460KiB，最后运行快照3404108KiB；protected_unchanged与cleanup_confirmed均true。原失败完整保留在`D:/JevUavRuns/20261031_d6_probe01/runs/town03_api03_20261031/`。

只读对照发行包`CarlaAir.ps1`第264–280行：原脚本不仅添加project/map参数，还更新`DefaultEngine.ini`的`EditorStartupMap`、`GameDefaultMap`、`ServerDefaultMap`。我们的独立启动器之前只复制了参数逻辑；实际独立副本的三个默认项仍为Town02。未执行原共享脚本，也未用`load_world()`重置保留地图。

## 修复边界

`OwnedMapConfiguration`只在独立副本中暂时切换这三个键。源与task根必须分离；拒绝配置/路径链接、硬链接、缺失或重复的地图键。先独占创建原始字节备份和SHA审计，再在同目录原子替换配置。

启动器在资源/进程/端口门控之后才修改配置。持有原始进程句柄并清理自有进程后，恢复原始字节、读回核验SHA。无法确认进程退出时不恢复，保留备份并记AUDIT_FAILED；配置遭意外修改也不覆盖。job/进程清理、保护文件或CIM审计异常均记录，不能丢失原始失败。审计文件存在或变成链接时不盲目覆盖。

保持4GiB启动/768MiB运行门槛与全部Town03 API-only限制。独立目录`D:/JevUavRuns/20261031_d6_probe01`，不访问视频任务保留运行目录，不占用3100/3101/3102/42451。

## 验证

- 初始回归RED：缺少配置守卫及launch边界地图修改，共9项失败；实现后相关63项通过。
- 异常路径RED：审计硬链接2项、job/process/client退出异常3项，共5项按预期失败；修复后相关68项通过。
- 主代理全套：`python -m pytest -q`，495 passed / 1 skipped；`python -m compileall -q scripts src`及`git diff --check`退出0。LF/CRLF规范化提示不作为测试失败。
- Python3.10确认Windows reparse标记可用。独立审查另跑42项通过，批准一次受限probe；指出非阻断的大小写变体重复键问题，当前发行配置只有规范键名。未泛化为任意INI格式支持。

## 现场复验

独立审查后只执行一次新的`town03_api04_20261031`，退出0，状态`API_SMOKE_COMPLETED`。实际返回`Carla/Maps/Town03`，settings_match与map_match均true，车辆列表仅JevDrone；12次状态读取完成。CARLA client/server均报告`7a7fdb3-dirty`，AirSim协议server version为1（不是发行包版本1.0）。

启动前空闲5001552KiB；最后运行快照2875316KiB，未触发768MiB门槛。这是离散快照，不称连续最低内存。专属四端口全部归Shipping PID80104；整个受限probe含清理约21.59秒，不作为API延迟benchmark。

原配置SHA为`07b8a71e8df9d8bfdaaea4d04dc15868b73339cbc51f8ca16239e350b6267e64`，临时Town03配置SHA为`893d31f60652474629ba42eb16d16bd2788dae1f3b26457336f434bf55172035`。退出后原字节和SHA恢复，两个进程句柄均确认退出；protected_unchanged、map_config_restored和cleanup_confirmed均true，cleanup_errors为空。最后额外只读复查未发现模拟器/Python残留或专属端口监听；8个受保护配置SHA再次与原始回执一致。

```powershell
$env:PYTHONPATH=(Resolve-Path -LiteralPath 'src').Path
python scripts/probe_isolated_carla.py smoke --source 'D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64/WindowsNoEditor' --task 'D:/JevUavRuns/20261031_d6_probe01' --client-python 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' --run-name town03_api04_20261031 --profile api --map Town03 --carla-rpc-port 3200 --carla-streaming-port 3201 --carla-secondary-port 3202 --airsim-rpc-port 42461
```

上述为已运行命令，目录不可复用；再次运行须重新核验资源与授权并使用全新run-name，不应立即重复。`--bootstrap takeoff`虽为通用CLI默认值，但`profile=api`不调用bootstrap或任何运动函数。

两次实际启动（03失败、04成功）和完整12条状态、原日志SHA及源配置复查见[机器证据](evidence/town03_api_map_fix_20261031.json)。原01–03证据均不改写。预检查在main try前失败仍只留下request/preflight；若最终证据写盘本身失败，也不能保证存在smoke_result。当前版本没有把这些未验证路径宣称为完整清理保证。

新增Town03 PASS episode仍为0；API连接成功不满足D1数据验收，不证明D3新实例原点、D5困难场景覆盖或D6真实控制执行。**已解除地图加载/接口前置阻塞，D1–D6整体仍PARTIAL。** 后续在开发地图准备真实受限控制运输层/测量链，Town03继续只作保留测试；新方法无关采集需另有完整审核协议，不借此接口成功自动扩大动作权限。
