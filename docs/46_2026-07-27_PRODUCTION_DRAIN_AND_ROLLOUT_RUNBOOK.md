# 生产任务排空后的统一升级执行单

状态：`MODELVIEW COMPLETE / THREE NODES ACTIVE / BLENDER CONTROL PLANE PAUSED`
日期：2026-07-27
适用范围：4090、3090-A、3090-B、ModelView 局部重绘、SeedVR2、Blender PBR UV Asset API。

执行结果：ModelView 可选 prompt、SeedVR2、ComfyUI `projects-0.2.3` 和 GPU Control `1.3.4`
已经完成滚动发布与真实图片验收；Blender Asset API、migration 和常驻 Worker 仍未启用。

## 1. 维护窗口前的历史限制

当前只允许修改仓库候选源码、文档和独立静态 Web。禁止：

- 重启或重建 API、Scheduler、PostgreSQL、Redis、Nginx；
- 重启三台 ComfyUI，或在它们的运行工作树执行 `git pull`；
- 覆盖已启用 WorkflowVersion；
- 让 SeedVR2 在真实首单中临时下载模型；
- 启动 Asset API、执行 migration `20260727_0006` 或启动 Blender 常驻 Worker。

2026-07-27 15:51 只读快照为 `production RUNNING=3、QUEUED=17`。Web-only `.2` 已上线，后端和
4090 ComfyUI 容器 ID 均未变化。

## 2. 已完成的无中断热更新

- 产品名统一为“统一调度中心”，节点页改为“计算节点”；
- 调度页展示真实三节点、客户公平、生产任务优先、租约和失败恢复，不再展示旧单机逻辑；
- `/asset-processing` 展示 Blender 候选能力，但明确后端未启用；
- ModelView `prompt` 示例由 `VITE_MODELVIEW_PROMPT_ENABLED` 保护；完成三节点验收后已切为 `true`；
- Web 镜像为 `gpu-control-web:1.4.0-dev.20260727.2`，image ID
  `sha256:a479541fc98ffcbdde0c0bdc897b674dc71ab97b047e3ac263f75195fd60e3f3`；
- Web lint 0、Vitest 3/3、生产构建通过；Playwright 桌面和移动端检查通过。

## 3. 开始维护窗口的硬门禁

同时满足以下条件才进入下一阶段：

1. 数据库所有生产 job 的活动状态计数为 0；
2. 所有父批次均处于终态，不能只有顶层页面看起来空闲；
3. 三台 ComfyUI `/queue` 的 running、pending 均为空；
4. 三台节点心跳仍 ONLINE，4090/3090-A/3090-B 身份与固定 MAC 对应；
5. 记录 API、Scheduler、三台 ComfyUI 当前 image ID 和数据库备份；
6. 保留旧 WorkflowVersion 和 `projects-0.2.2` 镜像作为回滚点。

任一条件不成立，维护窗口自动取消，不做“先改一半再等待”。

## 4. ModelView 与 SeedVR2 发布顺序

1. 固定 ModelViewCreator 当前远端提交
   `c58249a29c2cc1b1e0cdeef5d26f27265ca28220`，工作流 SHA-256
   `eec3a66ded9290b8d7f5c2eb1cbfdeaeec7acd5d5260c08266a8430750d0eaaf`；
2. 构建包含固定 SeedVR2 插件提交 `690cc393...` 的新 ComfyUI 镜像；
3. 在隔离容器运行 `pip check`、`/object_info` 和 UI→API 转换；
4. API prompt 只绑定 `127.inputs.image` 与 `94.inputs.prompt`，唯一输出为 `SaveImage #9`；
5. 三台宿主机预取 DiT/VAE 到 `/opt/imageclip/models/SEEDVR2/` 并验证固定 SHA；
6. 三节点全部改为 DRAINING 后逐台替换，每台恢复前执行
   `scripts/preflight_modelview_seedvr2.sh`；
7. 三台预检全部 PASS 后导入新的不可变 WorkflowVersion，旧版本只停用、不删除；
8. 发布支持 multipart 可选 `prompt` 的 API；
9. 先做无 prompt 单图，再做有 prompt 单图，再做三节点混合调度；
10. 确认响应只有 SeedVR2 后的最终图片、`X-Job-ID` 可追踪、幂等冲突正确；
11. 最后用 `VITE_MODELVIEW_PROMPT_ENABLED=true` 构建 Web，才对用户展示新调用示例。

以上 1–11 已完成。三节点最终预检均为 `PASSED`，真实有 prompt、无 prompt 与 4090 请求均返回
最终 2048×1152 PNG。Blender 第 6 节仍保持暂停状态。

SeedVR2 的“首次自动下载”只作为开发兜底。生产验收时日志不得出现模型下载，首单只允许模型加载和
推理耗时。

## 5. ModelView 外部合同

路径保持 `POST /api/v1/services/modelview-inpaint`：

- `image` 必填；
- `prompt` 可选，最长 4096 字符；
- 未传 prompt 时由工作流根据图片自动反推；
- `parameters.prompt` 与表单 prompt 同时存在且不相同返回 422；
- 图片或 prompt 改变必须使用新的 generation/Idempotency-Key；
- 成功只返回一张最终图片，不返回 Qwen、重绘拼接或 SeedVR2 前的中间图。

完整调用示例、提交哈希和模型 SHA 见
`45_MODELVIEW_OPTIONAL_PROMPT_AND_SEEDVR2_ROLLOUT.md`。

## 6. Blender UV Asset API 发布顺序

Blender 与 GPU Job 分表、分队列、分租约，先启 Asset API、后启单个 3090-B CPU 槽：

1. 备份数据库并校验 migration SQL；
2. 生成独立 HMAC secret，不复用 GPU node token；
3. 只启动 Asset API，回归 GPU API/Scheduler；
4. 3090-B 以 1 槽执行非生产模型并校验四件套；
5. 再启 3090-A，最后评估是否启用 4090 CPU Worker；
6. 逐阶增加 CPU 并发，GPU 推理 p95 或控制面延迟恶化立即降档；
7. 只有 `SUCCEEDED`、四个固定 artifact 齐全、逐件 SHA 正确才允许业务继续。

完整异步 cURL、Python 轮询、取消、下载和 SHA 校验见
`43_BLENDER_PBR_UV_ASSET_API_CONTRACT_V1.md`。

## 7. 回滚判定

以下任一情况立即停止新任务并回滚，不带病继续：节点 class type 缺失、三节点任一 SHA 不一致、模型
在首单现场下载、输出 artifact 不唯一、旧任务查询回归失败、任务被重复领取、GPU 推理延迟明显退化、
Asset Worker 占用 GPU 或控制面不健康。

回滚只切回旧镜像和旧 WorkflowVersion；不删除任务、输入输出、数据库记录、模型、Docker volume 或
审计日志。失败的新任务保留原 job ID 和尝试记录用于定位。
