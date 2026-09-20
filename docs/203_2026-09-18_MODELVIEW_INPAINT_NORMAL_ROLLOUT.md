# 局部重绘法线参考版上线记录

日期：2026-09-18。范围仅 `modelview-inpaint`；由用户提供最新版 JSON、新 LoRA 和
四张真实样例，授权替换。单视图生成、单视图局部重绘、其他管线及配额策略不变。

## 发布结果

新版本 `2026.09.18-refcontrol-normal-4step-r1` 已启用，旧版本
`2026.09.17-li3d4500-defaultprompt-steps2-r1` 停用但完整保留。
API 镜像：`gpu-control-api:1.5.23-refcontrol-normal-20260918`。
该镜像直接继承当前生产镜像，仅覆盖 API 的 main.py，不引入工作区其他未发布改动。

| 节点 | 同步和验收 | 本次直接提交至取回图片耗时 |
| --- | --- | --- |
| control-4090 | LoRA/UI 哈希一致、真实生成通过、ACTIVE/ONLINE | 35.28 秒 |
| worker-3090-a | LoRA/UI 哈希一致、真实生成通过、ACTIVE/ONLINE | 66.07 秒 |
| worker-3090-b | LoRA/UI 哈希一致、真实生成通过、ACTIVE/ONLINE | 85.37 秒 |
| worker-5070ti-01 | OFFLINE，SSH 两次超时，未同步、未验收 | 未测试 |

耗时只表示此次样例及缓存/机器状态，不是性能承诺。5070 Ti 新版兼容性为 false，旧
工作流的 16GB 验收档案未沿用到新模板；恢复在线后仍不会误接新任务。12GB 4070 Ti
也保持不兼容。不得为绕过待验收状态降低全局 24000 MiB 显存门槛。

## 工作流差异与一致性

- `image` 从 LoadImage #4 改绑 #73，参考 #5、蒙版 #44、默认提示词 #60 保留。
- 新增 `normal_image` → LoadImage #79 → VAEEncode #77 → ReferenceLatent #74。
- #78 增加 `flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors`，强度 0.8，串接 #21。
- 严格采用上传 JSON 的实际参数：原 LoRA #21 强度由 0.9 到 1.0、采样由 2 步到 4 步。
- API 使用最终 #29：只将 UI PreviewImage 适配为 SaveImage，保持其输入 #33:1；
  不返回 #62 中间预览，最终画布仍固定 2048×2048。
- 内置 973 字符英文提示词不变。服务端继续新任务随机 seed、重试复用 seed。

原始 UI SHA-256：`20c0e6a15cdf5529eb547f6f14ead3ddc0a2bb45c91692ce23e5c356425cbdab`。
数据库 template_digest：`08a54d3f53e804749aab4044a26023342e82ea0138b78f2b2e7f11b7b1046685`。
转换器 ensure_ascii=False 摘要为 `74a191df39d96fd4b5e001f616a833bf3592502379082af5c52a38d0ebabf04c`；
二者序列化方式不同，不应混用为发布校验值。
新增 LoRA 165704512 字节，SHA-256：
`baa97f297d048330850f0cb8063392678927a34282de961760693f6897983fd2`。

LoRA 主机路径 `/opt/modelviewcreator/model/lora/flux-kelin/flux2_klein_9b_refcontrol_normal.safetensors`。
UI 主机路径 `/opt/modelviewcreator/Flux2 Klein TrueV3-双图材质编辑-局部重绘.json`；
容器挂载 `/opt/comfyui/user/default/workflows/ModelViewCreator_flux_fill_inpaint.json`。
三台均核对容器内 UI 哈希，不仅检查主机文件。保留挂载文件 inode，未重启 ComfyUI/GPU。

## API、测试与上线安全

`POST /api/v1/services/modelview-inpaint` 新增必填 `normal_image` 文件字段。
法线和蒙版尺寸必须与 image 相同。法线字节参与幂等指纹，四个文件统一隔离上传。
若 API 与当前启用模板不匹配，不会静默丢弃法线，而返回契约错误。
其余两个 ModelView API 不要求 normal_image，OpenAPI 已实查。

隔离容器回归：`tests/integration/test_api.py` + `tests/unit/test_modelview_truev3_bundle.py`，
共 **57 passed**。覆盖新增法线缺失/尺寸、节点绑定、上传持久化、随机 seed、同 key 重试、
法线变化产生幂等冲突及单视图旧契约。静态未定义变量/未用导入检查通过。

三个节点分别先 DRAINING，等待生产 Job、资产任务和 ComfyUI 队列均空后执行实际生成；
完成后恢复 ACTIVE。新模板先导入禁用版本，三机成功才发布。同步可见 UI 时再次排空。
API 通过临时候选接流量、确认旧连接归零、更新规范容器、恢复网关路由的方式发布，
未中断已有推理或修改调度器。临时候选已停止保留，正式容器健康。

正式 HTTPS 端到端任务：`56268552-d449-4fdb-9a4b-3cee07cc08e5`，执行 control-4090。
返回 HTTP 200/image/png，上传文件 4 个、输出 1 张 2048×2048；随机 seed
`301440841720854` 已保存任务快照，法线连线已核对。相同幂等键重放返回同一任务及相同字节。
输出 SHA-256：`9b4be8c848c6e60a1521a2271c09f55b425e8d43135497b6a63b78c09ee8574e`。
视觉检查确认示例白色占位区域已被生成内容替换；不等于任意素材的艺术验收或像素保护保证。

完整验收证据：`/srv/gpu-control/jobs/deployment-modelview-normal-20260918/`：
`verification.json`、每节点 PNG/JSON、`release.json`、`service-acceptance.json`。
脚本归档：`scripts/modelview_normal_rollout_20260918/`。这些是此次发布脚本，含固定版本、
任务与路径保护，不应未经审核直接复用于未来版本。

## 回退与离线节点补部署

本地备份：`backups/modelview-normal-20260918/`；包含旧 API、工作流、测试、模型清单、UI 和
受限权限的 compose.env。远端 UI 备份：`/tmp/modelview-inpaint-before-normal-20260918.json`。
回退需一起恢复旧 API 镜像和旧启用工作流，避免三图/四图契约错配；可见 UI 也恢复旧副本。
不要删除新旧 LoRA、历史任务、工作流快照。此轮未提交/推送 Git，模型未进入 Git/LFS。

5070 Ti 恢复后：复制新 LoRA/UI 并校验哈希；排空节点；用同一真实四图跑新版；确认显存、
单图输出和连线后，更新仅该节点且绑定新版本、模板哈希和硬件的资源验收档案，重新计算
兼容性后再接任务。不能只把它手工标成 compatible。

前端文档：[新增法线四图输入](202_2026-09-18_MODELVIEW_INPAINT_NORMAL_FRONTEND.md)。
下载副本：`/home/lilithgames/下载/局部重绘-新增法线四图输入-前端对接-20260918.md`。
