# 统一调度中心（GPU Control）

> 今天部署请先打开 [文档总入口](docs/00_START_HERE.md)，再按 [三机当天部署手册](docs/28_TODAY_DEPLOYMENT_MANUAL.md) 和 [现场勾选表](docs/30_TODAY_ONSITE_CHECKLIST.md) 执行。单文件离线版位于仓库根目录 `GPU_CONTROL_成品部署联调与核心逻辑手册.pdf`。

面向两台 RTX 3090 工作节点和一台 RTX 4090 控制中心的统一任务调度、运维与可观测平台；GPU
推理平面负责 ComfyUI，独立 Asset Processing 平面负责 Blender CPU 资产任务。

当前版本：`1.5.0`。GPU 推理平面继续提供三机图片 API 与分布式序列帧调度；独立 CPU Asset 平面新增 Blender PBR UV 和 AI 重拓扑 API，支持并发 Worker、轮询与可续传 SSE 进度、动态 ETA、多视角参考、三模型四视图复核、人工发布门禁和逐产物 SHA-256 校验。两个平面使用独立队列与租约，资产任务不会占用或阻塞 GPU 推理槽。

```mermaid
flowchart LR
  C["业务客户端"] --> N["Nginx / API"]
  N --> P[("PostgreSQL\n任务真相来源")]
  N -.唤醒/事件.-> R[("Redis")]
  S["asyncio Scheduler\n单主 advisory lock"] --> P
  S --> A["3090-A / ComfyUI"]
  S --> B["3090-B / ComfyUI"]
  S -.ACTIVE/OVERFLOW.-> G["4090 / ComfyUI"]
  A & B & G --> L["Alloy → Loki / Prometheus → Grafana"]
```

当前生产模式为 4090、3090-A、3090-B 三节点全部 `ACTIVE`，合计 3 个执行槽位。维护期间可把 4090 切换为 `OVERFLOW`，此时才启用队列阈值、等待时长、哨兵文件、利用率、显存和允许时段门槛。每个 ComfyUI 只保留一个本系统任务。

## 组件

| 组件 | 职责 |
|---|---|
| FastAPI | API Key、JWT/RBAC、任务/工作流/管理 API、SSE、幂等与配额 |
| PostgreSQL | 任务、事件、尝试、租约、节点、审计、回调的唯一持久真相 |
| asyncio scheduler | `SKIP LOCKED` 领取、公平队列、节点选择、执行、恢复与回调 |
| Redis | 非持久唤醒、实时事件和限流；中断不会丢任务 |
| ComfyUI client/Fake | 上传、提交、WS、历史、下载、取消、释放模型；无 GPU 可测 |
| Vue 3 管理台 | LiClick 风格总览、任务、节点、工作流、客户、策略、日志和审计 |
| Alloy/Loki/Grafana | 三机日志集中、指标、仪表盘与告警 |
| Node Agent / `gpuctl` | HMAC 受限运维与统一启动器；不挂 Docker Socket |

## 5 分钟无 GPU 开发验证

Python 3.11/3.12 与 Node 22：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,load]"
pytest -q
uvicorn tests.fake_comfyui.app:create_app --factory --port 8188
```

另开终端验证 `curl http://127.0.0.1:8188/system_stats`。前端：

```bash
cd apps/web
npm ci
npm run test
npm run dev
```

Windows PowerShell 使用 `.\.venv\Scripts\python.exe -m pytest -q`。完整本地服务链见 [负载测试](docs/21_LOAD_TEST_AND_CAPACITY.md)。

需要审阅真实渲染的管理台时，可用 `scripts/seed_demo.py` 向单独的 SQLite 库写入明确标记的演示数据，再用同一个 `DATABASE_URL` 启动 API。该脚本拒绝 PostgreSQL URL，不会写入生产库，也不会伪造真实工作流或模型。

今天直接部署请只读 [三机当天部署与联调手册](docs/28_TODAY_DEPLOYMENT_MANUAL.md)；成品功能、算法和测试证据见 [成品报告](docs/29_PRODUCT_RELEASE_AND_TEST_REPORT.md)。旧的分章节文档保留作深入参考。

单文件可打印版本：[成品部署、联调与核心逻辑 PDF](GPU_CONTROL_成品部署联调与核心逻辑手册.pdf)。

## 从空服务器开始

先读 [准备清单](docs/04_PREPARATION_CHECKLIST.md)，再依次执行 [4090 安装](docs/05_CONTROL_4090_INSTALL.md)、[3090 安装](docs/06_WORKER_3090_INSTALL.md)、[首次部署](docs/11_FIRST_DEPLOYMENT.md)。不要先手工安装 ComfyUI，也不要使用 `docker commit`。

常用命令：

```bash
scripts/gpuctl doctor
scripts/gpuctl comfy build
scripts/gpuctl deploy control
GPU_CONTROL_ROLE=node scripts/gpuctl deploy node
scripts/gpuctl comfy status
scripts/gpuctl comfy logs
scripts/gpuctl models sync --host 192.168.10.11 --dry-run
scripts/gpuctl diagnostics job JOB_ID
make verify
```

## 文档

- [架构与网络](docs/02_ARCHITECTURE.md) · [端口](docs/03_NETWORK_AND_PORTS.md)
- [镜像构建](docs/07_COMFYUI_IMAGE_BUILD.md) · [镜像分发](docs/08_IMAGE_DISTRIBUTION.md) · [模型同步](docs/09_MODEL_SYNC.md)
- [工作流接入](docs/10_WORKFLOW_ONBOARDING.md) · [管理台](docs/12_WEB_ADMIN_GUIDE.md) · [公共 API](docs/13_PUBLIC_API_GUIDE.md) · [动画管家批量抠图 V2](docs/38_GPU_CONTROL_MATTING_HANDOFF_V2.md)
- [调度设计](docs/14_SCHEDULER_DESIGN.md) · [日志排错](docs/15_LOGGING_AND_TROUBLESHOOTING.md) · [监控飞书](docs/16_MONITORING_AND_FEISHU.md)
- [备份恢复](docs/17_BACKUP_AND_RESTORE.md) · [升级回滚](docs/18_UPGRADE_AND_ROLLBACK.md) · [安全](docs/19_SECURITY.md) · [故障手册](docs/20_FAILURE_RUNBOOK.md)
- [验收清单](docs/23_ACCEPTANCE_CHECKLIST.md) · [仍需提供的材料](docs/USER_INPUT_REQUIRED.md) · [实施状态](docs/IMPLEMENTATION_STATUS.md)
- [4090 与双项目部署实录](docs/31_2026-07-22_4090_DEPLOYMENT_RECORD.md) · [图片 API、真实任务与 Web 管理台实录](docs/32_2026-07-23_PUBLIC_IMAGE_API_AND_UI_RECORD.md) · [3090 当前接入交接](docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md) · [3090-B 与三卡 10 客户验收](docs/35_2026-07-23_3090_B_AND_THREE_NODE_ACCEPTANCE.md)
- [批量抠图 1.2.0 生产部署记录](docs/39_2026-07-24_BATCH_MATTING_DEPLOYMENT_RECORD.md)
- [Asset V3：UV / 重拓扑 API 与真实两机验收](docs/55_ASSET_UV_RETOPOLOGY_V3_API_AND_LIVE_ACCEPTANCE.md)
