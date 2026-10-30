# 手动体验入口与当前限制

当前版本是研究原型，还没有“鼠标框选目标→无人机持续跟随”的完整交互界面。离线动作选择可以立即体验；真实CARLA/AirSim持续跟随由独立运行聊天验证中，不能把短动作录像当成稳定持续跟踪。

## 立即运行，不占仿真实例

本机使用包含新修复的独立分支：

```powershell
cd D:/JevUavRuns/repos/jev-outcome-dataset-20261030
$env:PYTHONPATH = (Resolve-Path src).Path
python -m jev_uav_track.demo
```

已实际运行通过，会输出6步 `choice/confidence/visibility/reason`。这是Mock Jev加透明运动学的文本演示，不加载训练权重、不读取真实视频、不调用API、不控制无人机。`confidence_gate` 表示置信度回退，不等于Jev产生高置信度决策；`visibility`是预测值。

## 加载已训练模型检查记录数据

参考[D3复现命令](experiments/d3_causal_evaluation_20261030.md)，它加载保存的causal_visible residual模型，只读一个已核验的validation episode。看报告中的 `input_mode`、`records` 和 `blocked_records`。目前该数据没有电量/实时间距，四个抽样帧全部被安全门控拒绝，这是正确的拒绝结果，不能凭空补充传感值来演示运动。

`--input-mode auto` 会跟随模型声明；无模型时默认旧oracle，想检查因果透明基线须显式传 `--input-mode causal_visible`。不把oracle离线得分当视觉跟踪效果。

## 真实仿真体验应如何进行

在开发地图Town02上，由唯一运行方使用任务独立发行副本启动：加载固定模型、选择已实现的目标场景，先显示原生相机画面，再执行有限时长自动跟随，保存动作、拒绝原因和录像。目标由场景脚本创建，目前不是用户在画面中任意框选。Jev选择高层行为，最新观测驱动低层控制，两者作用不同。

当前这条持续跟随路径尚在另一聊天“设置 Jev 聊天目标”修复验证，本分支不包含该聊天未提交的控制器。不要在其运行期间双击外部CarlaAir启动器、另开控制脚本、清理端口或修改共享settings；不要用Town03调试跟踪效果。API key只从进程环境读取。

可用的验收结果应至少包含：目标持续运动、无人机连续响应、画面/框/动作时间对应、失去观测后停止使用旧位置，以及限定时间结束后的停止和清理。仍需完成这些检查后，才能提供可复现的实时体验启动命令。本文不把未验证命令当成现成功能。
