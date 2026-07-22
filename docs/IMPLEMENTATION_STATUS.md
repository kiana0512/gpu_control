# 实施状态

最后更新：2026-07-22  
版本：1.1.0 三机现场部署候选版

## 状态定义

- `已实现`：仓库存在完整实现，不是目录壳或 TODO。
- `本机已测`：Windows/SQLite/Fake ComfyUI 或浏览器环境真实运行通过。
- `现场待测`：必须在 Ubuntu、Docker、NVIDIA、PostgreSQL 或真实业务材料下执行。
- 只有“现场已测”才能写入生产验收通过；本文件当前没有虚报该状态。

| 范围 | 已实现 | 本机已测 | 现场待测 |
|---|---|---|---|
| PostgreSQL 持久队列/迁移 | 是 | 空库迁移到 0002、SQLite 集成测试 | Ubuntu PostgreSQL 17、SKIP LOCKED 实例竞争 |
| asyncio Scheduler | 是 | 领取、租约、恢复、取消、超时、100 并发 | 三机长稳、真实 WS/历史/下载 |
| 3090 优先/4090 溢出 | 是 | Guard、事务后复核、动态设置测试 | 真实 GPU 利用率/显存/时段/sentinel |
| API/幂等/租户 | 是 | 100 同 key 只产生一个 job、跨租户拒绝 | Nginx HTTPS 下真实客户调用 |
| 工作流注册 | 是 | API 格式、bindings、节点兼容、启用测试 | 真实 Export Workflow (API) 与模型 |
| ComfyUI 统一镜像 | 是 | Dockerfile/锁文件静态验证 | 主控构建、三机 image ID、真实启动 |
| Node Agent/后台运维 | 是 | HMAC、防重放、固定命令测试 | systemd/sudo/Docker start-stop-restart |
| 管理台 | 是 | lint、format、Vitest、build、浏览器登录/页面/移动端 | 现场 HTTPS、真实节点操作 |
| Loki/Alloy/监控/飞书 | 是 | 配置静态检查、告警 API 测试 | 三机日志、Prom targets、飞书实发 |
| 初始化/部署脚本 | 是 | 参数/生成文件单测、Python 静态检查 | 三台 Ubuntu 顺序执行 |
| 文档/PDF | 是 | Markdown/PDF 生成和渲染检查 | 操作者按 PDF 完成现场签字 |

## 当前验证结果

- `python -m ruff check .`：通过。
- `python -m mypy packages apps/api/src apps/scheduler/src apps/node_agent/src`：通过，22 个源文件。
- `python -m pytest -q --basetemp=.pytest-tmp-final2`：51 passed（34.20 秒）。
- 前端 `npm run lint`：通过。
- 前端 `npm run format:check`：通过。
- 前端 `npm test`：1 passed。
- 前端 `npm run build`：通过，2038 modules transformed。
- 浏览器：管理员真实登录本地演示 API；总览、节点、调度页面 DOM/视觉检查；控制台 0 warning/error；390×844 无横向溢出。
- 部署配置：12 个 YAML 文件全部解析通过。
- Alembic：空 SQLite 到 `20260722_0002 (head)` 通过。

## 为当天落地完成的关键修复

1. 一条命令生成主控 `.env`、两个 worker env、三节点清单、Prometheus 实际 IP 和初始管理员密码。
2. ComfyUI 模型统一挂载到默认 `/opt/comfyui/models`；镜像固定 commit 和依赖，三机导入同一 tar。
3. PostgreSQL/Redis/Grafana/Loki 等改用 Docker 命名卷，避免空机首次启动的 UID 写权限冲突；job/runtime 目录对齐容器 UID 10001。
4. Node Agent systemd 允许固定 sudo 命令和 Docker Unix Socket，后台启动/停止/重启不再被沙箱配置阻断。
5. 导入工作流自动生成三节点兼容性，没有兼容节点不能启用。
6. 动态 4090 配置由 Scheduler 每轮从数据库读取，后台保存后真实生效。
7. 管理台补充启动/停止、任务取消、诊断包、工作流 JSON 导入、真实 dashboard 指标。
8. Alertmanager webhook 鉴权，告警先持久化再异步发送飞书。
9. 上传 staging、事务幂等、request_id 一致性和维护 DRAINING 协议已补齐。
10. 新增 `docs/28_TODAY_DEPLOYMENT_MANUAL.md`，严格按三机实际执行顺序编排。

## 现场首日执行入口

只读这一份即可开始：`docs/28_TODAY_DEPLOYMENT_MANUAL.md`。打印/离线版本为仓库根目录 `GPU_CONTROL_成品部署联调与核心逻辑手册.pdf`。

现场完成后，在本表新增日期、主机名、命令、结果和操作者，不覆盖本机测试记录。
