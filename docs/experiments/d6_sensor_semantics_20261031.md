# D6 安全输入：碰撞消费语义与距离测量边界

2026-10-31（Asia/Shanghai），代码基线 `da19812b6bb10816d529d51f1f67c4ed2cbd561d`。
本项为**上游源码核验与本机SDK离线复现**，不是新探针实现、现场传感器认证或D6通过。未启动真实模拟器、连接模拟器RPC、解锁、移动或改配置；没有研究训练、用户源HDF读取或Jev调用（合成单测夹具不计入这些现场/实验计数）。自动目标续作不代替用户对上一条新增安全探针设计的确认。

## 核验结果

### 碰撞读取不一定无副作用

核验的上游AirSim提交为 `1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41`。
其 [`simGetCollisionInfo` RPC绑定](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/AirLib/src/api/RpcLibServerBase.cpp)调用 `getCollisionInfoAndReset()`；[Pawn实现](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/Unreal/Plugins/AirSim/Source/PawnSimApi.cpp)返回碰撞记录后清除 `has_collided`，碰撞事件时间戳保留。因此多个读取者可能消费彼此需要的事件标志，不能把该API称为严格无副作用的查询。

`time_stamp`由碰撞事件回调设置，不是每次RPC采样时间。长时间没有新碰撞时，事件时间较旧不等于本次读取已过期；反过来，刚收到RPC回复也不能证明传感器状态与图像同一时刻。后续接入须分别保留事件时间、RPC本机时间括号、时钟域及缺测状态，不能用回复时间改写事件时间，或拿 `False` 属性单独认证“没有碰撞”。

以上证明的是该上游版本，**未证明本机CarlaAir二进制使用相同实现**。安装的Python封装只证明调用名称，不能代替服务器实现/现场行为核验。上游读取会清除标志这一风险，也说明即使不飞行，探针仍应独占本任务实例而非借用其他任务。

### 距离值不是全方向最小间距

[默认传感器说明](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/docs/sensors.md)列出的多旋翼默认传感器不含Distance/Lidar。本任务settings也未显式配置它们；本机服务器扩展是否提供有效数据仍为 `NOT_VERIFIED`，不能从配置空缺直接断言服务器一定没有。

[DistanceSimple](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/AirLib/include/sensors/distance/DistanceSimple.hpp)在产生测量时设置传感器时间戳，存在更新频率/延迟队列及噪声；RPC刚返回不等于数据刚产生。[Unreal距离实现](https://github.com/microsoft/AirSim/blob/1ca93f6f77e4e8a39b2b241c1fe2764da4d7dd41/Unreal/Plugins/AirSim/Source/UnrealSensors/UnrealDistanceSensor.cpp)使用定向射线，未命中时给出最大量程，随后距离模型可能叠加噪声。

因此一个方向的距离、最大量程或空Lidar点云，不能自动变成D6所需的有效 `clearance_m`。探针即使读到数值也只认证“API响应及测量语义”；动作扫掠空间、机体范围、盲区和时空对齐未证明之前，控制安全门仍应拒绝。这里没有新增验收标准，而是在解释现有“真实间距”约束，避免用不等价的标量替代它。

### 本机SDK会让缺失字段表现为默认属性

在本机Python3.10环境，实际执行以下无RPC复现：

```python
import airsim

collision = airsim.CollisionInfo.from_msgpack({})
distance = airsim.DistanceSensorData.from_msgpack({})
assert vars(collision) == {} and vars(distance) == {}
assert collision.has_collided is False
assert collision.time_stamp == 0
assert distance.distance == 0 and distance.time_stamp == 0
```

结果符合断言。空payload没有这些字段，但类属性仍给出 `False/0`；这是SDK解码行为，不是真实服务器发送空payload的证据。未来适配器须核验实际收到的字段及类型/有效性，不能只检查属性存在。本轮没有修改SDK或生产解析器，也不声称已修复尚未接入的live路径。

## 证据与下一步

机器证据含5份固定上游源码的完整SHA/字节数、本机SDK与独立settings指纹、离线复现输出，见[JSON](evidence/d6_sensor_semantics_20261031.json)。核验时间为 `2026-10-31T06:19:58.8513411Z`。未保存API key或第三方运行状态。

提交前再次执行上述SDK断言，核对JSON与3个本机文件SHA；全套回归 `562 passed, 1 skipped in 14.67s`，`python -m compileall -q scripts src`通过。生产代码未改；这些回归不新增任何live传感器验收证据。

现有批准的D6执行计划已逐项落盘，但不代表原D1–D6完成。Town03仅有API基础设施证据，PASS episode仍为0；D5负结果与冻结模型不变；D6仍缺真实安全测量和受限执行/停止回执。没有为了获得新的通过数重复训练或扩大到D7矩阵。

上一条向用户提出的下一步仍是：只在本聊天独立Town02实例增加无解锁/无运动/不改传感器配置的安全探针，记录响应、测量时间与失败，补回归测试；此设计**待用户确认，尚未实施或运行**。它不会直接认证几何覆盖或授权飞行。缺失传感器时如实失败，不通过临时补默认值放行。
