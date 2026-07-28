# 3090-B Windows / WSL2 混合节点 GPU 上线与真实验收记录

文档日期：2026-07-28
控制中心：`control-4090` / `10.3.34.11`
节点：`worker-3090-b` / Windows `10.3.34.14` / WSL2 SSH `10.3.34.14:2222`
结论：GPU 推理已生产验收；Asset Worker 已上线，B 的真实 UV/重拓扑 canary 留待下一维护窗口

## 1. 当前结论

- 3090-B 已作为与 3090-A 同级的 `PRIMARY / ACTIVE / ONLINE` GPU 节点接入统一调度中心。
- 4090 主控通过 Windows 端口代理访问 WSL2 的 SSH、ComfyUI、Node Agent 与监控端口。
- WSL2 已有常驻 Keepalive 和只在映射异常时修复的 Watchdog；修复后 SSH 握手 `100/100`，失败 `0`。
- ComfyUI 使用与 4090、3090-A 相同的批准镜像、业务仓库提交、管线 SHA 和模型目录。
- 真实统一 HTTPS API 已分别完成 ImageClip RGBA 抠图和 ModelView 局部重绘，最终文件可解码且 SHA-256 已记录。
- 三节点同时 `ACTIVE` 时又完成一轮并发真实 API 验证，调度器按当前热工作流把两个请求分配到 A、B；B 可以正常接单。
- B 的 Blender Asset Worker 已注册为 `ONLINE`、4 个独立 CPU 槽，不占用 GPU 槽。今天不把“Worker 在线”虚报为“B 的 UV/重拓扑真实任务已验收”。
- Windows 原生 Substance/烘焙 Worker 尚未接入；这不影响 WSL2 GPU 推理，后续应作为同一物理节点的另一执行能力登记，不能伪装成第四台 GPU。

## 2. 唯一身份与地址

| 项目 | 当前值 |
|---|---|
| GPU Control node_id | `worker-3090-b` |
| 显示名 | `3090-B` |
| Windows 固定地址 | `10.3.34.14` |
| 物理 MAC | `3c:7c:3f:a5:b0:4f` |
| GPU UUID | `GPU-092a5184-5857-d196-5df2-efa9503368aa` |
| Windows | Windows 10 Pro 22H2 build 19045.6456 |
| WSL2 | Ubuntu 22.04.5 |
| WSL2 SSH | `10.3.34.14:2222` |
| ComfyUI | `http://10.3.34.14:8188` |
| Node Agent | `http://10.3.34.14:9201` |
| GPU 池 | `PRIMARY` |

物理身份以 MAC 和 GPU UUID 为准。WSL NAT 地址会变化，不得写入控制面的节点身份；Node Agent 固定上报 Windows 物理地址 `10.3.34.14`。

## 3. Windows / WSL2 网络与自恢复

Windows 侧已验证：

- `10.3.34.11:443` 连通；
- `0.0.0.0:2222` 转发到 WSL2 `22`；
- `8188/9201/9100/9400` 均转发到当前 WSL2 `eth0` 地址；
- `GPUControl-WSL-Keepalive` 持续保持 WSL2 运行；
- `GPUControl-WSL-Watchdog` 在映射正确时不执行 delete/add；
- 明确出站规则允许 WSL2 到主控 `10.3.34.11:80,443`；
- WSL2 到主控 HTTPS 返回 `200`，`gpu-node-agent` 为 `active`。

控制端验收：

- SSH 连续 `20/20` 成功；Windows 侧修复验收为 `100/100` 成功；
- `gpu-node-agent`、`ssh`、`docker`、`containerd`、`node_exporter` 均为 `active`；
- ComfyUI 与 Blender Worker 都使用 `restart: unless-stopped`；
- 节点心跳持续更新，数据库显示 `ONLINE / ACTIVE / current_jobs=0`。

## 4. GPU 运行时一致性

### 4.1 固定镜像

```text
registry.local:5000/gpu-control/comfyui:projects-0.2.3
image id: sha256:d76e54…
```

B 的 ComfyUI 容器为 `gpu-control-node-comfyui-1`，验收时 `healthy`；`/system_stats` 返回 RTX 3090、24 GB、ComfyUI 0.28.0、PyTorch 2.7.1+cu128。

### 4.2 外部业务管线只读一致性

```text
ImageClip commit:       721f7d68635ee36d45f545ce2c82037046147442
ImageClip pipeline SHA: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
ModelView commit:       c58249a29c2cc1b1e0cdeef5d26f27265ca28220
```

模型目录：

```text
/opt/imageclip/models            约 16 GB
/opt/modelviewcreator/model      约 22 GB
```

上述仓库、工作流和模型只做批准版本同步、哈希与兼容性校验；本次没有修改工作流 JSON、节点参数、模型、提示词、图拓扑或输出语义。

### 4.3 运行目录权限修复

第一次强制 B canary 在 `/upload/image` 返回 500，原因是宿主运行目录属于 uid 1000，而容器内 `comfy` 用户为 uid/gid 10001。已把 B 的 `/srv/comfyui/runtime` 对齐为 `10001:10001`，目录权限 `0775`，并用容器实际用户验证 input/output/temp 可写。没有为此重启 ComfyUI。

## 5. 真实 GPU API 验收

全部请求均通过生产入口 `https://10.3.34.11`，不是直接调用 `8188`，也没有模拟数据。

### 5.1 强制路由到 B 的 canary

强制路由只在三节点 `current_jobs=0` 时临时将另外两节点置为 DRAINING，测试退出后立即恢复 ACTIVE；没有停止或重启任何服务。

| 能力 | Job ID | 节点 | 终态 | 用时 | 最终文件 |
|---|---|---|---|---:|---|
| ImageClip RGBA | `f3762084-48a3-4916-a655-8aab3b5a1fd9` | `worker-3090-b` | `SUCCEEDED` | 144 s 冷启动 | 768×768 RGBA PNG |
| ModelView 局部重绘 | `3bac1e4c-65db-4e8d-ad7a-ac9440920d89` | `worker-3090-b` | `SUCCEEDED` | 76 s | 2048×2048 RGB PNG |

最终 SHA-256：

```text
ImageClip:  4ca3fbc1c5cf8eedc863eb7afc9af63968797a7c3901d9d8372fff41d2fdf3bf
ModelView:  3229291d773d9f4b5c12371a3fca4c6c59e6fb6c8ce9feb3f4d50c8a0d28d9d6
```

### 5.2 三节点正常 ACTIVE 调度

两项真实请求并发提交，未强制节点：

| 能力 | Job ID | 实际节点 | 终态 | 用时 | 最终 SHA-256 |
|---|---|---|---|---:|---|
| ModelView | `06146dad-69ff-473d-b9e2-b3a4cdd61535` | `worker-3090-b` | `SUCCEEDED` | 39 s | `d1903f6cb23bb02228796b4b15b4c29a379ab4a80b646b3c54af2e49d3ac3aa4` |
| ImageClip | `74042fbc-11d3-487b-b1a5-9e6954cbc620` | `worker-3090-a` | `SUCCEEDED` | 78 s | `0e4ad59a852565f012f242f1b97e62156d8dbb96bb82dc0dc12a28dd6e13e123` |

这证明 B 在正常缓存亲和调度中可以领取生产 Job，且不会把全部请求错误固定到单一节点。

## 6. Asset Worker 当前状态

三台独立 CPU Worker 当前均 `ONLINE`，Blender `5.1.2`，Skill 版本 `asset-skills-2026.07.28-v3`：

| Worker | 槽位 | CPU 报告 |
|---|---:|---:|
| `asset-control-4090` | 2 | 24 |
| `asset-worker-3090-a` | 3 | 32 |
| `asset-worker-3090-b` | 4 | 64 |

B 使用与主控相同的 `li3d/blender-worker:1.1.0`，本地 image ID 为 `sha256:c43941fb6dd4bbb68eec89eacd92c42e73d86677daa505d9980e7e4a1c0065a6`。约 688 MB 镜像通过局域网压缩流在 28.05 秒内传输并载入。B 的真实 UV 与重拓扑 canary 尚未执行，明天必须分别验证真实输入、进度/SSE、制品数量、SHA 和客户端复核链路后才可写成资产能力验收通过。

## 7. 当前三节点生产状态

```text
control-4090   ONLINE  ACTIVE  OVERFLOW  current_jobs=0
worker-3090-a  ONLINE  ACTIVE  PRIMARY   current_jobs=0
worker-3090-b  ONLINE  ACTIVE  PRIMARY   current_jobs=0
```

生产 API、Asset API、Scheduler、Web、Nginx、PostgreSQL、Redis 和监控栈均健康。为保护 GPU 任务，本轮 B 验收没有重启控制 API、Scheduler、4090/3090-A ComfyUI。

## 8. 明日继续项

1. 在 B 上分别强制执行一单真实 UV 和一单带多视角参考图的重拓扑，验证所有原子制品和 SHA；
2. 完成“用户端人工复核”决定回传的公开客户接口与权限合同；调度后台只保留只读状态、诊断和制品下载；
3. 接入 Windows 原生 Substance/烘焙 Worker，并保持与 WSL2 能力同属 `worker-3090-b` 物理身份；
4. 做 Windows 重启、WSL2 地址变化、Watchdog 恢复和主控重新握手的完整断电回归；
5. 资产任务验收通过后再决定 B 的 Asset 并发槽是否从 4 调整，不能凭 CPU 核数盲目拉高。

## 9. 源码与离线镜像保存

- 已审计源码基线：`e492779`；
- 候选镜像标签：控制面 `1.5.0-r1`、Blender Worker `1.1.0-r1`；
- 服务器归档：`/srv/gpu-control/images/unified-scheduler-1.5.0-r1-images.tar.gz`；
- 大小：`826519963` bytes；
- SHA-256：`0c68057f66f2c143f203f54b98533e1fb419a8df0f70ad7704646836b1521ccb`；
- Git LFS：`artifacts/control-plane/1.5.0-r1/`。

归档包含 API、Scheduler、Web、Asset API 和 Blender Worker，不包含 `.env`、密钥、证书私钥、
数据库、任务、模型或 ComfyUI 大镜像。构建和归档没有替换在线 API/Scheduler/ComfyUI。

## 10. 回滚

- GPU 异常：先把 `worker-3090-b` 置为 `DRAINING`，等待 `current_jobs=0`，再停止 B 的 ComfyUI；A/4090 继续服务。
- Asset 异常：只停止 B 的 Blender Worker，不影响 GPU ComfyUI 和 GPU Scheduler。
- WSL2 代理异常：修复 Windows Keepalive/Watchdog 与端口映射，不要在控制面创建新的 node_id。
- 身份不一致：MAC、GPU UUID、固定 Windows IP 任一不符时 fail closed，禁止自动把未知主机当成 B。
