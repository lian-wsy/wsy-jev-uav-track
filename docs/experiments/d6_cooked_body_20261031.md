# D6：固定发行包机体碰撞资产只读审计

## 结论与验收边界

从本任务独立发行副本的四个完整 SHA/大小固定资产中，复现了 `BP_FlyingPawn → BodyMesh → Quadrotor1 → BodySetup` 引用、一个原始凸包、两个 PhysX cooked 凸包和 cooked 三角网格。两套 Python 输出字节相同。这解决“机体碰撞形状没有可复核来源”的离线证据缺口，**不认证运行中使用的碰撞外形**。

固定输出 `control_ready=false`、`runtime_collision_envelope_m=null`。D6仍为PARTIAL，Town03独立PASS仍0。没有启动模拟器、API/飞行/Jev调用、HDF读取、训练或新增episode。旧静态位姿、道路接触及失败记录保留，不由资产分析追认起飞/制动通过。

## 复现方式

脚本为 [`scripts/audit_cooked_uav_body.py`](../../scripts/audit_cooked_uav_body.py)，不是通用Unreal/PhysX解析器。先核对全部四文件的完整SHA和大小，再使用经审核的此发行版本字节布局；未知/更改资产拒绝。只读输入，输出不允许放回资产目录，也拒绝覆盖已有文件。

已执行的命令（重新运行应使用新的输出名，保留原证据）：

```powershell
& 'C:/Users/20748/miniconda3/python.exe' scripts/audit_cooked_uav_body.py --asset-root 'D:/JevUavRuns/20261031_d6_probe01/package/CarlaUE4/Plugins/AirSim/Content' --output artifacts/d6_cooked_body_20261031/python314.json
& 'C:/Users/20748/miniconda3/envs/carlaAir/python.exe' scripts/audit_cooked_uav_body.py --asset-root 'D:/JevUavRuns/20261031_d6_probe01/package/CarlaUE4/Plugins/AirSim/Content' --output artifacts/d6_cooked_body_20261031/python310.json
```

Python3.14.7/3.10.21均退出0，两个输出均7650字节，SHA256均为`b163f4df295273d883dfe3e17dce5674f0f007592c9cd5bfe02b0176e022c87c`。原文件留在忽略的artifacts目录；完整可审阅输出、脚本SHA、四资产SHA/大小/整数纳秒mtime、六个参考源码SHA及八个保护文件SHA收录在[机器证据](evidence/d6_cooked_body_20261031.json)。不提交发行包二进制。

## 提取结果

以下均为资产局部单位，不是已标定的米制运行间距。

| 数据 | 点/面 | 局部边界 | 验证 |
|---|---|---|---|
| 原始凸包 | 32点 | min=(-50.267357,-50.284718,-19.168186)，max=(50.250004,50.284718,12.530990) | 序列化ElemBox逐值一致；绕资产原点最大半径61.487393 |
| 两个cooked凸包 | 各32点 | 第一个与原始凸包一致，第二个为X镜像 | 最近顶点最大差均7.688768e-6；各自存储bounds与解码顶点一致 |
| cooked三角网格 | 6484点/12765三角形 | min=(-49.084709,-49.084717,-17.968185)，max=(49.084709,49.084717,11.330989) | 全部16bit三角索引有效；绕原点最大半径59.597016 |

凸包元素变换为单位旋转、零平移、单位缩放；`BuildScale3D=(12,12,12)`单独保留为构建元数据，**没有再次乘到cooked顶点上**。不能仅凭通常的100单位/米约定或actor级scale，把这些数写成已认证的UAV碰撞半径。

## 来源与剩余关键路径

本地参考源码commit为`d0c3fd717d5983fa488bedcdc055ebb2278c8780`，六个选定文件无本地修改；源码与已发布Shipping二进制的构建同一性仍为NOT_PROVEN。引用链来自该源码和精确资产的独立交叉检查，不宣称重建了整个引擎。

字节布局对照[UAssetAPI固定版本](https://github.com/atenfyr/UAssetAPI/blob/b4e8a012939295a6fd890ddc64e57ea78b709b95/UAssetAPI/PropertyTypes/Objects/ArrayPropertyData.cs)、[PhysX凸包读取源码](https://github.com/NVIDIAGameWorks/PhysX-3.4/blob/5e42a5f112351a223c19c17bb331e6c55037b8eb/PhysX_3.4/Source/GeomUtils/src/convex/GuConvexMesh.cpp)和[PhysX三角网格读取源码](https://github.com/NVIDIAGameWorks/PhysX-3.4/blob/5e42a5f112351a223c19c17bb331e6c55037b8eb/PhysX_3.4/Source/GeomUtils/src/GuMeshFactory.cpp)。Epic当前BodySetup文档只用于一般语义，不证明此UE4版本的实际行为。

运行中实际mesh/形状选择、组件变换、其他可碰撞组件及contact offset仍未核验。`simGetObjectScale`只有actor级信息；参考实现中的`simGetMeshPositionVertexBuffers`遍历全世界静态网格/GPU缓冲，无对象过滤且不是碰撞形状查询，故没有调用它来冒充有界机体查询。

下一步应核实运行时形状/变换及地面支撑和启动通道，再执行原受限动作→独立停止遥测。不得直接放宽碰撞/间距门控，不重复已完成的静态位姿或固定扫掠夹具来代替这一缺口。

## 验证

20个新增测试先RED后GREEN；固定发行包只读集成测试实际执行而非跳过。全套904 passed/1 skipped（24.11s），双Python compileall和`git diff --check`通过。独立只读审查批准、19个用例通过，另单独核验拒绝覆盖行为，独立复现相同输出SHA。完整记录在机器证据，测试通过不改变上述运行验收边界。
