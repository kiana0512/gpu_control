# 2026-07-27 统一调度中心 Web 与 Blender Worker 预部署记录

状态：`WEB LIVE / BLENDER IMAGE STAGED / ASSET CONTROL PLANE OFF`
原则：生产 GPU 后端有任务期间，不重启 API、Scheduler、数据库、Nginx 或任何 ComfyUI。

## 1. Web-only 上线

线上 Web 镜像：

```text
gpu-control-web:1.4.0-dev.20260727.2
sha256:a479541fc98ffcbdde0c0bdc897b674dc71ab97b047e3ac263f75195fd60e3f3
```

用户可见变更：

- 产品名改为“统一调度中心”；
- “GPU 节点”改为“计算节点”；
- 新增 `/asset-processing` 资产处理候选页；
- 候选页明确区分“镜像已验证”和“后端待安全窗口启用”；
- 展示 Blender 5.1.2、候选 13 CPU 槽、独立队列、原子四件套和 API 路径。
- ModelView 新 `prompt` 协议受 `VITE_MODELVIEW_PROMPT_ENABLED` 构建开关保护；当前线上为关闭，调用
  示例仍只发送 `image`，系统信息明确显示“prompt 候选协议待安全窗口启用”。

首次使用 `/assets` 时发现它与 Vite 静态产物目录 `/assets/` 冲突：页面内导航可用，但直接刷新会
发生内部主机重定向并返回 403。正式路由已改为 `/asset-processing`，API 路径
`/api/v1/assets/*` 不变。线上直接请求返回 HTTP 200 和 `<title>统一调度中心</title>`。

更新前后以下后端容器 ID 完全一致，并保持 `healthy`：

- `gpu-control-api-1`；
- `gpu-control-scheduler-1`；
- `gpu-control-postgres-1`；
- `gpu-control-nginx-1`。

2026-07-27 15:51 的 `.2` Web-only 更新前后，以上四个容器以及 `comfyui-4090` 的容器 ID 逐项
完全一致且均为 `healthy`。更新后只读队列为 `production RUNNING=3、QUEUED=17`，说明生产任务继续
消化，没有因 Web 更新重新入队或中断。Web 自身新容器随后转为 `healthy`。

Web 回滚：

```bash
cd /opt/gpu-control
APP_IMAGE_TAG=1.4.0-dev.20260727.1 docker compose \
  -f deploy/control-plane/compose.yaml up -d --no-deps web
```

## 2. 浏览器 QA

Browser 插件当前未安装，因此按前端调试流程使用隔离 Playwright 1.55.0：

| 检查 | 桌面 1440×1000 | 移动 390×844 |
|---|---|---|
| 页面标题 | 统一调度中心 | 统一调度中心 |
| 有意义内容 | PASS | PASS |
| 框架错误层 | 无 | 无 |
| console warning/error | 0 | 0 |
| 横向溢出 | 1440/1440 | 390/390 |
| 资产处理导航 | PASS | 页面直达 PASS |

`.2` 热更新额外复核：桌面 `/asset-processing` 与移动端 `/settings` 均为统一标题、console
warning/error 为 0、横向宽度分别为 `1440/1440` 和 `390/390`；系统信息页实际渲染了候选协议提示，
没有把未上线的 prompt 伪装成可用能力。

验证截图仅保存在 `/tmp/gpu-control-playwright/evidence/`，不提交进仓库。

## 3. Blender 固定镜像

```text
runtime: li3d/blender-runtime:5.1.2
runtime image ID: sha256:60099f2b3217335a3ea055d6cb202e1d1895199b64e7d75f2c47a2225ce7cac8

worker: li3d/blender-worker:1.0.0
worker image ID: sha256:8b926307d52d393e995cf9e32fba6abf362c6b1ce3790f43f790bc8a50b08a64
worker size: 687518125 bytes

temporary distribution archive SHA-256:
7c41594e5035a37a23d4baae12582862cd198942bf7a398a4a7e7500d9aaeaeb
```

Blender 官方归档：5.1.2 stable，build hash `ec6e62d40fa9`，归档 SHA-256
`21a6ab66b2a8b9f035fdb39c6445cdbe91e2fe1dcff30786148b757df7f9a9c5`。

## 4. 两台 3090 身份和验收

镜像分发前按 MAC 强校验，未按易变 IP 单独认机：

| 节点 | 当前 IP | 主机名 | 固定 MAC | 结果 |
|---|---|---|---|---|
| 3090-A | 10.3.34.12 | lilithgames1 | `18:c0:4d:9f:13:13` | PASS |
| 3090-B | 10.3.34.4 | lilithgames3 | `2c:f0:5d:76:7b:70` | PASS |

两机都先复算 656 MB 分发归档 SHA，再执行 `docker load`。加载后镜像 ID 均严格等于
`sha256:8b9263...`。验收命令由 `scripts/accept_blender_worker.sh` 固化，执行时：

1. `--network none`；
2. 创建一个临时立方体 `.blend`；
3. 运行真实 UV 脚本；
4. 导出 `model_PBR_UV.blend`、`model_PBR_UV.fbx`；
5. 生成 `model_report.json`、`model_QA.json`；
6. 验证四个文件非空、`passed=true`、`hard_failures=[]`；
7. 输出每个文件 SHA-256；
8. 清理唯一临时目录。

A、B 均得到：

```text
qa_passed=true hard_failures=0
blender_worker_acceptance=PASSED image=li3d/blender-worker:1.0.0
```

BLEND/FBX 包含生成时间等非语义元数据，因此两次验收的文件 SHA 不要求相同；镜像 ID、输入、QA
规则、报告内容和管线版本必须一致。生产 artifact 仍逐任务记录自己的 SHA。

## 5. 明确未启用的内容

- 未执行 migration `20260727_0006`；
- 未启动 Asset API；
- 未生成/写入生产 `ASSET_WORKER_HMAC_SECRET`；
- 未启动任何常驻 Blender Worker；
- Asset Worker 未发送心跳、未领取任务；
- 4090 尚未加载 Blender Worker 镜像；
- 未进行复杂模型、材质保真和 13 槽并发压测。

因此 Web 页面中的 CPU Worker 只能显示“部署候选”，不能显示 ONLINE。

## 6. 临时文件清理

镜像成功加载和验收后，可删除以下可再生临时文件，不删除 Docker 镜像：

```text
/tmp/li3d-blender-worker-1.0.0.tar
/tmp/accept_blender_worker.sh（两台 3090）
/tmp/gpu-control-blender-acceptance.*
```

这些临时文件不可作为回滚点；回滚点是固定 Docker image ID 和仓库源码。
