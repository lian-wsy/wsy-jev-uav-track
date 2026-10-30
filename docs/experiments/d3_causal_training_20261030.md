# D3：可见性门控训练输入与冻结划分重训

2026-10-30，Asia/Shanghai。协议在读取本轮源HDF和拟合前写出。

## 修复边界

现有live/代理因果belief已禁止遮挡位置输入，但D4/D5记录动作训练仍用HDF oracle当前状态。新增显式 `input_mode=causal_visible`：逐帧先用可见、在框且不遮挡的标注判断是否有观测，只在该时刻读取目标位置与actor id；目标速度由同身份的历史可见位置估计，不读取HDF oracle速度/距离。开场不可见采用与现有因果估计器一致的自身位姿搜索先验。训练抽样只决定loss行，不能丢弃抽样点之间已发生的观测。

当前位置特征和residual基线都使用同一因果状态；未来真值只构成监督标签。缺明确visibility/in-frame/occlusion数据或相机位置/yaw时拒绝该模式，不从默认数组推断真实可见。UAV未知电量/间距写0并注明未观测。该模式不是RGB检测器：位置/身份仍是可见标注oracle，图像中心输入为水平bearing代理；与live像素框测量存在域差异。

旧默认 `oracle` 保留历史重跑，模型保存的config/dataset summary明确记录input_mode，不把旧模型改名为causal。新产物使用独立目录，不覆盖D4/D5权重；并行录制仍使用其已冻结的D5模型。

## 冻结实验

- 来源：原 `artifacts/acceptance_20261028/development_split_v2.json`，SHA256 `7326f07e67e90ed0e6397bb553abf31e23cd8d594cf647532a5b02447b9f8e87`。
- 仅28 train与6 validation，源数据约10.45GiB；5 development_stress和Town03/final test不打开、不调参、不回流。
- 串行完整SHA、大小、PASS、mtime/帧数核对后只读state；哈希限速16MiB/s，不加载RGB。
- 每集96样本、stride2、horizon_frames5、rollout_horizon1秒、时间容差0.05秒、ridge alpha1、desired range20m。没有搜索超参/seed/阈值。
- 同一新causal输入和未来监督比较transparent、absolute、baseline_residual；训练标准化只从train计算。旧oracle结果仅作历史参考，不把跨输入契约的数值差异说成模型收益。
- 保留逐集误差、输入分层、训练/候选支持域、保存重载与源码/模型指纹。缺真实遮挡/出框验证样本则如实记录，不以合成回归补成真实场景成绩。
- 此为输入契约修订后的离线校准，不是闭环、反事实动作学习、实测安全或效果提升。

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python scripts/run_d4_rollout.py --split "D:/WPS Cloud/WPSDrive/1574147233/WPS云盘/wsy-jev-uav-track/artifacts/acceptance_20261028/development_split_v2.json" --output artifacts/d3_causal_training_20261030 --samples-per-episode 96 --hash-mib-per-s 16 --input-mode causal_visible
```

输出必须是新目录；该命令不启动仿真、不联网。运行前与唯一仿真运行方协调Python预检查窗口，不更改其模型、进程、端口或设置。

## 代码回归

`tests/test_causal_training.py` 对absolute/residual检查隐藏位置/身份/oracle速度扰动不改变特征；未来扰动仅影响标签；抽样与完整历史一致；缺输入契约拒绝；模型保存重载保留模式且拒绝错误标注拟合。临时合成fixture用于回归，不作为PASS源episode。

## 实验结果

运行退出0，report.status=COMPLETED。34/34源文件通过完整SHA/大小/PASS/稳定性检查；2688 train、576 validation样本，统一实际时域0.9999999776482582秒。输出在独立clone的 `artifacts/d3_causal_training_20261030/`。运行后逐项核验源代码与模型SHA无不匹配，Python3.10额外完成residual加载、推理时域绑定及有限数值推理；不将数值smoke当线上部署验收。

公开结果及逐文件/源码/模型指纹见[证据JSON](evidence/d3_causal_training_20261030.json)。它是本机报告的公开子集，未上传源HDF或NPZ；完整支持域数组仍在本机report中。

| 同一576验证样本 | visibility代理RMSE | 入框代理RMSE | range error RMSE（m） | covariance代理RMSE |
| --- | ---: | ---: | ---: | ---: |
| transparent | 0.134729 | 0.282904 | 0.243116 | 0.261600 |
| absolute | 约0 | 0 | 1.484269 | 约0 |
| baseline_residual | 0.032562 | 0.034842 | 0.228249 | 0.065536 |

汇总距离RMSE约降低6.1%，但这是先汇总样本平方误差再开方，不等于每集平均RMSE。逐集结果为3集改善、3集退化；episode等权平均距离RMSE反而从0.201443m升至0.203266m。不能挑汇总方式宣布稳定收益。

| validation episode | transparent距离RMSE | residual距离RMSE |
| --- | ---: | ---: |
| Town02 bicycle frequent_turns long seed26094451 | 0.103402 | 0.204263 |
| Town05 bicycle normal_motion close seed26094529 | 0.188984 | 0.267185 |
| Town05 pedestrian frequent_turns long seed26094457 | 0.084126 | 0.076972 |
| Town06 bicycle normal_motion close seed26094538 | 0.264341 | 0.182857 |
| Town07 pedestrian normal_motion long seed26094827 | 0.471078 | 0.387139 |
| Town10HD pedestrian frequent_turns long seed26094464 | 0.096728 | 0.101181 |

全部2688/576个抽样输入可见，遮挡/出框验证仍 **NOT_EVALUATED**。absolute的三个近零误差对应此数据中的常量/规则标签，不代表近乎完美视觉预测。候选支持域报告仅描述特征覆盖，不能给未执行动作补反事实标签。未调参、未选择最优seed、未读取stress/test、未进行真实Jev或仿真动作，本轮不替换视频运行模型。

新residual SHA256 `a3542d8e3f2730f8c0a597fa83b3c9fc225c50e147aa047c7b56f5f0fd01b560`；absolute SHA256 `d6a66344894ed24b7e4c389a900139b7cff555ecfc257c898d4ec779a1886cac`。

## 验收与审查

全套 **379 passed / 1 skipped**，compileall、diff检查通过。独立静态审查无阻断；按建议补充三个独立可见性门控扰动和residual基线不变检查，最后专项22项通过。真实重训之后仅检查保存模型/源码指纹与原Python3.10可加载性，没有重复寻找更好的训练配置。

D3新增“训练特征可见性门控与当前/未来监督分离”的代码和真实PASS数据重训证据。原默认oracle分支与旧D4/D5模型继续明确标注，不能把所有历史产物升级为causal。真实困难覆盖、RGB/ReID、线上动作效果仍未由本轮证明。D1独立Town03 PASS仍为0，本修复不替代D1。
