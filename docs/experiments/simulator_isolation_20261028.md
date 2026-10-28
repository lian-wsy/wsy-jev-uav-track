# D2 独立实例可行性复核

结论：配置隔离有静态实现线索，但不能证明当前具备安全独立实例，状态为 `BLOCKED_PENDING_RESOURCE_OWNERSHIP`。本次未运行发行启动器、未启动/停止任何仿真或外部任务、未连接控制RPC、未修改配置/锁/端口。

## 可核验的版本与路径

源码根 `D:/CarlaAir/repos/CarlaAir`，HEAD `d0c3fd717d5983fa488bedcdc055ebb2278c8780`，提交时间2026-07-21，有既存本地改动，不修改。发行根 `D:/CarlaAir/CarlaAir-v0.1.7-Windows11-x86_64`，README标记v0.1.7，源码及发行DefaultGame.ini写ProjectVersion=0.9.16；这些是不同版本层级，不可混称。

发行 `WindowsNoEditor/CarlaUE4/Binaries/Win64/CarlaUE4-Win64-Shipping.exe` 为93,875,712字节、mtime 2026-03-28，版本字段空。SHA-256：`e39091f72c148db0c6821d4dcbbbba55d768fe67e3320707c4007d2db784b0d7`。NonUFS manifest仅有时间，无法绑定源码Git commit；未读取921MB PDB。源码结论因此必须经过独立实例验证才能上升为二进制行为结论。

## 配置路径证据

源码相对根 `Unreal/CarlaUE4/Plugins/AirSim/Source/`：

- `SimWorldGameMode.cpp:1222`、`SimHUD/SimHUD.cpp:333`：优先命令行settings，其后exe目录、launch目录、用户Documents。
- `SimHUD/SimHUD.cpp:341–361`：示例为 `--settings "path"`，实现查找 `-settings` 后截取余下全部文本。因此候选必须把该参数放在最后，不能在路径后继续添加参数；单/双短横线的实际构建解析仍待验证。
- `AirLib/include/common/AirSimSettings.hpp:453–465`：缺设置时可能生成用户Documents/AirSim/settings.json；UE UserDir不会自动证明AirSim的Documents路径已隔离。
- `AirLib/include/common/AirSimSettings.hpp:1118`：LocalHostIp/ApiServerPort；CARLA的 `Plugins/Carla/Source/Carla/Settings/CarlaSettings.cpp:125–136` 支持独立RPC、streaming、secondary端口。

发行exe只读字符串中有 `-settings`、`UserDir=`、`SaveToUserDir`、`abslog=`及三个CARLA端口开关，没有观察到 `SAVEDIR=`。字符串证明存在候选解析线索，不能证明Saved/config/crash/cache的全部写入路径。

共享 `CarlaAir.ps1:175,181,254–275` 会停止旧PID或同名Carla进程，覆盖Documents/AirSim/settings.json，改DefaultEngine.ini；本任务禁止调用。不能通过“先备份再恢复”覆盖正在使用的共享配置。

## 待验证独立配置清单

所有可写路径归入本任务 `artifacts/isolated_sim_d2/`，不创建在采集根。以下为计划，不是已分配资源或可执行启动脚本：

| 项 | 候选值 | 实际验收 |
| --- | --- | --- |
| 可执行文件 | 上述固定SHA Shipping exe或本任务专属完整副本 | 未启动；源目录只读 |
| 工作目录/输出 | task root内runtime、logs、recordings、user | 需证明所有Saved/config/crash/cache写入均落本任务 |
| AirSim配置 | task settings.json；LocalHostIp=127.0.0.1，ApiServerPort=42451，Recording.Enabled=false且Folder显式 | 参数解析失败必须中止，不能回落共享Documents；尚待实测 |
| CARLA | RPC3100、streaming3101、secondary3102 | 候选空闲不代表拥有；需任务专属lease/进程父子证据 |
| UE可写目录 | UserDir+SaveToUserDir，abslog明确task路径 | 二进制字符串存在，不等于完整隔离已证明 |
| TrafficManager | 不启动auto_traffic、不使用共享8000 | D2 smoke不需要外部交通链 |
| 启停 | 只登记和清理由本任务启动的精确PID+创建时间 | 不允许名称匹配停止或共享PID文件 |

当前不生成默认可运行的launcher，避免将未核验开关误当安全执行条件。若无法证明共享发行目录完全只读，改用独立用户/机器或完整任务副本，保持原采集工程不动。

## 时间同步是另一道门槛

CARLA `CarlaEngine.cpp:284–289` 同步模式等待TickCue，固定delta由CARLA引擎设置；AirSim `AirLib/include/physics/World.hpp:43–45`在自身物理update里step时钟，`PhysicsWorld.hpp:127`启动异步更新，`SimModeWorldBase.h:73`默认周期3ms。`AirSimSettings.hpp:1204`的SimpleFlight默认SteppableClock。

因此CARLA tick、AirSim physics step和相机采样不是天然同一时钟。后续独占smoke必须记录wall/CARLA/AirSim/image时间，验证暂停和恢复、1秒命令实际时长、相机配准及丢帧；当前5Hz/25Hz HDF对齐不能替代现场同步证明。

## 资源与外部所有权快照

2026-10-28 15:06–15:09 +08：RTX5070Ti Laptop显存12,227MiB、可用9,476MiB、利用率11%；系统总内存约15.8GiB，可用约4.95GiB；D盘约111.1GiB空闲。稍后再次只读检查可用RAM降至1,949,712KiB（约1.86GiB），GPU可用6,633MiB、利用率47%。资源在其他任务活动中变化，不能用早先快照保证新增仿真可行。

早先未见UE4，候选端口无监听；Ayaka dashboard PID4668/父16864监听8767，属于外部任务。transfer lock存在且无可用owner JSON，部分control标paused；Town06/07 formal_status虽写COMPLETE，formal_launcher读取IOException，不能当移交证据。不会停止这些进程或修复外部文件来满足本任务。

## D2 验收判定

已完成：版本与二进制指纹、坐标/时钟源码证据、只读资源调查、接口骨架、独立配置候选、[调用费用规划](jev_budget_20261028.md)。

未通过：实际独立实例所有权、完整可写路径隔离、足够且可承诺的内存资源、二进制设置/时钟smoke。关闭其他任务或借用采集实例不在权限范围；需要可证明独立的资源条件，不能为“今天验收”降低这道门槛。真实闭环属于D7目标，本报告未把D2阻塞改写为D7已完成。
