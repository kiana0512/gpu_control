# 2026-07-24 批量序列帧抠图生产部署记录

## 1. 部署结论

GPU Control `1.2.0` 已部署到 4090 主控，数据库已迁移到
`20260724_0004 (head)`。批量 ImageClip RGBA 父子任务、三卡分发、完整结果归档和 Web 父任务
详情均已生产验证。

本次不需要修改或重启两台 3090 的 ComfyUI：批次拆分、调度、状态汇总和结果归档全部属于
4090 控制面；3090-A/B 继续运行已经校验的
`registry.local:5000/gpu-control/comfyui:projects-0.2.2`，执行的仍是同一个 ImageClip 单帧 API
工作流。因此避免了不必要的 GPU 服务中断和模型冷启动。

## 2. 生产节点身份

| 节点 | 稳定 node_id | MAC（唯一硬件身份） | 当前心跳地址 | 职责 |
|---|---|---|---|---|
| 4090 | `control-4090` | `58:11:22:c1:66:63` | `10.3.34.11` | 控制面、归档、可调度 GPU |
| 3090-A | `worker-3090-a` | `18:c0:4d:9f:13:13` | `10.3.34.12` | 主算力 |
| 3090-B | `worker-3090-b` | `2c:f0:5d:76:7b:70` | `10.3.34.4` | 主算力 |

IP 由节点心跳动态更新；身份和授权不能用 DHCP 地址替代 node_id、MAC 和 GPU UUID。网络管理员
仍应为三台机器做 DHCP 保留，主控 4090 固定保留 `10.3.34.11`。

## 3. 变更范围

- Alembic 新增父批次、帧、批次事件、批次 artifact、批次幂等记录，并把内部 job 关联到帧；
- API 新增创建、查询、帧分页、SSE、取消和租户隔离下载；
- Scheduler 新增 12 帧有界 feeder、三卡分发、单帧重试、取消收敛、重启恢复和异步归档；
- 结果归档重新核对 PNG/Alpha、路径和 SHA 后原子发布；
- Web 列表隐藏内部子任务，只显示一个父批次，详情展示帧级追溯和结果下载；
- Nginx 批量入口从 55 MiB 对齐为 101 GiB multipart 上限，上传和代理超时调整为 86400 秒；
- API multipart 临时文件改放已挂载的 `JOB_ROOT`，避免大包先填满容器 overlay；
- `.env.example` 显式记录批次容量和并发参数；
- 对外合同冻结为 `38_GPU_CONTROL_MATTING_HANDOFF_V2.md`。

## 4. 备份和回滚点

升级前备份：

```text
/srv/gpu-control/backups/20260724T042528Z-pre-1.2.0/
```

其中 PostgreSQL custom dump 为 87104 bytes，仓库配置归档为 22869 bytes，生成后 SHA-256
复核通过。旧 `gpu-control-api:1.1.0`、`gpu-control-scheduler:1.1.0`、
`gpu-control-web:1.1.0` 镜像保留作为控制面回滚点；ComfyUI 镜像和模型未动。

回滚 1.1.0 前必须先确认是否保留 1.2.0 期间产生的批次记录和文件。不要只回退应用而忽略数据库
schema，也不要删除 `/srv/gpu-control/jobs/batches`。

## 5. 自动化验证

- Ruff：通过；
- mypy：23 个源文件通过；
- 后端单元/集成：66 项通过；
- 前端 lint、format check、build：通过；
- Vitest：2 项通过；
- npm audit：0 vulnerabilities；
- 浏览器真实生产页面：父任务 1 行、详情 3 帧、三节点、全部路径和 artifact SHA 可见，子 job
  未出现在顶层列表，控制台 0 error。

浏览器证据：`artifacts/screenshots/gpu-control-1.2.0-batch-detail.png`。

## 6. 真实生产批次证据

```text
external_batch_id: codex-real-batch-20260724-01
batch_id:          7f441948-886b-4ff5-81af-3354be978fdd
workflow:          imageclip-rgba 2026.07.23-bb243808
frames:            3
created:           2026-07-24 04:27:26 UTC
finished:          2026-07-24 04:28:14 UTC
status:            SUCCEEDED
```

帧分布：

```text
ordinal 0 → worker-3090-a
ordinal 1 → worker-3090-b
ordinal 2 → control-4090
```

结果：

```text
artifact_id: 5bb4a4e5-f79b-4c49-b46b-2e3443095f7f
filename:    7f441948-886b-4ff5-81af-3354be978fdd-rgba.zip
size:        2189120 bytes
sha256:      27421bc853ec4d6a64981856854ad07663d04e3aa48ff3bf39029ae1a05d1cb1
```

下载整包 SHA 与服务元数据一致；ZIP 可完整解包；manifest ordinal 为 `[0,1,2]`；三份文件
逐项 SHA 一致，均为 `1264×1353` RGBA PNG。同一幂等键重放返回 HTTP 200 和同一个 batch/artifact，
未产生重复任务。

## 7. 已发现并修复的问题

浏览器联调时发现 `/admin/jobs` 同时合并普通 job 和父 batch 后，一个时间字段为 datetime、另一个
为字符串，排序触发 HTTP 500。现已统一输出 ISO 时间字符串并新增“普通任务 + 父批次混合排序”
集成测试，API 重新部署后页面和控制台复测通过。

同时发现 Nginx 原 `client_max_body_size 55m` 与批次应用层 100 GiB 上限不一致。已调整入口为
101 GiB 并保持 `proxy_request_buffering off`，避免大包在 Nginx 完整缓冲。

## 8. 下一步真实联调

动画管家按 `38_GPU_CONTROL_MATTING_HANDOFF_V2.md` 先提交 30 帧 × 2 批次。重点核对：

- Web 只有两个父任务，不出现 60 条帧任务；
- 三台 GPU 均参与且每卡最多一个运行槽；
- 路径、文件名、ordinal、整包和逐帧 SHA 完整一致；
- 动画管家只在全部校验通过后原子发布；
- 同 key 重放不重复，取消和进程重启恢复符合合同；
- 正式长期运行采用 API Key，避免出口 IP 变化造成租户身份漂移。
