# 实施状态

最后更新：2026-07-28
版本：统一调度中心 1.5.0 + 3090-B Windows/WSL2 GPU 生产验收；V4 批量抠图永久修复候选等待安全滚动发布

## 2026-07-28 3090-B Windows / WSL2 GPU 上线

- `worker-3090-b` 已按物理 MAC `3c:7c:3f:a5:b0:4f`、GPU UUID
  `GPU-092a5184-5857-d196-5df2-efa9503368aa` 和固定 Windows IP `10.3.34.14` 登记；WSL NAT
  地址不作为节点身份。
- 节点当前为 `ONLINE / ACTIVE / PRIMARY`，ComfyUI healthy；Node Agent、SSH、Docker、
  containerd、node_exporter 均 active。
- 真实生产 HTTPS API 已在 B 完成 ImageClip RGBA 与 ModelView 局部重绘，均 `SUCCEEDED`，最终
  PNG 可解码并完成 SHA-256 核对；三节点同时 ACTIVE 的正常调度又把 ModelView 分配到 B。
- B 的 `/srv/comfyui/runtime` 已与容器 uid/gid 10001 对齐，修复首次上传 500；没有修改或重启
  ImageClip/ModelViewCreator 工作流。
- B 的 Blender Worker 已 `ONLINE`，Blender 5.1.2、Skill `asset-skills-2026.07.28-v3`、4 个独立
  CPU 槽；真实 UV/重拓扑 canary 留待下一维护窗口，不能提前写成业务验收通过。
- Web 已修复资产终态持续计时和窄抽屉显示；管理员人工复核按钮已从调度后台移除，复核应在用户端
  完成。客户侧复核决定回传接口仍待安全发布。
- 详细证据见 `57_2026-07-28_3090_B_WINDOWS_WSL2_GPU_ACCEPTANCE.md`。
- 已生成未部署的 `1.5.0-r1` 五镜像归档，大小 `826519963` bytes，SHA-256
  `0c68057f66f2c143f203f54b98533e1fb419a8df0f70ad7704646836b1521ccb`，由 Git LFS 分发。

## 2026-07-28 V4 批量抠图修复

- 已确认动画管家未取消 `assetclaw:VID_9D9EB9ACE6A1:matting:g1`；旧 Web 的“取消中”来自服务端
  将单帧失败错误映射为取消状态。
- 已定位 ordinal 34 的主控源 PNG 有效，而 3090-A 的 ComfyUI 输入文件为 0 字节；根因是旧上传
  重试使用 `overwrite=false` 且未回读验证远端最终字节。
- 新实现强制 `overwrite=true`，每次上传后回读并校验 size/SHA-256，完全一致后才提交 prompt；
  零字节遗留覆盖修复单元测试通过。
- 新父任务语义隔离失败帧并继续其他帧；只有明确取消请求才能进入 `CANCELLING`，失败批次最终
  `FAILED` 且不发布部分结果。
- 原父任务按原 batch ID、原 child job ID 和原 ordinal 恢复；4090 与 3090-A 已同时继续执行。
- 当前活动生产队列未排空，因此永久修复尚未滚动替换生产 API/Scheduler；不得提前写成已上线。
- 当前完整接口与联合验收合同见 `56_GPU_CONTROL_MATTING_HANDOFF_V4.md`。

## 1.2.0 生产增量

- 两台 3090 和一台 4090 均已接入、在线并完成真实三卡分配；节点以 node_id、MAC 和 GPU UUID
  保持身份，心跳可更新 DHCP 地址。
- ImageClip/ModelView 单图 API、Web、Scheduler、PostgreSQL、Redis、Nginx 和监控栈已在生产验证。
- 动画管家 ImageClip RGBA 序列帧批次已完成：严格 ZIP/manifest、父子任务、有界投喂、三卡
  调度、重试/取消/恢复、结果校验和原子 ZIP 发布。
- Web 顶层只显示一个批次父任务，帧级路径、节点、重试和错误只在详情分页展示。
- 对外冻结合同为 `38_GPU_CONTROL_MATTING_HANDOFF_V2.md`，部署证据为
  `39_2026-07-24_BATCH_MATTING_DEPLOYMENT_RECORD.md`。

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

- 2026-07-28 本轮针对性回归：Comfy 上传完整性、Node Agent 混合节点身份、Asset API 来源 IP
  自动客户共 `17 passed in 9.65s`（一次性容器、测试凭据、仓库只读挂载）。
- 本轮 Python 源码编译、5 个部署 Shell 脚本 `bash -n` 与 `git diff --check` 通过。
- 当前 Web production build 通过并已只替换 Web 容器；API、Scheduler 与三台 ComfyUI 未因此重启。

- `python -m ruff check .`：通过。
- `python -m mypy packages apps/api/src apps/scheduler/src apps/node_agent/src`：通过，23 个源文件。
- `python -m pytest -q`：66 passed；批次列表混合排序修复后相关集成 17 passed。
- 前端 `npm run lint`：通过。
- 前端 `npm run format:check`：通过。
- 前端 `npm test`：2 passed。
- 前端 `npm run build`：通过；`npm audit` 为 0 vulnerabilities。
- 浏览器：管理员真实登录生产 API；父批次只显示 1 行，3 帧详情、路径、三节点和 artifact SHA
  可见，内部子任务未出现在顶层，控制台 0 error。
- 部署配置：12 个 YAML 文件全部解析通过。
- Alembic：生产 PostgreSQL 已到 `20260724_0004 (head)`。

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
