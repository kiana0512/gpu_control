# Substance Baker v7 GPU fencing 热修、Admin Retry 与成功验收记录

日期：2026-08-13（Asia/Singapore）

状态：`CANARY_SUCCEEDED`

适用节点：唯一 Windows Substance 节点 `3090-B`

## 1. 结论

3090-B 上四个 Windows Agent 已运行 `substance-baker-2026.08.12-v7`。第一笔 v7 领取在原生 Baker
启动前暴露 ComfyUI 容器 Python 路径错误；修复绝对 Python 路径后，真实复测又要求显式等待 `/free`
的异步显存释放。最终 Agent 已同时完成两项修正并与仓库字节级对齐。

控制面增加受限 Admin Retry 与 v7 fencing 结果兼容。原任务
`867d53b9-cfb6-49d5-b1d2-0007777e8072` 保留失败 attempt 审计，随后受控重试生成 attempt 3，真实
Substance Baker 执行成功并发布 12 个通过 SHA 校验的制品。烘焙功能已恢复。

这次修复不更改 Substance 参数、贴图合同、Baker 二进制，也不修改外部 ImageClip/ModelView
工作流、模型、Prompt、图结构或输出语义。

## 2. 最终 Agent 变更与身份

文件：`apps/substance_baker_agent/Invoke-GPUControlSubstanceAgent.ps1`

最终修复包括：

1. 容器执行从裸 `python` 改为 `/opt/python/bin/python3`；
2. 调用 ComfyUI `/free` 后，最长 30 秒轮询权威空闲 VRAM；
3. 每次 0.5 秒轮询都复查 ComfyUI 队列，防止释放期间有新 prompt 进入；
4. 队列不再为空、卸载未确认或显存阈值未满足时 fail-closed，不启动 Baker；
5. ComfyUI 不重启，任务结束后验证原进程身份连续。

| 制品 | SHA-256 |
|---|---|
| 热修前 v7 Agent | `91f83e0b0be6edb2a1b2156baad2b3d9a2b9ae021fd170178ceac19354e6053a` |
| 仅 Python 路径修正的中间候选 | `2511702d0cf8174e385065a5c932067996835fcd200d978c56bc88fbbc0b2bd1` |
| 最终 v7 Agent | `06fcb4cefb9aeb7e53693faf9f87a36a113324ba8df162738e416afdb9e4b399` |
| v7 Installer | `5385a5576f2f0519a95915133e7db3bb5fac1dc2a798618fe21929fbd133700b` |

3090-B 候选文件、四个实装 Agent 与 GPU Control 仓库文件已执行 SHA-256 和 `cmp` 双重核对，结果
为字节级一致。

## 3. 原失败 attempt

任务：`867d53b9-cfb6-49d5-b1d2-0007777e8072`

| 字段 | 事实 |
|---|---|
| 原状态 | `FAILED / RECOVERY_REQUIRED` |
| 原 Worker | `asset-worker-3090-b-windows-01` |
| 原 Attempt | `1` |
| 错误码 | `SUBSTANCE_COMFYUI_CONTINUITY_FAILED` |
| 根因 | OCI exit 127：生产 ComfyUI 容器没有裸 `python` |
| Baker | 未启动 |
| 审计处理 | 不改库、不覆盖失败记录，通过 Admin Retry 生成新 attempt |

该 attempt 证明 v7 版本门禁、任务领取和约 58.5 MB 输入下载可用，但不能作为烘焙成功证据。

## 4. Admin Retry 安全门禁

新增入口：

```text
POST /admin/asset-jobs/{job_id}/retry
```

只允许管理员对 `FAILED / RECOVERY_REQUIRED` 的 Substance continuity/execution 错误执行。入口在同一
数据库事务和 Worker 锁下检查：

- 原输入仍存在且 SHA 身份保留；
- 未发布任何原任务结果制品；
- 没有其他活动烘焙；
- 3090-B 为 `ONLINE / ACTIVE / current_jobs=0`；
- 没有 recovery、GPU fence 或专用窗口冲突；
- 恰好四个 v7 Baker Agent 在线、心跳新鲜、进程探针 `HEALTHY`；
- 四个 Agent 均为 0 当前任务、0 原生 Baker 进程；
- 管理员显式 `confirm=true`、填写原因，重试次数受上限约束并写审计事件。

Admin Retry 复用同一 job ID 和原始业务输入，不创建伪造成功记录；每次实际领取生成新的 attempt。

## 5. 真实成功证据

同一任务经 Admin Retry 后：

| 字段 | 结果 |
|---|---|
| 终态 | `SUCCEEDED / SUCCEEDED` |
| 成功 Attempt | `3` |
| Worker | `asset-worker-3090-b-windows-03` |
| 开始（UTC） | `2026-08-13 02:30:00.579710` |
| 结束（UTC） | `2026-08-13 02:30:37.798564` |
| Profile | `li3d-pbr-full-v2` |
| Baker | Adobe Substance 3D Baker `15.1.0` |
| 命令 | 10 条；成功标记存在，退出码有效 |
| GPU fencing | queue empty、models unloaded、空闲 23222/24576 MiB、比例 0.9449 |
| ComfyUI | 未重启，进程身份连续 |
| 制品 | 12 个、34,775,544 bytes、12 个唯一 SHA-256 |

制品 SHA-256：

| 制品 | SHA-256 |
|---|---|
| AO | `297599290b8cd480ea655640474b0feb873fe545fc4d2fe77d061129714ddc5a` |
| Base Color | `7ff48f996af248dc92e1cecf220b6cb74fcc7ada4f9e56faf2d3cd1f055c34cb` |
| Curvature | `b0f77e7196da6bc70c21e0663d76844de5490ebc05df6e7f87e70c766a106678` |
| Metallic | `c75edfa88583c8dd145b46e72782b0ee55583adf1218ad5953edfce1c1a97a98` |
| Normal DirectX | `d46853ef542757533224c64469c0117ed935f063307a3e2acd9fb32811c5ac45` |
| Normal OpenGL | `c9604adb395f4364010f446864bc293c51e64ec291350e5be1236e4c3304ac40` |
| Position | `75cca7413227bf1e489e07d4f3b2de2f8d8bd3579ced5423d77a078392b5e8ef` |
| Roughness | `5a5a2496237d498141c5fe52fbc82333ff961cd37d0c558547ec39e554f8cdca` |
| Thickness | `f13a9ac10df79e5a04b0986643be5f423ced028fdfb0095d2bf0617f840c43e8` |
| World Normal | `7e783b4f6f430426384e9742b7026777dedd1f7f9644a536ed4e7ccf83c77bb3` |
| `baker.log` | `2417ee2e13014ca48b8c7b6c651e44c56e0bfecfb8c97f4d0cbedc9a6c8f7213` |
| `baker_result.json` | `c7d8ea560052315fa8f24eac8a736b889932e20a3a57427912f0dc507930c2db` |

`baker.log` 含 10 个成功标记。控制面接受 schema 2 结果，验证新的
`queue_drained_models_unloaded_vram_verified` 策略及非空 `comfyui_drain_evidence`。

## 6. 结束后运行态

- `asset-worker-3090-b-windows-01`～`-04` 均为 `ONLINE`；
- `skill_version=substance-baker-2026.08.12-v7`；
- 四个进程探针均为 `HEALTHY`；
- `current_jobs=0`，`substance_active_processes=0`；
- 3090-B 无 recovery/fence 残留；
- ComfyUI 未因烘焙切换被重启；
- Asset API 已使用支持 v7 fencing 结果的控制面代码。

## 7. 自动化回归

`tests/unit/test_substance_baker_agent_contract.py` 固定：

- 必须使用 `/opt/python/bin/python3`；
- 禁止裸 `python` 回归；
- 必须使用最长 30 秒 monotonic 显存释放循环；
- 必须在循环内复查 ComfyUI 队列；
- 轮询间隔固定 0.5 秒。

控制面回归还覆盖 Admin Retry 权限、终态/错误类型、单一活动烘焙、3090-B 节点状态、四 Agent
版本与进程探针、输入/制品和审计事件。

## 8. Docker、Git 与回滚边界

Windows Agent 通过 PowerShell 安装包部署，不被当前 Dockerfile `COPY`。但本轮同时修改控制 API、
Asset API 和 Web UI，因此统一升版为 GPU Control `1.5.14` 并重新构建正式镜像与 LFS 离线归档；不能
覆盖不可变的 `1.5.13` tag 或归档。

回滚前必须确认没有活动 Baker。不得回滚到 `91f83e0b…` 或 `2511702d…` 后继续接单，因为两者分别
重新引入 Python 路径故障或缺少异步显存释放验证。生产可接单基线是最终
`06fcb4ce…e4b399`。

## 9. 验收口径

- Substance 功能闭环：已通过；
- 3090-B 唯一烘焙职责：保持不变；
- 4070Ti 烘焙能力：无；
- 六 API 功能 canary：均已有真实成功证据；
- 正式 1.5.14 镜像、LFS、Git 与综合稳定性结果：由发布记录继续补齐；
- 未完成正式综合压测前，不把“单项功能恢复”写成“容量验收完成”。
