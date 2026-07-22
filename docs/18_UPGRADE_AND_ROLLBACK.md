# 升级与回滚

1. 记录当前应用/ComfyUI image tag 和数据库 revision。
2. `scripts/backup.sh`；在预发布运行 lint、类型、测试、compose config 和 Fake 负载。
3. 构建新 tag，禁止覆盖旧 tag；先在 4090/单个 3090 验证镜像与模型兼容性。
4. Web 中 Drain 节点，等待 `current_jobs=0`。
5. `docker compose run --rm api alembic upgrade head`，再滚动更新 API/scheduler/Web/节点。
6. 运行 `scripts/smoke_test.sh` 并观察至少一个告警周期。

应用回滚：恢复旧 `APP_IMAGE_TAG/COMFY_IMAGE` 后 `docker compose up -d --force-recreate`。数据库迁移只有在迁移说明确认 downgrade 安全时才执行；默认恢复升级前备份。工作流单独通过启停版本回滚。任何运行中 prompt 状态未知时先查 history，禁止重提。

