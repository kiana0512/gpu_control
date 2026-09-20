# 单视图两套工作流：法线参考版发布记录

2026-09-18，按用户批准分别替换单视图生成和单视图局部重绘。普通局部重绘不在本次修改范围。

## 批准的源文件与版本

| 工作流 | 用户下载文件 | UI SHA-256 | 发布版本 |
| --- | --- | --- | --- |
| 单视图生成 | `ModelViewCreator_flux_Single-view Generation (1).json` | `a05adefe9124848b8e294b3acb2a4562eeec8c8b3e0838e71572280e231c2123` | `2026.09.18-refcontrol-normal-single-view-4step-r1` |
| 单视图局部重绘 | `ModelViewCreator_flux_fill_inpaint -View generation (2).json` | `1b078109ac3342c00dd2ae013561084f680439fb92e56ba001e4c591d9fb5fa6` | `2026.09.18-refcontrol-normal-single-view-inpaint-2step-r1` |

源文件保留，原样同步到四节点 `/opt/modelviewcreator/` 下不带数字后缀的正式工作流文件。
UI 的最终 PreviewImage 29 在 API 转换时仅适配成 SaveImage 29，仍从 `["33",1]` 取图。
不修改用户的 LoRA 强度、提示词、步数、法线处理或其它图连接。

API 模板标准化摘要分别为：

- 单视图：`6b5543d043b2920add573cf2015379dfb621bc1a238a17d2ad037b64b8800805`。
- 单视图局部重绘：`2022768e4f2f604a5e5a385730a3406f2713ee0a8db060d0e788d0a0aca632d8`。

两接口新增必填 multipart `normal_image`，要求与当前图尺寸相同。
单视图三个图片字段，单视图局部重绘四个图片字段；补充 prompt 仍是追加语义。
原 `modelview-inpaint` 版本和摘要保持 `2026.09.18-refcontrol-normal-2step-r1` /
`65b0c6d0a3445d8318c411fd421b1721e33991494bd5b63d770bbcf29b67da7f`。

## 四节点真实生成验收

使用用户提供的填白效果图、参考图、外扩蒙版、法线图，测试 seed 固定为 4500。
每个节点先排空控制中心任务、资产任务及 ComfyUI 队列，再提交测试；完成后恢复 ACTIVE。

| 节点 | 单视图 4 步 prompt_id / 秒 | 单视图局部重绘 2 步 prompt_id / 秒 |
| --- | --- | --- |
| control-4090 | `4803ee06-0b0f-4989-b2e1-2eca4b4405b4` / 35.29 | `307e9cc9-5e9c-44dc-bc1b-5d1d4adc8742` / 20.29 |
| worker-3090-a | `e672a29a-da6c-4e6b-9f65-ccb4c6b94dff` / 60.28 | `68afffd0-7152-4ef3-8d9d-5a87670f7ba2` / 35.34 |
| worker-3090-b | `23cce6fd-0871-4cc6-9eb5-8cfdce8c5465` / 130.65 | `ccafeb65-bf2f-48fe-bb66-c7057797075e` / 40.74 |
| worker-5070ti-01 | `ff13999b-6d04-49c9-887a-980d476f6ba0` / 100.25 | `32f0d210-f7c4-4ea5-9e12-b1cfc8762259` / 65.22 |

8 次均成功返回最终单张 2048×2048 PNG，输入及输出 SHA 保存在证据中。
上述时间为单次提交至取回结果，包含可能的模型切换和加载，不是纯采样性能或延时保证。

四台宿主机及运行容器可见 UI 哈希与源文件一致，未重启 ComfyUI。
旧 UI 备份为同目录 `<原文件名>.before-single-normal-20260918`，原位复制保持 bind mount inode。
新 LoRA SHA 为 `baa97f297d048330850f0cb8063392678927a34282de961760693f6897983fd2`，原 li3d4500 SHA 为
`80c1e864ebe62c2a5e975385e0b70d2d7c7fb679cc39cb8f458a12c46cb3343e`。

## 路由与生产保护

两个新版本在同一数据库事务中启用，旧版记录保留，不删除已有任务或模型。
新版本要求 `modelview_normal_single_flows_20260918=validated`，仅四台验收节点拥有该标签。
未验收的新云节点及 4070 Ti 不会自动承接这两个版本。

全局 min_vram_mb 仍为 24000。5070 Ti 在两套新版本真实生成通过后，分别更新绑定
版本、模板哈希、GPU UUID、证据哈希和实际运行配置摘要的 16GB 验收档案；普通局部重绘档案不变。
5070 Ti 运行镜像与 lowvram/cpu-vae 等参数未变。后续修改配置必须重新验收，当前摘要并不能自动防止运行配置漂移。

## API 与验证状态

源码修改仅涉及两个接口的必填法线上传、转发，以及 ModelView 家族统一法线尺寸检查。
新增/更新两套 manifest、API 模板、单元测试与相关接口集成测试。
针对两套工作流的图连接、必填输入、参数注入、蒙版/法线尺寸、幂等与随机 seed 的测试：
`14 passed, 47 deselected`。同时在最小改动候选和并行构建的新 API 源码上通过。

现场另一项 AutoDL API 发布同时进行，新构建
`gpu-control-api:1.5.23.post3-autodl-lifecycle-20260918` 已包含本次法线接口修改。
为避免覆盖其他发布，不改 `.env` 中 API_IMAGE_TAG，不用旧基线候选回滚新服务。
现场最终 API 已切换至上述镜像，安装模块 SHA 为
`e22fcab6255724beff9b3401d9aa5beee57b8f74e4801c697fbec37e21af385d`，与当前仓库模块一致；
API 与 Scheduler 均 healthy。本次没有启动第二个生产 API/云控制器。

两个正式 HTTPS 接口（可信 CA，真实图片）验证均成功：

- 单视图：job `4c00d21a-9a0b-4978-b324-1d43990f8c51`，4090，32.21 秒，seed `1102724693290796`。
- 单视图局部重绘：job `654e6df9-c897-40aa-8af5-b95c1fbb23f6`，3090-B，69.38 秒，seed `43137831445571`。
- 均使用新版本、新法线文件绑定，返回 2048×2048 PNG；返回 SHA 与实际响应字节匹配。
- 同一请求幂等重试返回同一 job_id 和完全相同结果，不重复生成。
- 缺少 `normal_image` 均返回 422，未创建任务。

排空期间到达的旧版任务 `589f00f9-695d-407d-abd9-0f498399ffa5` 被保留；发现调度器仅领取
enabled 版本后，临时恢复旧版 enabled，让该任务按原图、原输入、原版本在 4090 成功完成，
随后再次关闭旧版。新业务入口按创建时间选择新版本，未回滚新接口。
此补偿有审计日志及 `legacy-drain.json`；后续发布应在停用旧版前同时检查无节点归属的排队任务。
最终四台节点 ACTIVE/ONLINE，检查时无未完成任务，两个旧版均已停用。

证据目录：`/srv/gpu-control/jobs/deployment-modelview-single-normal-20260918/`。
发布脚本归档：`scripts/modelview_single_normal_20260918/`。脚本依赖当时的已批准版本与现场配置，
保留作审计，不应在后续版本上直接重跑。
无 Git 推送，无新增 Git LFS，大模型和测试 PNG 不进入仓库。

## 前端交接

- [单视图生成：三图输入](207_2026-09-18_SINGLE_VIEW_NORMAL_FRONTEND.md)。
- [单视图局部重绘：四图输入](208_2026-09-18_SINGLE_VIEW_INPAINT_NORMAL_FRONTEND.md)。

回滚需同步回滚两接口输入契约、旧 workflow enabled 状态、四节点 UI 和 5070 对应版本验收档案；
不能只恢复 UI，也不能覆盖整个 API 镜像导致丢失其它并行发布。
