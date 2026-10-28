# Jev-Track 数学表述：早期设计与当前实现对照

整理日期：2026-10-28。来源是本项目聊天在 **2026-10-21 17:24（北京时间）** 给出的首版算法公式，以及当前已提交代码 `e2d77998efa1618316839f02762f0f3fb3075ecf`。本文整理方法，不修改算法或实验结果；之后的独立仿真调试不作为本文的完成证据。

这里的 Jev 是有限候选行为的外部决策模型，不是 JEPA/V-JEPA。我们的主线始终是：**本地代码预测各行为的后果，硬约束筛选候选，Jev 评价并选择，再由执行器执行和重新观测。** 本文中的公式不是 Jev 内部网络结构或训练目标。

阅读约定：**已实现**指所列版本存在对应代码；**设计目标**指研究表述或待实现机制；两者均不等于闭环性能提升已经得到证明。详细流程见 [algorithm.md](algorithm.md)，实验口径见 [metrics_protocol_v1.md](metrics_protocol_v1.md)。

## 1. 最早提出的五组公式

### 1.1 目标 belief

早期定义为：

$$
b_t=\{\hat p_t,\hat v_t,\Sigma_t,c_t^{\mathrm{id}},q_t^{\mathrm{vis}},q_t^{\mathrm{occ}}\}.
$$

它概括目标位置、速度、不确定度、身份置信度、可见性和遮挡风险。当前 `TargetBelief` 与这条主线吻合，但保存的是标量 `covariance_trace`，并没有维护完整协方差矩阵或完整后验分布。位置字段为所声明世界坐标中的位置；相对位置由目标位置减无人机位置得到，不能把两者混写。

### 1.2 候选后果

早期定义为：

$$
\hat z_{t,i}=\{P_{\mathrm{visible}},e_{\mathrm{center}},\operatorname{tr}(\Sigma),P_{\mathrm{collision}},E,d_{\mathrm{obstacle}},IG\}.
$$

当前 `CandidateOutcome` 保留这些量，并增加期望距离误差与可执行标记。不同指标中有相当部分仍是启发式代理，不能仅因为字段名含 probability 或 information gain，就将它们称为校准概率或真实互信息。

### 1.3 Jev 选择

早期简写为：

$$
a_t=\arg\max_{a_i\in\mathcal A_{\mathrm{safe}}}p_{\mathrm{Jev}}(a_i\mid b_t,\hat z_{t,i}).
$$

更完整的设计表达应包含 UAV 自身状态、任务、短时历史以及**全部**安全候选及其后果：

$$
p_{t,i}=p_{\mathrm{Jev}}\!\left(a_i\mid s_t,h_t,g,\{(a_j,\hat z_{t,j})\}_{j\in\mathcal I_t^{\mathrm{safe}}}\right).
$$

这描述期望决策语义。当前真实客户端优先接受服务返回的 `choice`；只有没有 choice 时才从概率取最大值。因此“最终动作严格等于概率 argmax”不是所有响应情况下都已强制成立的代码不变量。

### 1.4 置信度与概率间隔

早期写为：

$$
p_{\max}>\tau,\qquad p_{\max}-p_2>\delta.
$$

当前采用包含等号的阈值，并增加单独的 confidence 检查，见第 6 节。

### 1.5 综合评价目标

早期提出的研究目标为：

$$
J_{t,i}=w_vP_{\mathrm{visible}}-w_ce_{\mathrm{center}}-w_\Sigma\operatorname{tr}(\Sigma)+w_{ig}IG-w_eE-w_rP_{\mathrm{collision}}-w_s\mathbf1[a_i\ne a_{t-1}].
$$

这仍可作为方法设计框架，但不能直接写成当前程序的完整目标函数：当前 utility 增加了距离误差和安全余量奖励，**没有显式切换惩罚项**；动作保持由选择器的回退/滞回逻辑处理。当前实际公式见第 5 节。

## 2. 统一符号与因果状态

定义高层决策输入：

$$
s_t=(b_t,x_t^U,\mathcal C),\qquad h_t=(s_{t-L+1},\ldots,s_t).
$$

其中 $x_t^U$ 为 UAV 位置、速度、偏航、电量和健康状态，$\mathcal C$ 为任务约束；$L$ 是最多保留的历史状态数。实际窗口时长由时间戳差决定，不能把“8 个状态”固定解释为某个秒数。

对应代码：[models.py](../src/jev_uav_track/models.py)、[history.py](../src/jev_uav_track/history.py)、[causal_belief.py](../src/jev_uav_track/causal_belief.py)。

### 2.1 可见更新与丢失外推（已实现的简化估计）

设最后两次同身份、可见观测为 $(t_{k-1},p_{k-1})$ 与 $(t_k,p_k)$：

$$
\hat v_{t_k}=\frac{p_k-p_{k-1}}{t_k-t_{k-1}}.
$$

首次观测或可见身份改变时速度置零；丢失时只使用最后已知状态：

$$
\hat p_t=p_k+\hat v_{t_k}(t-t_k),\qquad q_t^\Sigma=0.05+(t-t_k)^2.
$$

$q_t^\Sigma$ 对应当前 `covariance_trace` 的启发式增长，不是经滤波推导的物理协方差传播。首次发现目标前，代码以自身位置、yaw 和期望距离建立搜索先验，初始不确定度代理为 10。不可见时不向该估计器传入当前隐藏真值位置和身份。

**输入边界：**当前因果代理仍使用“可见时的 oracle 位置/身份”，尚不是视觉检测器；`HDF5Episode.tracking_state()` 及现有训练入口仍属 oracle-state 校准，不能因为闭环代理采用因果估计，就宣称所有训练数据已经消除真值依赖。

### 2.2 历史摘要与身份需求（已实现）

对窗口内 $n$ 个状态：

$$
\bar q_t^{\mathrm{vis}}=\frac1n\sum_kq_k^{\mathrm{vis}},\quad
\bar c_t^{\mathrm{id}}=\frac1n\sum_kc_k^{\mathrm{id}},\quad
f_t^{\mathrm{occ}}=\frac1n\sum_k\mathbf1[q_k^{\mathrm{occ}}\ge0.5].
$$

$$
n_t^{\mathrm{id}}=1-\min(c_t^{\mathrm{id}},\bar c_t^{\mathrm{id}}),\qquad
\alpha_t=\frac{\|\hat v_t-\hat v_{t-1}\|_2}{t-t_{-1}}.
$$

这里 $t_{-1}$ 表示上一历史状态的时间戳；不足两条或时间间隔过小时，代码的加速度摘要置零。rollout 使用身份需求和加速度增加不确定度；扫描、绕飞、上升候选还可获得身份信息增益代理。这不是经过训练的 ReID 网络或真实身份互信息。

## 3. 行为库、时间与基础 rollout

候选动作写为：

$$
a_i=(\mathrm{id}_i,u_i,\omega_i,H),\qquad \hat z_{t,i}=F_0(s_t,h_t,a_i;H).
$$

当前行为包括 `hold_center`、`follow_predictive`、`orbit_left/right`、`climb/descend`、`scan_reacquire`、`safe_hover`。代码见 [primitives.py](../src/jev_uav_track/primitives.py)、[rollout.py](../src/jev_uav_track/rollout.py)。

**动作语义限制：**当前 $u_i$ 是固定世界 x/y/z-up 速度，不是随 UAV 朝向旋转的机体速度，也不是随目标转向的真正目标相对绕飞技能。名称表达设计意图，不能充当实现证据。

### 3.1 从时间戳确定预测时域

$$
H_k=t_{k+m}-t_k,\qquad m=\texttt{horizon\_frames}.
$$

当前已审核数据的图像间隔约 0.2 秒，默认 $m=5$ 对应约 **1.0 秒**。早期 JSON 示例里的 `predicted_after_1_5s` 只是当时的设计例子，已经不适合作为当前默认值。训练校验所有窗口的跨度；推理 baseline 必须使用模型记录的时域，不能单独换回 1.5 秒。

### 3.2 透明常速度预测（已实现）

$$
\hat p^U_{t+H,i}=p_t^U+Hu_i,\qquad
\hat p^T_{t+H}=\hat p_t^T+H\hat v_t^T,
$$

$$
\hat r_{t,i}=\|\hat p^T_{t+H}-\hat p^U_{t+H,i}\|_2,\qquad
e^r_{t,i}=|\hat r_{t,i}-r^\star|.
$$

这是常速度运动学近似，不包含完整飞行动力学、惯性或实际制动过程。可见性、画面中心误差、碰撞风险、能耗和信息增益在透明 rollout 中为规则代理，不是实际传感器或物理仿真读数。

## 4. 学习型 rollout：当前实际训练的是什么

当前训练的是一个 **15 维状态/动作特征到 4 个 outcome 的标准化 ridge 校准器**，没有训练 Jev 本身，也没有训练大型视觉世界模型。对应 [training.py](../src/jev_uav_track/training.py)、[learned_rollout.py](../src/jev_uav_track/learned_rollout.py)。

输出向量为：

$$
y=(q^{\mathrm{vis}},e^{\mathrm{center}},e^r,q^\Sigma)^\top.
$$

训练输入包含目标距离、目标和 UAV 三轴速度、可见性、遮挡、中心误差、自身速度模长、命令三轴速度及偏航率。当前记录动作使用相机观测速度近似命令，偏航命令特征在该训练入口默认为零；因此非零 yaw 候选的支持域尤其需要审查。

### 4.1 absolute 与 residual 标签

$$
\ell_n^{\mathrm{abs}}=y_n,\qquad
\ell_n^{\mathrm{res}}=y_n-y_n^0,
$$

其中 $y_n^0$ 是对**同一记录动作、同一预测时域**计算的透明 baseline。未来标签只用于离线监督，不属于决策时可见输入。

设训练集特征均值/尺度为 $\mu_j,\sigma_j$，构造：

$$
\tilde x_{n,j}=\frac{x_{n,j}-\mu_j}{\sigma_j},\qquad
\phi_n=[1,\tilde x_n^\top]^\top.
$$

代码将小于 $10^{-8}$ 的训练标准差替换为 1；输入消融先屏蔽特征，再以训练集拟合标准化参数。令设计矩阵为 $\Phi$，标签矩阵为 $Y$：

$$
W^*=\arg\min_W\|\Phi W-Y\|_F^2+\lambda\|RW\|_F^2,
\qquad R=\operatorname{diag}(0,1,\ldots,1),
$$

$$
(\Phi^\top\Phi+\lambda R)W^*=\Phi^\top Y.
$$

截距不受正则化；代码优先解线性方程，奇异时用最小二乘求解，不要求显式求逆。数据集、模型与推理必须一致记录 `target_mode`，否则 residual 会被误当绝对值。

### 4.2 推理混合与支持域回退

对候选 $a_i$，混合前后分别为：

$$
\hat y_i^{\mathrm{learned}}=
\begin{cases}
f_W(x_i),&\mathrm{absolute},\\
y_i^0+f_W(x_i),&\mathrm{baseline\_residual},
\end{cases}
$$

$$
\hat y_i=\Pi\big((1-\beta)y_i^0+\beta\hat y_i^{\mathrm{learned}}\big),\qquad0\le\beta\le1.
$$

$\Pi$ 将可见性和中心误差限制在 $[0,1]$，距离误差限制为非负，协方差代理下限为 0.05。OOD 判据为所启用特征的最大标准化偏离：

$$
z_i^{\max}=\max_{j\in\mathcal J}\left|\frac{x_{i,j}-\mu_j}{\max(|\sigma_j|,10^{-8})}\right|.
$$

若启用的状态级或全特征阈值被超过，该候选直接返回透明 baseline。状态级版本排除 `command_*` 特征。当前 OOD 阈值可不启用，不能声称默认始终有支持域保护。

碰撞概率、障碍余量、能耗、信息增益和 `feasible` **仍沿用透明 baseline**，并不随学习后的四个 outcome 重新计算。这一实现限制必须在论文中说明。记录动作上的误差降低也不等于未执行候选的反事实后果正确，更不等于闭环跟踪改善。

## 5. 安全集合与 utility

### 5.1 安全过滤的实际范围

当前筛选可概括为：

$$
\mathcal A_t^{\mathrm{safe}}=\{a_i:\mathrm{feasible}_i,\ \hat P_i^{\mathrm{col}}\le P_{\max},\ \hat d_i\ge d_{\min},\ H_i\le H_{\max},\ B_i^{\mathrm{code}}\}.
$$

透明 rollout 在 `feasible` 中检查速度和时长；`safety.py` 没有再次独立计算任意外部候选的速度。因此这个集合依赖候选生成器的契约，不是完整的外部轨迹验证器。

**电量量纲缺口：**当前 $B_i^{\mathrm{code}}$ 对应 `battery_percent - energy_cost_wh >= reserve_battery_percent`。百分比与 Wh 不能直接相减，这只能记为原型缺口，不能作为严格电池安全证明。物理一致的设计应在容量 $C_{\mathrm{Wh}}$ 已知且能耗模型有效时使用：

$$
B_t-100\frac{\hat E_i}{C_{\mathrm{Wh}}}\ge B_{\mathrm{reserve}}.
$$

后一式是**待实现建议**，本次仅整理文档，没有修改代码。禁飞区、真实障碍地图和完整动力学可达性同样不能称为已由当前过滤器全面保障。

### 5.2 当前 utility 的精确对应

$$
U_i=3.0q_i^{\mathrm{vis}}-1.8e_i^{\mathrm{center}}-0.08e_i^r-0.12q_i^\Sigma+0.8IG_i-0.5E_i-8.0P_i^{\mathrm{col}}+b_i,
$$

$$
b_i=\begin{cases}0.5,&d_i\ge d_{\min},\\-2.0,&d_i<d_{\min}.\end{cases}
$$

对应 [CandidateOutcome.utility](../src/jev_uav_track/models.py)。这些固定权重是可复现的原型设定，不是从 Jev 学出来的权重，也不是经过验证的通用最优奖励。

## 6. 真实 Jev、Mock 与最终执行选择

### 6.1 三种选择器不能混称

utility 基线：

$$
a_t^U=\arg\max_{a_i\in\mathcal A_t^{\mathrm{safe}}}U_i.
$$

Mock Jev 的已实现概率为：

$$
\tilde U_i=U_i+w_{\mathrm{id}}(1-c_t^{\mathrm{id}})IG_i,\qquad
p_i^{\mathrm{mock}}=\frac{\exp(\tilde U_i/T)}{\sum_j\exp(\tilde U_j/T)}.
$$

默认 $T=0.35$、$w_{\mathrm{id}}=0$；代码将 $T$ 下限设为 0.05。默认 Mock 的原始排序因此与 utility 相同，差别可能来自概率门控及回退保持；不能把两者接近写成“真实 Jev 无收益”。其 confidence 为 $\min(1,s\max_i p_i^{\mathrm{mock}})$，默认 $s=1$。

真实 Jev 的概率来自 API，不能写成上述 softmax 的内部实现。客户端会归一化有效候选的概率；无概率或总和为零时可用 choice 构造 one-hot。这种兼容性 one-hot 不代表模型提供了经校准的 100% 置信度。

### 6.2 已实现的门控与回退

令排序后的前两项概率为 $p_{(1)},p_{(2)}$，单候选时令 $p_{(2)}=0$：

$$
G_t=\mathbf1[p_{(1)}\ge0.45]\,\mathbf1[p_{(1)}-p_{(2)}\ge0.08]\,
\mathbf1[c_t^{\mathrm{Jev}}\text{ 缺省或 }c_t^{\mathrm{Jev}}\ge0.55].
$$

还必须验证 choice 属于当前安全集合。异常、未知 choice 或门控不通过时走 utility 回退；如果上一动作仍安全且

$$
\max_iU_i-U_{\mathrm{previous}}\le0.25,
$$

则回退路径保持上一动作。原始模型决策和最终执行动作分开记录，不能以 fallback 的表现替代模型本身的表现。

另有 `hysteresis_hold` 分支要求通过门控后满足概率间隔 $<0.05$。由于默认门控要求间隔 $\ge0.08$，**该专门分支在默认参数下不可达**；目前能够起作用的是上述 fallback 保持逻辑。不能把它描述成所有情况下都已生效的显式切换惩罚。

若 $\mathcal A_t^{\mathrm{safe}}=\varnothing$，当前选择器抛出异常，不会无条件选择 `safe_hover`。将“无安全动作”交给经过验证的停止/人工接管机制是执行层要求，而不是当前选择器已保证的能力。候选过期和远程响应延迟还需要执行层管理。

代码：[jev_client.py](../src/jev_uav_track/jev_client.py)、[selector.py](../src/jev_uav_track/selector.py)、[safety.py](../src/jev_uav_track/safety.py)。

## 7. 坐标和执行时间不能省略

当前本地 CarlaAir 配准契约是：

$$
p^{\mathrm{AirSim}}=\operatorname{diag}(1,1,-1)p^{\mathrm{CARLA}}+o,\qquad
v^{\mathrm{AirSim}}=\operatorname{diag}(1,1,-1)v^{\mathrm{CARLA}}.
$$

$o$ 必须来自对应实例的配准，不能随意指定或跨地图套用。它不同于地理 ENU 到 NED 的转换 $(x,y,z)\mapsto(y,x,-z)$。泛用机体 FLU 到 ENU 的旋转为：

$$
v^{\mathrm{ENU}}=
\begin{bmatrix}\cos\psi&-\sin\psi&0\\\sin\psi&\cos\psi&0\\0&0&1\end{bmatrix}v^{\mathrm{FLU}}.
$$

这个泛用转换不代表旧候选已自动变成机体相对动作。对应 [frames.py](../src/jev_uav_track/frames.py)、[sim_adapter.py](../src/jev_uav_track/sim_adapter.py)。

早期“高层 1–2 Hz、低层 20–100 Hz”是架构建议；冲刺暂以高层 1 Hz、低层 20 Hz 为目标，需实测冻结。HDF 图像采样率、状态记录率、命令刷新率和物理更新率是不同量。代理中命令到期后理想化为零速度保持，不等于真实 UAV 自动停止。

## 8. 用什么公式评价效果

对每个 episode 的预定有效时间网格计算正确目标可见比例，再对 episode 等权：

$$
V_e=\frac1{N_e}\sum_{k=1}^{N_e}\mathbf1[\text{正确目标可见}]_{e,k},\qquad
\bar V=\frac1M\sum_{e=1}^{M}V_e.
$$

这是冻结协议中的目标口径；live 提前终止后的预定网格按失败计入，不可仅平均运行存活部分。

固定窗重捕获中，事件起点 $t_0$ 独立于方法确定，窗口 $W=10$ 秒，稳定确认时长 $S=0.5$ 秒。$t_{\mathrm{confirm}}$ 是采样序列连续正确跟踪至少 $S$ 后的首次确认时刻，**不是稳定片段的起始时刻**：

$$
T_e^{\mathrm{restricted}}=
\begin{cases}
\min(W,t_{\mathrm{confirm}}-t_0),&\text{完整观察且窗口内确认恢复},\\
W,&\text{未恢复或方法提前结束}.
\end{cases}
$$

$$
\bar T=\frac1{N_{\mathrm{eligible}}}\sum_eT_e^{\mathrm{restricted}},\qquad
R=\frac{N_{\mathrm{recovered}}}{N_{\mathrm{eligible}}}.
$$

没有完整方法无关参考窗的事件预先记为不具资格，并报告数量；不将其随意算成方法失败。连续性还要求采样间隔不超上限，当前离线默认 0.25 秒。无合格事件时结果为缺失，不填零。

实现见 [event_metrics.py](../src/jev_uav_track/event_metrics.py)。真实身份交叉缺失记 `NOT_AUDITED`；代理风险不替代碰撞传感器。真实 Jev 的独立贡献应比较**同 rollout、同候选、同控制器和预算**的 utility 与真实 Jev，另报原始选择、fallback、延迟和实际 API 覆盖。

## 9. 与早期方案的最终对应

| 早期内容 | 当前保留方式 | 必须说明的变化或缺口 |
| --- | --- | --- |
| belief 状态公式 | 保留，并加入时间历史、可见更新和失锁外推 | 当前只有协方差 trace 代理；不是完整贝叶斯滤波器 |
| 外部模拟候选后果 | 保留透明 rollout，并增加 absolute/residual 校准 | 记录动作监督不等于反事实动作动力学 |
| Jev 在安全候选中选择 | 保留结构化 choice 接口及异常回退 | API choice 不一定被强制改为概率最大项 |
| 概率和置信度门控 | 已实现阈值检查 | 缺省 confidence、兼容 one-hot 必须区分 |
| 综合评价与切换惩罚 | utility 已实现，动作保持另在 selector | 显式切换成本未实现；默认专用滞回分支不可达 |
| 1.5 秒候选例子 | 改为时间戳绑定的时域，当前默认约 1 秒 | 不可混用不同时间跨度训练和推理 |
| 安全悬停/返航兜底 | 保留为执行层设计要求 | 无安全候选会抛错；电池量纲、真实制动与返航未完整落实 |
| 闭环提升与论文贡献 | 保留为研究假设 | 不能由 API 接通、离线 RMSE 或 Mock 结果直接推导 |

本文可以作为论文方法部分的数学说明草稿。正式论文必须按最终冻结代码修订，并将“已实现”“代理近似”“未实现”和“实验证明”分开表述；不能直接复制早期理想化设计作为最终系统能力。
