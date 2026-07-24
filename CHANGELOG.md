# Changelog

## 1.2.0 — 2026-07-24

- 新增 ImageClip RGBA 序列帧批次 API：严格 manifest、ZIP_STORED、逐帧大小/SHA-256/图片校验与租户幂等。
- 新增父批次、帧、事件和批次产物持久化；内部帧任务分布到三台 GPU，支持有界投喂、重试、取消和重启恢复。
- 结果仅在全量帧 PNG/Alpha、路径、顺序和 SHA-256 校验通过后原子发布，失败不暴露部分结果。
- Web 任务列表以一个父批次展示，逐帧进度、节点分布、错误和完整结果归档统一置于详情页。
- 新增动画管家 V2 接口交接、生产部署记录和批次安全/幂等/隔离/归档测试。

## 1.0.0-rc1 — 2026-07-21

- 全量替换旧单机 SQLite/React/进程管理架构。
- 新增 PostgreSQL 任务真相、asyncio 单主调度、3090 主池与 4090 Reserve/Overflow。
- 新增可复现 ComfyUI 镜像、Fake ComfyUI、Node Agent、gpuctl 与三机部署脚本。
- 新增 FastAPI 公共/管理 API、RBAC、审计、回调、工作流注册和安全存储。
- 新增 LiClick 风格 Vue 管理台以及 Prometheus/Grafana/Loki/Alloy/Alertmanager。
- 新增空机部署、灾备、故障和 30 项验收文档。
