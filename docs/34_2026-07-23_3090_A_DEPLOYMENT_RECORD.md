# 2026-07-23 RTX 3090-A 部署与真实任务验收记录

## 结果

3090-A 已完成 Docker、NVIDIA Container Toolkit、统一 ComfyUI 镜像、9 个模型、两个
真实 Git 项目、Node Agent、监控、防火墙、动态心跳和主控调度接入。当前为唯一接单节点；
4090 保持 `RESERVED`。

| 项目 | 验收值 |
|---|---|
| Node ID | `worker-3090-a` |
| SSH 用户 / 主机名 | `lilithgames` / `lilithgames1` |
| 当前地址 | `10.3.34.13` |
| 网卡 / MAC | `enp5s0` / `18:c0:4d:9f:13:13` |
| GPU UUID | `GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c` |
| GPU | RTX 3090，24 GiB，驱动 `580.159.03` |
| 模式 / 健康 | `ACTIVE / ONLINE` |
| ComfyUI / Agent | `http://10.3.34.13:8188` / `http://10.3.34.13:9201` |
| 镜像 | `registry.local:5000/gpu-control/comfyui:projects-0.2.2` |
| Image ID | `sha256:bb8c76cfb0bf18c1caff7cfe2a758a9ec1e049543180f117d75af2e94d73a325` |

## 文件与运行时一致性

- ImageClip HEAD：`bb243808a6bd43055ad92c1071b2ea949b1d9ea1`。
- ModelViewCreator HEAD：`b22bb377d200d10ae1af565494674fdfb53580dc`。
- 两个目录均保留完整 Git 元数据并通过 `git fsck`；ImageClip 的现场修改状态与 4090 一致，
  ModelViewCreator 工作树干净。
- ImageClip 4 个模型、ModelViewCreator 5 个模型，共 9 个文件全部通过 SHA-256 校验。
- ComfyUI、node-exporter、DCGM exporter、Alloy 均运行；ComfyUI 为 `healthy`。
- 从 4090 访问 8188、9100、9201、9400 全部返回 200；UFW 仅允许 `10.3.34.11`
  访问这四个业务端口，SSH 仅允许 `10.3.34.0/24`。

## 长期在线与地址漂移处理

节点代理使用 systemd 开机启动，每 10 秒通过主控内网 CA 验证的 HTTPS 发出 HMAC 心跳。
主控校验时间戳、nonce、防重放签名、来源 IP、Node ID、MAC 和 GPU UUID 后，动态更新
ComfyUI 与 Agent 地址。调度器反向访问 Agent `/v1/identity` 再做一次 HMAC 身份核验。
Prometheus 的 worker 目标从数据库 HTTP 服务发现，不再写死工作节点 IP。

本次断电后 Docker 与 systemd 服务均自动恢复。A 的 DHCP 地址曾变化，最终由上述身份字段
确认当前节点仍为同一台设备。主控 Nginx 也已改为持续解析 Docker DNS，避免 API/Web
容器单独重建后因缓存旧容器 IP 返回 502。

## 真实公共 API 验收

| 字段 | 值 |
|---|---|
| HTTP | `200` |
| Job ID | `ab5fb745-cfa7-4b02-a8f2-03e1a0372d50` |
| Request ID | `d5baed4f780662ab2b99c494552df854` |
| Trace ID | `f65375caeb88449286c3355e80ba54b1` |
| Node / Prompt ID | `worker-3090-a` / `97bf15da-4e20-4f9e-990c-8e0db364da23` |
| 状态 / 尝试 | `SUCCEEDED / 1` |
| 时间 | 创建 `10:02:39.792566Z`，完成 `10:02:52.352441Z` |
| 输出 | 512×512、8-bit RGBA PNG、174659 bytes |
| 输出 SHA-256 | `ff6860547dc0ae97c117afa678f89abeb7a29d0d16bbcad2c59c9910579c5550` |

验收中发现直接图片服务把带随机 Job UUID 的内部上传路径计入请求指纹，导致同图同幂等键
重试返回 409。现已改为仅用调用者参数与文件 SHA 计算指纹，并增加图片幂等回归测试。
生产复验使用同一图片和同一幂等键连续调用两次：两次均 HTTP 200、均返回 Job
`638717ab-b018-4a7b-b743-3b6a2bf55fe3`，输出 SHA 均为
`4f013218276fadb1fee6d813b57dddf195ef3f19f14387b1423fe110954a03a2`；数据库只有一次
执行尝试，节点仍为 A。

## Web 与主控修复

- GPU 节点页不再把所有非 4090 节点硬编码为“尚未接入”；“打开 ComfyUI”使用数据库
  返回的动态 `base_url`，因此 IP 更新后按钮也自动跟随。
- Web 生产构建通过（2046 modules transformed）。
- API、Scheduler、Web、Nginx、Prometheus 均 healthy；数据库和 Redis ready。
- 3090-B 在真正接入前保持 `DISABLED / OFFLINE`，不假在线、不接单。

## 3090-B 完成与后续记录

3090-B 已使用独立 `worker-3090-b`、HMAC 密钥、MAC 和 GPU UUID 完成复制与真实任务；
三卡 10 客户轻量并发也已通过。B 的完整结果、A 上 OOM/重试修复、动态热缓存和 GPU 指标
修复见 `docs/35_2026-07-23_3090_B_AND_THREE_NODE_ACCEPTANCE.md`。
