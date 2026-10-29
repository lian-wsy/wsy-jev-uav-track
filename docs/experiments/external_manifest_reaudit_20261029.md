# 外部数据 manifest 只读复查（2026-10-29）

本次只读取外部任务已有的 JSON/JSONL 状态和 trim manifest 文件名，没有打开 HDF5，没有修改文件、锁、进程或上传链，也没有把结果纳入训练。

| 位置 | 元数据文件 | town 分布 | 状态摘要 | 是否解除 final test |
| --- | ---: | --- | --- | --- |
| `D:\CarlaAir\data\raw_airsim_uav\v3` | 319 | Town02 102、Town05 50、Town06 38、Town07 70、Town10HD 59 | `AUTOMATIC_PASS_PENDING_V3_ACCEPTANCE` 256；`VALIDATION_FAILED` 18；`CAPTURE_FAILED` 44；`STARTING` 1 | 否，raw/pending 不是完整 PASS |
| `E:\CarlaAir_1720\data\trimmed\v3` | 23 | Town02 23 | `STRICT_PASS_PENDING_VISUAL_REVIEW` 23 | 否，仍 pending 且没有新 town |
| `E:\CarlaAir_C_migrated_20261023\CarlaAir\data\raw_airsim_uav\v3` | 45 | Town02 45 | `AUTOMATIC_PASS_PENDING_V3_ACCEPTANCE` 42；`VALIDATION_FAILED` 2；`STARTING` 1 | 否，raw/pending 不是完整 PASS |
| `E:\CarlaAir_Town06_Town07` | 0 个匹配状态/trim manifest | — | 匹配查询未找到；另看到raw/trial manifest，未当PASS。历史存在不可读子目录，此枚举不能证明全盘没有合格数据 | 否 |

结论：本次限定元数据查询没有发现可接纳的、此前未暴露且完整PASS的独立town，不能据此声称已穷尽所有数据源。上述状态只反映对应元数据，不否定另存的完整审核报告。若有完整PASS、SHA、审核和未使用证明再核查；不因状态文件数量增长自动放宽门槛。
