# 验收清单

状态：`✅` 当前 Windows/Fake 已验证；`🟨` 已实现但须 Ubuntu/容器/真实材料验证。

| # | 标准 | 状态/证据 |
|---:|---|---|
| 1 | 空数据库一键迁移 | ✅ SQLite 空库到 head；🟨 PostgreSQL 执行 |
| 2 | 无 GPU Fake 全链路 | ✅ Fake API/client 测试；服务级 Compose 待 Linux |
| 3 | 100 并发请求入队 | ✅ pytest 100/100 返回 202 |
| 4 | 仅两台 3090 稳态并行 | ✅ 单槽/主池策略单测；🟨 实机 |
| 5 | 3090 空闲不发 4090 | ✅ 策略单测 |
| 6 | 4090 RESERVED 不调度 | ✅ 策略单测 |
| 7 | OVERFLOW 全 Guard | ✅ 阈值/显存/利用率/哨兵/窗口逻辑；🟨 实机 |
| 8 | 单节点不超过 1 | ✅ schema/事务租约；🟨 PostgreSQL 竞态压测 |
| 9 | 租户公平 | ✅ 公平排序单测 |
| 10 | API 快速返回 202 | ✅ 集成测试 |
| 11 | scheduler 重启恢复 | ✅ prompt/回调恢复与超时看门狗代码；🟨 进程级故障注入 |
| 12 | Redis 故障不丢任务 | ✅ API 无 Redis 测试仍入队；🟨 服务恢复 |
| 13 | 已提交任务不盲重提 | ✅ prompt_id queue/history 分支 |
| 14 | 实时进度 | ✅ WS/SSE 实现；🟨 真实浏览器链路 |
| 15 | 取消/重试/Drain/Reserve/Release | ✅ API/Web、调度器运行中取消观察器及状态转换；🟨 实机操作 |
| 16 | 4090 释放模型 | ✅ 管理 API；🟨 实机 |
| 17 | job_id 全链路日志 | ✅ JSON context/Loki 查询 |
| 18 | 三机日志集中 Loki | 🟨 Compose/Alloy 配置，待三机 |
| 19 | Grafana 任务/GPU/API/调度 | 🟨 provisioning 已有，待数据验证 |
| 20 | 飞书告警与恢复 | 🟨 webhook 未提供 |
| 21 | Web 管理能力 | ✅ TypeScript build/test + 本地真实 API 浏览器 QA；🟨 生产 API |
| 22 | save/load 分发镜像 | 🟨 脚本已实现，待 Docker |
| 23 | rsync + SHA 模型 | 🟨 流式 SHA 脚本，模型未提供 |
| 24 | 初学者从空机到首任务 | 🟨 文档完成，待空机演练 |
| 25 | 升级/回滚/备份/故障手册 | ✅ 文档与脚本 |
| 26 | 无 Secret/模型/日志入 Git | ✅ ignore/example；仓库无有效 Git 元数据无法 diff 验证 |
| 27 | 真实记录测试 | ✅ IMPLEMENTATION_STATUS |
| 28 | 无关键空实现 | ✅ 静态搜索/测试；真实集成见未验证项 |
| 29 | README/状态一致 | ✅ 2026-07-21 更新 |
| 30 | USER_INPUT 仅外部材料 | ✅ 单独清单 |

生产签字前必须在三台 Ubuntu 执行 `make compose-validate`、NVIDIA 容器检查、空库迁移、一次真实工作流、三机 Loki、飞书告警/恢复、备份恢复演练和 100 并发服务级测试，并把日期、操作者、输出摘要填入本页。
