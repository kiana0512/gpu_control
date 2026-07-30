# 2026-07-31 六 API、120 VU R8 最终有界压测验收

执行窗口：2026-07-31 00:19:15—00:51:34（Asia/Singapore）
会话：`sixapi-20260730-r8`
执行进程退出码：`0`
压测结论：`BOUNDED_STRESS_ACCEPTED`
发布总状态：`DEPLOYED_NOT_ACCEPTED`

## 1. 结论与边界

R8 在生产 1.5.6 控制面完成 `1 → 10 → 25 → 50 → 100 → 120 VU` 六 API 有界混合压力。
本轮共发出 `39,778` 个 HTTP 请求，失败 `0`；登记 `151` 个任务，其中 `66` 个在窗口内完成且
产物合同验证通过。六 API coverage、七项性能/稳定性阈值、生产任务让路、有界生命周期、作用域恢复、
三 GPU 饱和目标、遥测完整性和最终清场全部通过，执行进程正常以退出码 `0` 结束。

这里的“验收”仅指 R8 六 API、120 VU 有界压力方案。它不等于动画管家固定
B1/B6/B30/B64/B97/B300、同素材 1/2/3 节点速度 A/B、`3×B97`、完整故障注入矩阵或连续七天生产
观察已经完成，也不代表双方已签署 `FROZEN / PRODUCTION_ACCEPTED`。控制面总体状态必须继续保持
`DEPLOYED_NOT_ACCEPTED`。

本轮没有修改 ImageClip、ModelViewCreator、Retopology Skill 或任何外部 workflow、模型、prompt、
采样参数、图拓扑和输出语义。

## 2. 请求、任务与六 API 覆盖

| 指标 | R8 结果 | 判定 |
| --- | ---: | --- |
| HTTP 请求 / 失败 | `39,778 / 0` | PASS |
| HTTP P50 / P90 / P95 / P99 | `11 / 27 / 73 / 570 ms` | 记录 |
| 登记 / 验证成功任务 | `151 / 66` | bounded lifecycle PASS |
| 终态成功 | `SUCCEEDED=66` | 产物合同已验证 |
| HTTP retry / 业务 retry | `0 / 0` | PASS |
| 测试时长 | `1,938.699 s` | 含清场 |
| production watchdog | `triggered=false` | PASS |

六类 API 均有真实登记请求，`six_api_coverage.passed=true`：

| API | 登记数 | HTTP 接纳 |
| --- | ---: | --- |
| `imageclip_batch` | `86` | `202 × 86` |
| `modelview_roughness` | `15` | `200 × 15` |
| `uv_process` | `21` | `202 × 21` |
| `retopology_audit` | `11` | `202 × 11` |
| `retopology_process` | `10` | `202 × 10` |
| `substance_bake` | `8` | `202 × 8` |

同步 Roughness 以完整 end-to-end 时间单独验收，不再被错误归入 async submit；其余五类使用 async
submit、poll 与 artifact 指标。

## 3. 七项阈值全部通过

| 门禁 | 观测值 | 上限 | 判定 |
| --- | ---: | ---: | --- |
| async submit P95 | `1,500 ms` | `3,000 ms` | PASS |
| Roughness sync E2E P95 | `333,000 ms` | `600,000 ms` | PASS |
| poll P95 | `70 ms` | `1,500 ms` | PASS |
| artifact P95 | `17 ms` | `30,000 ms` | PASS |
| queue P95 | `306,338 ms` | `900,000 ms` | PASS |
| HTTP failure rate | `0%` | `1%` | PASS |
| retry rate | `0%` | `5%` | PASS |

任务总耗时 P50/P90/P95/P99 为 `22,144 / 277,190 / 349,177 / 974,502 ms`；队列 P50/P90/P95/P99
为 `528 / 181,038 / 306,338 / 426,777 ms`。固定压力窗口内未自然结束的任务不伪装成成功，也不要求
无限等待；它们进入下一节的精确作用域恢复和清场。

## 4. 有界生命周期与清场

`lifecycle_evaluation.passed=true`，策略为 `bounded_stress`：必须有经过产物合同验证的成功子集，所有
剩余任务必须限定在本轮 test tenant、运行开始时间和业务身份范围内恢复、取消或到达安全终态，并验证
服务端已经 settled。

R8 的机器结果为：

- registry 中登记 `151` 个任务，验证成功 `66` 个；固定窗口结束时登记任务中 `85` 个尚未完成；
- teardown 尝试 `120` 个、服务端接受 `120` 个、最终 settled `120` 个；
- 作用域恢复扫描 GPU 行 `240`、Asset 行 `297`，执行 `22` 次 GPU 状态查询；
- 找回并清理 harness 尚未登记的同作用域服务端任务 `35` 个；
- `unresolved_incomplete_task_ids=[]`、`teardown_failed_task_ids=[]`、
  `artifact_contract_failure_task_ids=[]`；
- 生产 watchdog 始终未触发；测试流量是非抢占式，未广泛取消其他租户任务。

因此 `120/120 accepted` 和 `120/120 settled` 是完整有界生命周期证据；不能把它写成“151 个任务全部
在加压窗口内自然完成”，也不能把被安全清场的任务计入 66 个成功任务。

## 5. GPU、Asset 槽位与队列峰值

R8 的 `telemetry.jsonl` 对三个 GPU 节点和七个有效 Asset Worker 全程取样，CPU 百分比因后端未提供而
明确记为 `NOT_EXPOSED`，不从槽位占用率伪造 CPU 利用率。

| 资源 | 峰值 / 分位 | 结果 |
| --- | --- | --- |
| 4090 harness GPU 利用率 | max `100%`、P50 `99%`、P95 `100%` | saturation PASS |
| 3090-A harness GPU 利用率 | max `100%`、P50 `99%`、P95 `100%` | saturation PASS |
| 3090-B harness GPU 利用率 | max `99%`、P50 `90%`、P95 `97%` | saturation PASS |
| GPU 槽位 | used peak `3/3`、available minimum `0` | 三卡并行 |
| GPU 队列 | peak `477` | 有界清场通过 |
| Asset 槽位 | used peak `7/13`、available minimum `6` | 多 Worker 并行 |
| Asset 队列 | peak `5` | 有界清场通过 |

三卡达到至少 90% 的饱和目标为 `passed=true`。Harness 记录的最低空闲显存分别为：4090
`1,626 MiB`、3090-A `2,143 MiB`、3090-B `2,874 MiB`。三个 Linux Asset Worker 及四个有效
Windows Substance Worker 均在本轮到达过 `100%` 单 Worker 槽位占用；失联的旧
`asset-worker-3090-b-windows` 不计入有效容量。

### 5.1 Prometheus 同窗口硬件峰值补充

同一运行窗口的只读 Prometheus 取样提供了 harness 结果目录之外的硬件峰值：

| 节点 | GPU | 温度 | 功耗 | 显存使用 |
| --- | ---: | ---: | ---: | ---: |
| 4090 主控 | `100%` | `77°C` | `437.609 W` | `21,435 MiB` |
| 3090-A | `100%` | `83°C` | `417.321 W` | `21,243 MiB` |
| 3090-B | DCGM 数据缺失 | DCGM 数据缺失 | DCGM 数据缺失 | DCGM 数据缺失 |

3090-B 不能因 DCGM 缺口伪填温度、功耗或显存峰值；其饱和证据只采用结果目录内 harness 指标：max
`99%`、P50 `90%`、P95 `97%`。上述 Prometheus 补充取样不在 R8 `manifest.json` 内，不能冒充已被
下述结果目录 checksum 覆盖；DCGM 3090-B 采集缺口需另行修复。

## 6. Substance 物理 GPU fence 与自动恢复

Windows Substance Baker 与 ComfyUI 共用 3090-B 物理 GPU。本轮遥测观察到三次完整的受控切换：

1. Windows Baker 领取 Substance 后，3090-B 停止接收新的 ComfyUI 工作并进入
   `DRAINING/OFFLINE` 保护状态；
2. 物理 GPU fence 存续期间，3090-B 的 GPU `current_jobs=0`，Windows Worker 执行 1～4 个
   Substance 槽位；
3. Windows 作业结束、持久 fence 释放后，节点无需人工删标签即恢复 `ONLINE/ACTIVE`，随后继续领取
   GPU 工作。

三次可见区间分别覆盖 telemetry sequence `99→116`、`183→191`、`254→261`。本轮 8 个
`substance_bake` 请求均被接纳，四个有效 Windows Worker 合计完成 8 个成功任务；没有观察到
ComfyUI 与 Substance 同时占用 3090-B，也没有遗留 fence 或人工解锁依赖。

## 7. 遥测完整性

| 项目 | 结果 |
| --- | ---: |
| 有效 / 无效样本 | `379 / 0` |
| 采样间隔 | `5 s` |
| 观测窗口 | `1,888.231 s` |
| 最大相邻间隔 | `5,004 ms` |
| 严格允许上限 | `7,500 ms` |
| sequence 连续 | `true` |
| 显式 final sample | `true` |
| GPU / Worker 样本齐全 | `true / true` |
| `sampling_evidence.passed` | `true` |

R8 关闭了 R7 的尾部采样竞态：停止监听器等待在途采样完成后再写显式 final sample；379 个样本序号
连续，最大间隔 `5,004 ms`，不再出现 R7 的 `7,699 ms` fail-closed 结果。R7 历史失败仍保留在
73 号记录，不因 R8 通过而改写。

## 8. Scheduler loop lag 指标算法偏差

R8 运行时 Prometheus 曾显示 `gpu_control_scheduler_loop_lag_seconds≈326 s`，但同时 20 秒内
Scheduler 循环计数继续增加 `39` 次，任务持续领取并成功，HTTP、队列和生命周期证据也正常推进。
代码复核确认当前生产 1.5.6 的该 Gauge 把每轮业务处理时间累加到一个历史 deadline，测到的是累计
drift，而不是“当前 event loop 已阻塞 326 秒”。因此该数值不可用于量化 R8 的实时 event-loop 卡顿，
但不影响本报告采用的真实 HTTP、数据库生命周期、队列与任务完成证据。

GPU Control 提交 `521ab58` 已准备候选修复：独立 `500 ms` event-loop wake probe，在每次等待开始
时重新建立 deadline，仅记录本次唤醒超时，并在正常退出和 Scheduler 锁丢失路径中有界回收；真实
同步阻塞专项与相关单测共 `22 passed`，Ruff、mypy 和 diff 检查通过。

该 lag 修复在 R8 结束后形成，截至本文记录**尚未构建镜像、尚未部署到生产**，不属于线上
1.5.6 的 revision `310a44c70c20f7cbfc601d19e19858380a61c20a`，也不是 R8 退出码 0 的前提。
后续只能在零活动任务窗口按正常发布门禁部署，并用新 Prometheus 序列验证；本文不得把源码候选冒充
已上线功能。

## 9. 持久证据、SHA-256 与复核

原始结果已保存在持久目录：

```text
/srv/gpu-control/load-results/sixapi-20260730-r8
```

同一份完整结果已制作成确定性 Git LFS 归档：

```text
artifacts/load-tests/sixapi-20260730-r8/sixapi-20260730-r8-results.tar.gz
size: 842402 bytes
sha256: ceddded2588b139ad5971c76ee70561c49e877908bf9d2decb0c07ab74ebbabc
```

归档采用排序路径、固定 mtime、numeric owner/group 与 `gzip -n`；两次独立构建逐字节一致。归档前
执行凭据模式扫描，未发现 Bearer token、API key 值、私钥头或 OpenAI 风格密钥；解压后的 15 项内部
checksum 也已再次全部通过。复核和提取说明见
`artifacts/load-tests/sixapi-20260730-r8/README.md`。

目录内 `checksums.sha256` 的 15 个条目已逐一执行 `sha256sum -c`，全部成功。关键证据为：

| 文件 | SHA-256 |
| --- | --- |
| `checksums.sha256` | `c5ae4401befecdef85702f076a24c9e902a7543d826c3c6ff62b980b425380de` |
| `manifest.json` | `ada21d3c083aefb080ab269bff6523f2ea949c6893adb08e439754547beb2ea0` |
| `summary.json` | `1463154198a005415d285f7567d2382b6922d17e873a6c95010eac704e4bcf57` |
| `telemetry.jsonl` | `a6a5efe469808addd2e43f96778e4bd5cb52f877d2d84e7a2e2e47f1fb90f7c5` |
| `events.jsonl` | `3a49770cee3db13143a6ec44376d6fe34979eafc26b08bc0b90a150cbdb0cf00` |
| `records.json` | `1e46d4d8e822673b240462d03d1bc8b15b971593b8279744798408efe64b3479` |
| `teardown.json` | `27b22ba02c3f899664238e55b5e6d30729071b5d142118fef33459457827ce1e` |
| `plan.json` | `751b423e680fe4f0c7acc8cf0b5efe9c69e744ee10e41191e6d6a58279cc086e` |
| `preflight.json` | `1f6bdedf0e6439fa243547d9b398abd1c00f793b44e4f347aabf398913521548` |
| `configuration/scenario.yaml` | `60f0a4f026c25acfcfacdef6493d26d374c9c68c6dc653a180eac61a07256d12` |
| `configuration/fixtures.yaml` | `c552ecf380db140dfe83addc27659cc312dd6f01d826fdd275401cf1a7fd4d01` |

`manifest.json` 记录每个文件的大小与内容 SHA；`checksums.sha256` 自身不可能包含自己的 SHA，故其
hash 作为本记录的外层锚点单独列出。结果中 `secrets_recorded=false`，未持久化 API 密钥或凭据。

## 10. 后续正式验收项

R8 已关闭六 API、120 VU 有界压力本身的红灯，但以下事项仍未关闭：

- 固定 B1/B6/B30/B64/B97/B300 与并发 `3×B97`；
- 本机与 1/2/3 节点同素材 A/B、三节点 B97 P50/90%/加速比/拖尾目标；
- 节点离线、损坏上传、prompt timeout、Scheduler restart、workflow drift、artifact tamper、非法
  cancel、幂等重放等完整故障矩阵；
- 3090-B DCGM 指标恢复、Scheduler loop lag 候选的受控部署与在线复核；
- 固定 SBOM generator、registry push 和四组件 registry manifest digest；
- 10% → 50% → 全量灰度、连续七天观察及动画管家/GPU Control 双方签字。

以上全部完成前，总体状态保持 `DEPLOYED_NOT_ACCEPTED`。
