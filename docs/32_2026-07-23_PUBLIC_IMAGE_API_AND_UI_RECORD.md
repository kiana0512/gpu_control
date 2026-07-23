# 2026-07-23 图片 API、调度器与管理台部署记录

## 当前结果

- 管理台：`https://10.3.34.11/`
- ComfyUI：`http://10.3.34.11:8188/`
- ImageClip 抠图 API：`POST /api/v1/services/imageclip-rgba`
- ModelView 局部重绘 API：`POST /api/v1/services/modelview-inpaint`
- 两个接口都使用 `multipart/form-data`，图片字段名固定为 `image`。
- 成功时 HTTP 响应体就是最终图片；响应头包含 `X-Job-ID` 和 `X-Client-ID`。
- 失败时返回 JSON，其中包含错误码、中文错误信息和可用于排障的 Job ID。
- 业务软件不需要持有 API Key。Nginx 固定传递真实来源地址，API 按来源 IP 自动建档、限流、计算配额和隔离任务。管理员以后可以为指定客户配置固定 IP；当前默认允许新 IP。

## 最简调用方法

```bash
curl -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: order-001-attempt-1' \
  -F 'image=@input.png' \
  -k \
  --output result.png
```

`Idempotency-Key` 应由业务软件为每笔业务生成。网络重试时继续使用相同值，避免重复生成。

完整 Python 和 cURL 示例也已放到管理台：进入“API 客户”，点击“查看图片 API”。

## 4090 节点中文操作

- **投入调度**：立即允许调度器向该 GPU 分配任务。
- **备用溢出**：只在溢出策略开启且达到阈值时使用 4090。
- **保留 GPU**：ComfyUI 仍可手工使用，但调度器不会占用。
- **排空任务**：不接新任务，等待已运行任务结束。
- **启动/停止 ComfyUI**：通过 Node Agent 控制对应 Docker 服务。
- **中断当前任务**：停止正在执行的 ComfyUI 队列任务。
- **释放模型显存**：卸载模型并清理显存缓存。
- **安全重启**：仅在节点空闲并经过二次确认后重启 ComfyUI。

端到端测试完成后，按正式开放要求切换为：`control-4090 = ACTIVE / ONLINE`。当前用户请求会立即进入这张 4090；两台未接入的 3090 保持 `DISABLED / OFFLINE`。

## 真实端到端验收

- 工作流：`modelview-inpaint:2026.07.23-b22bb377`
- Job ID：`334cfaab-acf7-458c-b664-c98c632c9bb5`
- 节点：`control-4090`
- 最终状态：`SUCCEEDED`，进度 `100%`
- ComfyUI Prompt ID：`78f03719-d423-4cbf-9af1-dcc7a2612f9e`
- 输出：PNG，1536×1024，1,818,657 字节
- SHA-256：`ba78a0d7c2fb61b21389190432caf7dfd016c3828bf661634903cdb1bd5dbd03`
- 持久化产物：`/srv/gpu-control/jobs/2026/07/23/334cfaab-acf7-458c-b664-c98c632c9bb5/output/000-ComfyUI_00004_.png`

复测脚本：

```bash
cd /opt/gpu-control
scripts/test_modelview_api.sh /path/to/input.png /tmp/modelview-result.png
```

脚本会释放旧模型、临时投入 4090、调用正式 HTTPS API、验证返回图片，并在退出时自动把 4090 恢复为保留状态。

## 本次修复

1. 管理员登录加入 15 分钟访问令牌和 7 天刷新令牌；页面自动续期，旧会话部署后只需重新登录一次。
2. 管理台主要数据页每 10 秒自动刷新，显示最后更新时间和明确的错误重试入口。
3. API 客户页改为来源 IP 自动发现，并直接展示两个图片接口、cURL 与 Python 调用示例。
4. 节点状态和操作按钮改为中文，补充“投入调度”和“备用溢出”的明确区别。
5. Nginx 对同步图片请求关闭请求体缓冲，并把读写超时提高到 1900 秒。
6. 修复 Scheduler 在事务回滚后读取已过期 ORM Node 对象导致的 `MissingGreenlet` 崩溃。修复后原排队任务无需重建即可被 4090 认领。
7. 新增 API 客户来源 IP 字段与迁移 `20260723_0003`。
8. 节点页只展示真正上报过心跳的设备；从未接入的 3090 规划位不再作为设备卡片展示，后续首次心跳后自动出现。
9. 节点维护操作改为右侧抽屉，解决“更多操作”被卡片裁切；4090 的 ComfyUI 按钮固定打开实际可用地址 `http://10.3.34.11:8188/#551d82b0-b1fb-483a-a5ea-564bdb813625`。
10. 任务表整行可点击并打开真实任务详情，显示完整 Job ID、Prompt ID、执行节点、时间、进度与错误；取消、重试和诊断包下载均提供加载、成功和失败反馈。
11. API 客户增加“管理设置”，可修改名称、启停、最多排队、最大并发、每日配额、调度权重、固定来源 IP 和回调域名；保存结果写入 PostgreSQL 和审计日志。
12. 原“系统设置”改为只读“系统信息”，不再重复暴露内部键；调度参数集中到中文“调度策略”页，一次保存所有变更。
13. 日志中心改为真实任务记录与管理员审计记录的统一检索页，支持详情抽屉和 Grafana 深入查看，不再保留无响应的占位表单。

## 当天问题与解决方案

| 现场问题 | 原因 | 已采用方案 |
|---|---|---|
| 任务长期排队但 4090 在线 | 4090 处于 `RESERVED`，且 Scheduler 回滚事务后访问过期 ORM Node 触发 `MissingGreenlet` | 修复事务边界；测试阶段把 4090 切为 `ACTIVE`，成功任务结束后可再保留 |
| Web 数据必须按 F5 才更新 | 页面只在首次挂载读取数据 | 所有主要页面加入 10 秒自动刷新、最后更新时间和手工重试 |
| 管理令牌过期后页面失效 | 只有短期 access token | 增加 7 天 refresh token，提前 60 秒自动续期；续期失败才回登录页 |
| 节点“更多操作”看不到 | 绝对定位菜单被卡片和布局裁切 | 改为全高右侧维护抽屉，并按风险分组操作 |
| 任务行和诊断按钮点了无反馈 | 行按钮没有事件，下载过程没有提示 | 任务详情抽屉；下载、重试、取消全都有状态和错误提示 |
| API 客户只能查看不能管理 | 后端只有创建/列表，没有更新接口 | 新增 `PUT /admin/clients/{client_id}` 与 Web 编辑表单，校验 IP 冲突并写审计 |
| 调度/系统设置显示英文内部键 | 通用占位组件直接遍历后端对象 | 新建中文专用调度页和只读系统信息页，删除旧占位分支 |
| 3090 尚未到场却显示成设备 | 初始化时写入了规划节点记录 | Web 仅显示上报过心跳或当前非离线的真实设备 |
| ComfyUI 工作流缺节点/模型 | 公共节点未固化、内部节点和模型路径未正确外挂、LFS 仍是指针 | 公共节点固定进镜像；两个业务仓库只读挂载；ModelViewCreator 执行 `git lfs pull` 并按 SHA-256 校验 |
| 老浏览器标签出现 `has no class_type` | 标签保存了升级前的节点占位对象 | 关闭旧标签，强制刷新后从工作流列表重新打开，禁止覆盖源工作流 |

## Web 管理台当前操作路径

- **任务**：点击任意任务行或“查看”打开详情；失败/超时任务可重试，运行任务可取消，所有任务都可下载诊断包。
- **GPU 节点**：主按钮控制是否接单；“打开 ComfyUI”用于人工调试；“维护操作”包含排空、服务启停、安全重启、中断和显存释放。
- **工作流**：查看两个真实 API 工作流版本、模型/节点要求和启用状态。
- **API 客户**：“查看图片 API”复制统一调用示例；每行“管理设置”调整该真实来源 IP 的配额和访问策略。
- **调度策略**：当前只有 4090 时保持手工投入；两台 3090 接入后再启用 4090 自动溢出。
- **日志中心**：按任务 ID、请求 ID、节点、来源 IP 或错误码检索；需要完整容器日志时跳转 Grafana。
- **系统信息**：只读显示真实服务状态和访问地址，不在此页修改调度配置。

## 无中断上线方法

本次客户编辑 API 使用并行容器完成零停机切换：先启动 `api-next` 并验证数据库、Redis
和 OpenAPI 路由，再让 Nginx graceful reload 把新请求切到它；随后更新正式 `api`，健康后
再 graceful reload 切回并清理临时容器。Nginx 旧 worker 会等待已有长连接结束，因此同步
图片请求不会因为正式 API 容器更新而被强行中断。常规只改 Web 时只执行：

```bash
cd /opt/gpu-control
docker build -t gpu-control-web:1.1.0 apps/web
docker compose --env-file .env -f deploy/control-plane/compose.yaml \
  up -d --no-deps --force-recreate web
```

该命令不会重启 API、Scheduler 或 ComfyUI。升级前保留的本地回滚标签为：

```text
gpu-control-web:rollback-before-ops-ui
gpu-control-api:rollback-before-client-edit
```

## 验证记录

- 新增 API 客户编辑集成测试：通过，包含保存配额/启停/IP 与重复 IP 冲突检查。
- API 集成测试当前结果：12 passed、2 个旧断言失败；失败项仍假设“工作流列表必须 API Key”和旧 Alertmanager 开发密钥，与当前来源 IP 免 Key设计/现网密钥配置不一致，需后续更新旧测试基线。
- 调度器单元/集成测试：8 passed。
- Web ESLint：通过。
- Web Vitest：1 passed。
- Web 生产构建：通过，2046 个模块完成打包。
- 正式 HTTPS：工作流列表无 Key 返回 200；两个服务缺少图片时均正确返回 422。
- 最终容器状态：API、Web、Scheduler、Nginx、ComfyUI 全部 healthy。

## 镜像说明

- 当前真实验收通过并运行在 8188 的 ComfyUI 镜像：
  `gpu-control/comfyui:projects-0.2.2`；完整 Image ID、锁指纹和离线包 SHA-256 见
  `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`。
- 模型、工作流和项目资源继续由宿主机目录外挂，不写入大镜像层。
- API、Web、Scheduler 当前服务标签均为 `1.1.0`；部署前版本已保留本地 rollback 标签。
- 跨机器导入、模型同步和镜像更新流程见 `docs/31_2026-07-22_4090_DEPLOYMENT_RECORD.md` 与 `docs/13_PUBLIC_API_GUIDE.md`。

## 收尾、清理与 3090 交接

- `projects-0.2.2` 已在真实 RTX 4090 上隔离启动；`pip check` 无损坏依赖，两套生产
  API 工作流的缺失节点均为 0。正式 8188 随后安全切换到同一 Image ID。
- ImageClip 4 个模型和 ModelViewCreator 5 个模型，共 9 个文件，已再次按 manifest
  校验大小及 SHA-256，全部为 `OK`。
- 最终离线归档已执行两次 SHA-256 读取校验，供 3090 使用；精确值见 33 号交接文档。
- 清理了本次 UI 构建产生的 `ops-redesign`、`ops-final` 临时 builder 镜像、无标签的
  旧 `0.2.2` 候选镜像、临时验证容器和一个空转的旧构建等待进程。
- 保留 `projects-0.2.1` 容器、Web/API rollback 标签和全部历史 ComfyUI 回滚容器；
  未清理 Docker volume、构建缓存、任务、数据库、输入输出或任何模型。
- 3090 的唯一当前执行页为 `docs/33_3090_NODE_DEPLOYMENT_HANDOFF.md`；旧文档中的
  `0.1.0` 命令只作为历史记录。
- 收尾检查覆盖 README 与 docs 下共 48 个 Markdown 文件，本地链接缺失数为 0；
  3090 Compose 配置解析、全部 shell 脚本语法和 `git diff --check` 均通过。
