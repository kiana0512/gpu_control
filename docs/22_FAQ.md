# FAQ

**为什么不用 Celery/Flower？** 三张固定 GPU、每节点单槽和专门的 4090 Guard，用 PostgreSQL 领取和 asyncio 执行更直接；详见 ADR。

**Redis 掉了会丢任务吗？** 不会。任务和租约在 PostgreSQL，Redis 仅唤醒/事件/限流；调度器有 500 ms 默认扫描。

**为什么不能提交普通 ComfyUI JSON？** UI JSON 是编辑器图结构；服务端需要 API prompt 对象和明确 binding。

**能让每个 ComfyUI 排很多任务吗？** 不能。本系统只在 GPU 空闲后提交一项，避免无法迁移和恢复的本地队列。

**4090 怎样参与？** `ACTIVE` 可在 3090 忙时参与；`OVERFLOW` 还需自动开关、队列/等待阈值、显存、利用率、时段、人工预留和哨兵全部通过。

**模型为什么不在镜像？** 体积大、变更频繁且分发慢；通过 manifest/rsync/SHA 管理更可靠。

**Windows 能证明生产可用吗？** 只能证明纯逻辑、API、Fake ComfyUI 和前端；驱动、GPU、systemd、UFW、Compose 与真实推理必须在 Ubuntu 验证。

