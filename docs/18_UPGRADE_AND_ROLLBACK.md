# 升级与回滚

1. 记录当前应用/ComfyUI image tag 和数据库 revision。
2. `scripts/backup.sh`；在预发布运行 lint、类型、测试、compose config 和 Fake 负载。
3. 构建新 tag，禁止覆盖旧 tag；先在 4090/单个 3090 验证镜像与模型兼容性。
4. Web 中 Drain 节点，等待 `current_jobs=0`。
5. `docker compose run --rm --no-deps api alembic upgrade head`；随后按当前发布文档规定的协议兼容顺序，
   每次只对一个明确 service 使用 `--no-deps --no-build --pull never --force-recreate` 滚动。1.5.9 的
   固定顺序为“四个 Windows v6 Agent → Asset API 1.5.9 → 三台 Linux Worker 1.2.5 →
   API/Web/Scheduler”，且 Scheduler 最后。
6. 运行 `scripts/smoke_test.sh` 并观察至少一个告警周期。

应用回滚：保持 intake 冻结和节点 `DRAINING`，恢复已记录的旧应用镜像身份后，每次只重建一个明确的
应用 service；禁止执行无 service 范围的 `docker compose up/down --force-recreate`，也不得在应用回滚中
改写 `COMFY_IMAGE`、停止或重建 ComfyUI。数据库迁移只有在迁移说明确认 downgrade 安全时才执行；
默认恢复升级前备份。工作流单独通过启停版本回滚。任何运行中 prompt 状态未知时先查 history，禁止重提。
