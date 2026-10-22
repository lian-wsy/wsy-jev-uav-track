# CarlaAir 数据适配说明

适配器只接受本地 HDF5 路径，使用 `h5py.File(path, "r")` 打开。它不复制、移动、删除或写回源数据；训练模型和报告默认写到仓库的 `artifacts/`。

## 需要的字段

核心字段是：

```text
observation/images/cam_high
state/positions
action/camera_positions
timestamp/image_timestamps
timestamp/position_timestamps
image_state/state_index
```

正式 trimmed 数据还可以提供：

```text
visibility/target_visible
visibility/target_in_frame
visibility/target_occluded
visibility/target_pixel_height
action/camera_target_distance
```

如果 raw vehicle 文件没有 visibility 组，适配器会保守地标记为“未观测标注”，用 actor/state 和相机位姿生成基础状态；这类样本不应与有严格可见性标注的 trimmed 样本混合后直接宣称同等质量。
此时 `TargetBelief.visibility_probability` 固定为 0.5 作为未知先验，报告中的 `visibility_observed` 会保持为 `false`；它不是图像检测器给出的可见率。

## 训练边界

训练脚本按 episode 内时间顺序切分训练和 holdout，避免同一段相邻帧同时泄漏到两边。小规模实验只验证数据能被读取、特征能生成、模型能保存/加载、候选 rollout 能执行和离线选择器能产出报告。正式实验还需要跨 episode 切分、目标身份检查、遮挡审查和闭环仿真验证。
