# D5：统一因果输入的校准与动作诊断

2026-10-30。修复基线 `3a163e7`。本轮复用冻结PASS开发数据，不等待Town03，不调用仿真、RGB或真实Jev，不向视频聊天发送消息。旧模型/报告保留，新输出目录为 `artifacts/d5_causal_calibration_20261030/`。

## 修复内容

此前D5有三处oracle入口：训练配置沿用默认oracle、采样一致性断言重建oracle特征、候选动作诊断再次用oracle状态。只把训练配置改成causal_visible不足以修复完整链路。

现在D5默认 `causal_visible`，仍支持显式 `--input-mode oracle` 复现历史实验。拟合、区间校准、validation标签对应的当前状态和候选诊断均使用同一输入契约。`sampled_inputs()`按时间顺序消费所有过去图像帧后抽样，并在生成候选前严格检查其特征与训练数据构建器相同。候选诊断继续使用默认3m间距等硬约束，不把训练标签构建的宽松约束带入安全选择。

隐藏位置、身份、目标oracle速度不进入当前特征；未来真值仅作为标签。冷启动不可见时用自身位姿先验。原始可见性标注仍用于分层评估，但不把目标真值喂回候选状态。每帧记录 `input_mode` 与缺失安全观测，权重摘要、protocol和report保留输入模式。缺可见性/相机位姿来源拒绝运行。

## 固定实验

- 原划分不变：28 train按内容SHA升序，前10集calibration，余18集fit；6 validation不变，5 stress和final test不读取。
- 96样本/集、stride2、5帧约1秒、ridge alpha1、区间alpha0.1、期望距离20m；不搜索参数、seed或回退阈值。
- 重新拟合18集模型是必要步骤：旧D5为oracle输入，已训好的28集因果模型包含这10个calibration episode，直接拿它校准会失去拟合/校准隔离。
- 34个源文件逐个完整SHA/大小/PASS/mtime/帧数核对后只读state，限速16MiB/s。协议与源码SHA在读取/拟合前保存。
- 评估coverage、区间宽度、记录动作/候选回退、越界支持域、未门控utility排名和硬安全可选集合；缺困难样本不视作通过。

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$env:PYTHONPATH=(Resolve-Path src).Path
python scripts/run_d5_calibration.py --split "D:/WPS Cloud/WPSDrive/1574147233/WPS云盘/wsy-jev-uav-track/artifacts/acceptance_20261028/development_split_v2.json" --output artifacts/d5_causal_calibration_reproduction_new --samples-per-episode 96 --calibration-episodes 10 --alpha 0.1 --hash-mib-per-s 16 --input-mode causal_visible
```

需新输出目录；不覆盖已完成的实验。

## 软件验证

新增回归先复现6 failed / 2 passed；修复后D5/区间/因果训练针对性32 passed，全套389 passed / 1 skipped，编译与diff检查通过。回归覆盖隐藏/未来真值扰动下候选输入不变、非默认期望距离、特征断言、缺测拒绝、默认causal完整运行、权重保存重载及显式oracle兼容。

## 完整实验结果

进程退出0，`report.status=COMPLETED`；34/34源文件通过SHA/大小/PASS/mtime/帧数校验。新旧protocol的manifest SHA和18/10/6完整成员顺序均相同。1728拟合样本、960校准样本、576验证样本；模型重载与源码SHA核验通过，576条诊断全部标为causal_visible且保留未知电量/间距信息。公开[机器报告](evidence/d5_causal_calibration_20261030.json)与本地report的JSON内容一致。

| 同一576验证样本 | visibility代理RMSE | 中心代理RMSE | 距离RMSE（m） | covariance代理RMSE |
| --- | ---: | ---: | ---: | ---: |
| transparent | 0.134729 | 0.282904 | 0.243116 | 0.261600 |
| raw residual（18 fit） | 0.127625 | 0.047471 | 0.426339 | 0.257696 |
| interval_gated | 0.134729 | 0.282904 | 0.243116 | 0.261600 |

距离预测退化；6集只有2集改善、4集退化。episode等权平均距离RMSE从0.201443m升至0.306750m。Town02 bicycle frequent_turns long seed26094451退化最明显（0.103402→0.956688m），不能剔除它后报收益。此前28集因果模型与本次18集模型的训练集不同，不能用两者数值差异单独证明区间算法有效或无效。

| 输出 | 区间总宽 | validation经验覆盖 |
| --- | ---: | ---: |
| visibility代理 | 1.207006 | 100% |
| 中心代理 | 0.854224 | 100% |
| 距离（m） | 7.438893 | 100% |
| covariance代理 | 2.433423 | 100% |

这里是未裁剪原始输出区间；visibility宽度大于1不代表概率合理或区间实用。名义90%对应的经验100%覆盖仍来自很宽的区间。距离半宽3.719447m由校准集Town07 bicycle normal_motion long seed26094516的最大残差决定，不根据验证结果删样本、改alpha或缩窄区间。

- 576/576记录动作全部输出回退；4608/4608候选全部输出回退，**门控后等价透明基线，没有learned收益证据**。
- 3174/4608候选超出fit边际框；左右绕行、升降、扫描五类全越界。边际框内也不能证明有反事实动作支持。
- 原始residual改变288/576个未门控utility第一名；区间门控后改变0。Mock与utility一致是Mock的构造性质，真实Jev请求0。
- 576/576帧被默认硬安全过滤拒绝；实际动作0。不能把缺测造成的拒绝记成安全事件改善或重捕获成功。
- validation当前输入visible=576，遮挡/出框/未知样本均0，保持NOT_EVALUATED。

新模型SHA256：`b67c0a70e50426dcebfff6a97adfaa3f286e1d26fe6a05e4f26e44a97f8ee189`。运行前后核对，旧D5 `a4fbeb5590d3d9e154f793ee2a702ae49a44c89dff4997e712fd17ebeb451000` 和28集因果residual `a3542d8e3f2730f8c0a597fa83b3c9fc225c50e147aa047c7b56f5f0fd01b560` 均未改变。本次不自动部署或替换视频模型。

## 结论及剩余问题

D5输入契约修复和现有PASS数据上的重跑已完成，软件链路通过；模型有效性未达标。主要限制仍是困难场景缺样本、动作后果支持不足、未测安全状态及可见oracle与真实视觉的差异。当前不再以Town03缺失阻塞离线训练；Town03仍保留最终独立测试用途，不回流训练。下一步若开展改进，先在开发数据中补真实困难场景与动作后果，再按冻结规则验证，不用更换seed/降低门控掩盖本轮负结果。D1–D5整体不改记完成。
