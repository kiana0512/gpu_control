# AutoDL PRO 6000 实例迁移与局部重绘低延迟验收

日期：2026-09-21

## 结论

- 生产云节点已从旧实例 `pro-7894be501780` 迁移至北京 B 区实例
  `pro-78993043bdb0`，实际 GPU 为 `NVIDIA RTX PRO 6000 Blackwell Server Edition`。
- 为保留历史任务、外键和监控连续性，节点 ID 继续使用 `autodl-5090-01`；UI
  展示名、provider ref 和 GPU 型号均以新实例实测值为准。
- 云节点仅允许 `modelview-inpaint`，该工作流优先选择已运行且兼容的 AutoDL
  云节点，本地 GPU 是云节点不可接单时的回退。
- 生产 API 仍为 `modelview-inpaint / 2026.09.18-refcontrol-normal-2step-r1`：
  法线输入启用，基础 LoRA 权重 1，法线 LoRA 权重 0.8，denoise 1，steps 2，
  输出 2048×2048。迁移和网络优化没有改变工作流语义、采样参数或模型内容。
- 最近两次热态端到端实测为 11.266 秒和 11.115 秒；GPU 外开销分别为
  2.740 秒和 2.916 秒。队列开销已降到 10–16 毫秒。

## 绑定与自动更新

`ProviderInstance(app:pro-78993043bdb0)` 是显式绑定的唯一云实例。库存同步只会
更新已经绑定的节点，不会自动挑选或绑定账号中的其他实例。官方 6006 地址必须满足：

- HTTPS；
- 主机属于 `autodl.com`、`autodl.art` 或 `seetacloud.com`；
- 端口为 443 或 8443；
- 根路径、无查询参数、无嵌入账号密码。

校验通过后，API 自动更新绑定节点的 `base_url` 和
`provider_service_6006_url`，清理旧浏览器隧道端口/片段，并写入
`cloud.node.endpoint.refresh` 审计记录。未知、未绑定或 provider ref 不匹配的
实例不会修改节点。

当前生产状态：

- provider ref：`app:pro-78993043bdb0`
- 节点：`autodl-5090-01`
- 数据通道：AutoDL 官方 6006 HTTPS
- 健康状态：`ONLINE`
- 工作流白名单：`["modelview-inpaint"]`
- Codex runtime：禁用（云推理节点不要求 Codex 授权）

云服务器页和 GPU 节点页的“打开 ComfyUI”优先在新标签页打开库存中的官方
6006 地址；只有库存未提供安全官方地址时才回退旧控制面隧道。这样实例更换后不会
继续打开旧实例链接，也不会把 SSH 密码暴露到浏览器。

## 模型与工作流完整性

部署按 SHA-256 校验，主要文件如下：

| 内容 | SHA-256 |
|---|---|
| Qwen text encoder | `abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6` |
| Flux2 Klein 9B Q5 UNet | `8167105716c715be31018f682916b5b9988f9afec4e20d7ad7cd9b42aa9774ce` |
| 基础 LoRA | `80c1e864ebe62c2a5e975385e0b70d2d7c7fb679cc39cb8f458a12c46cb3343e` |
| 法线 LoRA | `baa97f297d048330850f0cb8063392678927a34282de961760693f6897983fd2` |
| 生产 API 工作流 | `9a25e0aac07525430666a2c37779066f2911c3a2ba94ec724ed418a733269a88` |
| VAE | `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5` |

完整清单见
`output/autodl-pro6000-instance-migration-20260921/staging-manifest.json`。

## 延迟定位与优化

最初 20 秒左右的主要问题不是 PRO 6000 算力，而是控制面数据路径：四张输入重复
上传/回读、SSH 输出下载以及任务完成后的单流持久化。当前实现：

1. 四张输入按租户和工作流使用远端内容寻址缓存；命中时仍在目标路径做完整
   SHA-256 校验，不重复跨网上传。
2. 提交、事件和结果走官方 HTTPS；SSH 隧道只保留给完整性缓存和故障恢复。
3. 0.5–64 MiB 的 AutoDL 输出使用 8 路 Range 下载；每段必须返回精确 206、
   Content-Range 和长度，否则自动回退单流。
4. 合并后的每一个字节重新计算 SHA-256，文件先 fsync 到私有临时路径，再原子
   replace，并 fsync 输出目录。速度优化不牺牲完整性或断电持久性。
5. 心跳资格窗口从 20 秒调至 45 秒，防止多节点探测周期导致健康云节点被误判；
   明确探测失败仍会立即置为 DEGRADED/OFFLINE。

同一 2.36 MB 结果的并发 Range 基准（每组四次）：

| 分片 | 样本（秒） | 平均 |
|---|---|---:|
| 4 | 0.458 / 1.256 / 1.866 / 1.028 | 1.152 |
| 8 | 0.839 / 0.615 / 0.578 / 0.529 | 0.640 |
| 12 | 1.486 / 1.103 / 1.324 / 0.652 | 1.141 |

因此生产采用 8 路，而不是以单次最低值拍脑袋选择并发数。

## 端到端验收数据

| 数据路径/版本 | 排队 | GPU 前 | PRO 6000 GPU | GPU 后 | API 总耗时 |
|---|---:|---:|---:|---:|---:|
| SSH 输入/输出基线 | 0.020 s | 4.290 s | 8.852 s | 14.784 s | 28.123 s |
| 官方 HTTPS、输入缓存命中 | 0.427 s | 0.365 s | 8.430 s | 10.317 s | 19.336 s |
| 官方 HTTPS、4 路 Range | 0.018 s | 1.510 s | 7.304 s | 5.117 s | 14.147 s |
| 官方 HTTPS、8 路 Range #1 | 0.016 s | 0.429 s | 8.354 s | 2.281 s | 11.266 s |
| 官方 HTTPS、8 路 Range #2 | 0.010 s | 0.710 s | 8.032 s | 2.190 s | 11.115 s |

最后两次输出分别为 2,358,942 和 2,347,498 字节，均为 2048×2048 PNG，
响应 SHA-256 与落盘文件一致，执行节点均为 `autodl-5090-01`。测试 API 客户端在
每次验收后自动恢复为禁用状态。

生产端到端时间不可能严格等于 GPU 时间：2.35 MB 输出仍需跨区传输、完整散列、
原子持久化和数据库提交。当前非 GPU 开销约 2.7–2.9 秒，其中排队已接近零；继续
删除 fsync、SHA 或原子发布只能用可靠性换时间，本次不采用。

## 镜像

| 服务 | 镜像 |
|---|---|
| API | `gpu-control-api:1.5.23.post4-autodl-endpoint-r2-20260921` |
| Web | `gpu-control-web:1.5.23.post4-autodl-direct-ui-r21-20260921` |
| Scheduler | `gpu-control-scheduler:1.5.23.post4-autodl-direct-range8-r11-20260921` |
| AutoDL tunnel | `gpu-control-autodl-tunnel:1.5.23.post4-channel-serialize-r8-20260921` |

所有镜像均带 SBOM/provenance。令牌只从服务器 secret 文件读取，未写入镜像、前端、
文档或审计内容。

## 回滚

1. 停止新任务接入并确认 `current_jobs=0`。
2. 将 Scheduler 回滚至 `...direct-range-r10-20260921` 可恢复 4 路下载。
3. 将 API/Web 回滚至上一个镜像可恢复旧端点行为；数据库绑定和历史任务不删除。
4. 只有显式确认新的 provider binding 后才允许切换实例；不自动关机、重装或清空
   任何云实例。
