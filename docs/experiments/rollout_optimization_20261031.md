# Rollout优化：数据规模、模型表达与校准修正比例

日期2026-10-31。代码基线31391c6。用户授权继续优化；本轮离线处理，不启动仿真、调用Jev、修改外部采集上传或联系视频聊天。Town03仍为最终测试保留地图。

## 问题与预先固定的比较

上轮D5因果输入已贯通，但18集residual距离RMSE退化，区间门控全部回退。本轮将数据规模、模型形式和修正门控分别列为实验因素，完整保留所有组合，不用validation挑参数后把它称作独立测试。

原28 train中的10个calibration按既有SHA规则固定，其余18 fit取同一SHA顺序的前6、12、18集；6 validation保持原样。样本96/episode，stride2，horizon5帧约1秒，当前输入causal_visible。拟合、校准、验证共享相同输入构建器，所有过去帧按顺序进入估计器，未来真值仅供标签。旧模型不覆盖。

每个规模分别拟合两个模型：

1. 线性ridge residual，alpha1，fit-only标准化。
2. 64维固定随机傅里叶特征（RFF）的非线性ridge residual，alpha1；标准化只用fit，投影种子20261031，频率尺度1/sqrt(输入维数)。不搜索随机种子、核尺度、正则或模型宽度。

非线性模型是用于比较的轻量模型，尚未接入在线控制器。六个模型均单独保存NPZ并重载核对预测；NPZ保存归一化、投影、拟合范围和权重，输入契约/模型身份由同目录protocol与report的SHA关联，不冒充旧LinearOutcomeModel格式。

每个模型报告四种结果：透明baseline、原始residual、旧区间门控、校准修正比例。旧规则仍按alpha0.1的episode最大误差区间和fit边际框。新规则在固定网格0/0.25/0.5/0.75/1中按输出选修正比例：最小化10个calibration episode等权MSE，先应用同一fit边际框，平局选更小修正。0意味着不用学习修正。

新规则针对的是校准预测误差，而不是“修正量显著不等于零”。这是启发式模型选择，不是有限样本覆盖或安全保证；不能在同一校准集上选完参数，再用该集的好成绩当独立有效性证据。validation只报告已冻结选择后的结果。候选修正被采用的比例仅为输入/支持域诊断，不代表反事实动作收益或飞行授权。

## 数据与执行

使用development_split_v2 SHA `7326f07e67e90ed0e6397bb553abf31e23cd8d594cf647532a5b02447b9f8e87`，34源文件按16MiB/s串行SHA/大小/PASS/mtime核验后只读state；不打开5集stress或final test。运行前写protocol，包含划分、全部超参数和源码SHA。

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python scripts/run_rollout_optimization.py --split "D:/WPS Cloud/WPSDrive/1574147233/WPS云盘/wsy-jev-uav-track/artifacts/acceptance_20261028/development_split_v2.json" --output artifacts/rollout_optimization_reproduction_new --hash-mib-per-s 16
```

必须新输出目录。主要产物为protocol.json、verified_reads.jsonl、六个模型NPZ、包含全部逐集结果和候选诊断的report.json。不自动选择或部署验证集冠军。

## 解释边界

- 一个固定嵌套顺序仍混合了样本数量与新增场景组成的作用，不能凭三点曲线断言“唯一原因是数据不足”。
- 记录相机速度是动作代理；没有不同候选动作的真实执行后果。模型可改善记录动作预测，但不证明左右绕飞/升降动作预测正确。
- 位置/身份来自可见标注，部分输出是规则代理；不是RGB视觉跟踪。
- 同一6集开发validation已用于历史诊断，不是新独立最终测试。Town03仍未使用。
- 不因为门控采用率提高就声称效果提升。必须同时报告误差、逐episode变化与回退；无真实困难样本继续NOT_EVALUATED。
- 报告中的 `episode_equal_rmse` 定义为逐episode RMSE的算术平均，不是先平均MSE再开方；校准选择优化的是episode平均MSE。`aggregate_rmse`是全部验证样本汇总RMSE，本轮每集等长96点，两种聚合口径须分开解释。
- 单集训练数据构建器仍检查实际时域与1秒相差不超过0.05秒；本轮使用此前已验证约0.999999978秒的同一冻结源数据。新runner不额外输出逐集时域，因此本实验不推广到混合采样周期数据。

## 验证与结果

新增模型/实验流程6项回归先失败后通过；全套395 passed / 1 skipped，编译与diff检查通过。独立只读审查没有阻断问题；上述聚合指标和时域证据边界按审查意见明确。

真实实验退出0，34/34 SHA/大小/PASS/mtime/帧数核验完成，六个组合全部完成。运行后源码、六个模型SHA、新旧manifest/完整划分、34源文件SHA/大小对应关系核验一致。六个模型均保存重载预测一致，CARLA Python3.10也可加载并输出有限预测。公开[机器证据](evidence/rollout_optimization_20261031.json)与原始report JSON内容相同；模型仍保存在本机独立运行目录。

## 六组距离预测结果

同一576个validation样本，透明baseline距离RMSE为0.243116m。下面列出全部预先固定组合，没有删除退化配置；旧门控在这六组的距离输出上均等价baseline。

| fit集数 | 模型 | 原始residual RMSE（m） | 校准比例+支持门控RMSE（m） | 校准选定距离比例 | 改善/持平/退化episode |
| ---: | --- | ---: | ---: | ---: | --- |
| 6 | linear | 0.312705 | 0.230469 | 0.25 | 2 / 1 / 3 |
| 6 | rff64 | 0.304080 | 0.243116 | 0 | 0 / 6 / 0 |
| 12 | linear | 0.485079 | 0.230307 | 0.25 | 2 / 1 / 3 |
| 12 | rff64 | 0.250908 | 0.229185 | 0.25 | 5 / 1 / 0 |
| 18 | linear | 0.426339 | 0.207012 | 0.5 | 2 / 1 / 3 |
| 18 | rff64 | 0.235156 | 0.199700 | 0.5 | 5 / 1 / 0 |

按预定最大18集配置展开：rff64原始预测的汇总误差略优于baseline，但逐集RMSE平均0.203318m仍略高于baseline的0.201443m；采用校准修正比例和支持门控后，汇总RMSE降17.86%，逐集RMSE平均降至0.166161m。两种口径均改善，但只有6个历史开发validation episode，不宣称统计显著性、未见地图泛化或论文结论。没有根据这个排名自动选择部署模型。

| validation episode | baseline距离RMSE（m） | 18集rff64校准后（m） |
| --- | ---: | ---: |
| Town05 bicycle normal_motion close seed26094529 | 0.188984 | 0.173984 |
| Town06 bicycle normal_motion close seed26094538 | 0.264341 | 0.147979 |
| Town10HD pedestrian frequent_turns long seed26094464 | 0.096728 | 0.095509 |
| Town02 bicycle frequent_turns long seed26094451 | 0.103402 | 0.103402 |
| Town07 pedestrian normal_motion long seed26094827 | 0.471078 | 0.402310 |
| Town05 pedestrian frequent_turns long seed26094457 | 0.084126 | 0.073783 |

Town02不是模型学好了：其记录动作特征全部超出fit边际框，仍用baseline。该集rff64原始误差0.407398m，高于baseline；必须保留此失败。18集linear同样受支持门控保护，虽然汇总改善，仍有三个episode退化。

## 校准与动作诊断

18集rff64的修正比例按visibility/center/range/covariance顺序为1/1/0.5/1，全部来自10个calibration集；不是使用validation找出的blend。与baseline相比，该配置的四个代理输出汇总RMSE依次为：visibility 0.134729→0.056735，center 0.282904→0.279044，range 0.243116→0.199700，covariance 0.261600→0.109583。它们仍是记录数据代理指标。

4608个候选中3174个超出fit边际框，保持回退；1434个候选（31.12%）获得至少一维非零修正，旧门控对该配置为0。**采用率不是安全通过率或跟踪成功率**；新实验不执行动作，也没有对候选后果的真实监督。原有硬安全门控不修改，未知障碍/电量不能凭这个诊断获得执行许可。

validation输入可见576，遮挡/出框0；困难场景继续NOT_EVALUATED。当前改进不证明重捕获、身份保持或安全事件改善。真实Jev请求0，不把rollout改进归因于Jev。

## 对原因与下一步的判断

本轮支持“模型形式和修正规则都值得改进”，不支持“只需增加同类型数据”。linear的原始误差随6/12/18集不单调改善；rff64趋势改善，但数据数量与加入场景组成混杂，不能做唯一原因判断。校准误差驱动的缩减修正避免了旧规则全部拒绝，并在此次固定validation上取得较一致收益；这是描述性开发结果。

本轮优化代码、可复现对照和报告已交付；旧D3/D5模型保留，不自动部署。下一步应先补开发地图的遮挡/出框和候选动作实际后果，再验证本规则；方法冻结后再用Town03独立评价。不能在这6集上无限选择模型后声称最终泛化，也不能为提高采用率移除支持约束。
