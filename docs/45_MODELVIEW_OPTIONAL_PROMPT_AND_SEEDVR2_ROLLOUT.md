# ModelView 局部重绘可选提示词与 SeedVR2 三节点升级方案

状态：`PRODUCTION ENABLED / THREE GPU NODES VERIFIED / REAL IMAGE PASSED`
日期：2026-07-27
生产版本：GPU Control `1.3.4`，ComfyUI `projects-0.2.3`，工作流
`modelview-inpaint:2026.07.27-8c37f07-seedvr2`。

Web 已用 `VITE_MODELVIEW_PROMPT_ENABLED=true` 构建上线，调用示例同时展示必填 `image` 和可选
`prompt`。API、工作流、镜像、插件和模型均已通过三节点验收。

## 1. 上游版本与已修问题

ModelViewCreator 原生产提交：

```text
b22bb377d200d10ae1af565494674fdfb53580dc
flux_fill_inpaint.json SHA-256:
8f3bdadede4c482c6f575db9878150728b9f21a9af81e53b45508cae875b25f1
```

业务方新工作流提交为 `501c7f232b335d50abe2e22365fe8e020b24254c`。只读检查发现 JSON 使用
`Qwen3VLPlusLiclick`，但同一仓库实际只注册 `Qwen3 VL Plus`；三台 ComfyUI 对前者均返回未注册。

已在上游 main 提交兼容修复，并在 2026-07-27 完成 SeedVR2 API 字段修复：

```text
c58249a29c2cc1b1e0cdeef5d26f27265ca28220
remote HEAD after reverting an unauthorized performance experiment
flux_fill_inpaint.json SHA-256:
eec3a66ded9290b8d7f5c2eb1cbfdeaeec7acd5d5260c08266a8430750d0eaaf
```

当前远端 HEAD 的业务实现内容与用户批准的 `8c37f07` 一致。GPU Control 只负责输入绑定、最终输出
过滤、版本校验和部署，不修改同事维护的工作流参数或自定义节点实现。

## 2. 新工作流真实输入输出

| 语义 | UI 节点 | API 绑定/输出 |
|---|---|---|
| 必填输入图片（含蒙版/Alpha） | `LoadImage #127` | `image_filename -> 127.inputs.image` |
| 可选用户提示词 | `PrimitiveNode #124` → Qwen `#94` | `prompt -> 94.inputs.prompt` |
| 自动提示词 | Qwen `#94` | prompt 为空时根据原图+蒙版图反推 |
| 局部重绘结果 | `InpaintStitchImproved #79` | 进入 SeedVR2 |
| 最终放大 | `SeedVR2VideoUpscaler #110` | 固定 2048 上限参数 |
| 唯一外部输出 | `SaveImage #9` | `output_nodes=["9"]` |

API 模板必须只保留 `SaveImage #9` 的祖先图，不得把预览、调试图或 SeedVR2 前的中间结果登记为
artifact。`PrimitiveNode #124` 是 UI 代理，转换时把值内联到 `94.inputs.prompt`，不能作为虚构 API
节点提交到 ComfyUI。

## 3. 公共 API 合同

路径保持兼容：

```http
POST /api/v1/services/modelview-inpaint
Content-Type: multipart/form-data
Idempotency-Key: <业务对象+代次+尝试>
```

字段：

- `image`：必填图片；保留工作流需要的 Alpha/蒙版；
- `prompt`：可选 UTF-8 文本，最长 4096 字符；
- `parameters`：高级兼容字段，JSON 对象；也可包含 `prompt`；
- 同时传 `prompt` 和 `parameters.prompt` 时，内容相同可接受，内容不同返回 422；
- 成功响应仍是唯一最终图片，响应头保留 `X-Job-ID` 与 `Cache-Control: no-store`。

带提示词：

```bash
curl -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: shot-010:g2:attempt-1' \
  -F 'image=@input-with-mask.png' \
  -F 'prompt=修复蒙版区域的破损边缘，保持其他区域不变' \
  --output result-inpaint.png
```

不带提示词：

```bash
curl -X POST 'https://10.3.34.11/api/v1/services/modelview-inpaint' \
  -H 'Idempotency-Key: shot-011:g1:attempt-1' \
  -F 'image=@input-with-mask.png' \
  --output result-inpaint.png
```

省略提示词时不是跳过 Qwen，而是让 Qwen 按图片自动生成提示词。输入图片或 prompt 改变时必须使用
新的 generation/Idempotency-Key；不能用旧 key 覆盖内容。

## 4. SeedVR2 固定依赖

新 JSON 记录的插件提交：

```text
repository: https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git
commit: 690cc39379c1481159ddd451368dbf2295930fc6
```

统一 ComfyUI 候选锁已增加该固定提交，并只补三项缺失依赖，禁止插件隐式升级 Torch/CUDA：

```text
antlr4-python3-runtime==4.9.3
omegaconf==2.3.1
rotary-embedding-torch==0.9.1
```

工作流选择的模型及插件登记 SHA：

```text
SEEDVR2/seedvr2_ema_7b_sharp-Q4_K_M.gguf
SHA-256: 7aed800ac4eb8e0d18569a954c0ff35f5a1caa3ed5d920e66cc31405f75b6e69

SEEDVR2/ema_vae_fp16.safetensors
SHA-256: 20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1
```

插件支持首次执行自动下载，但生产禁止让任意首单在随机节点现场下载。正式启用前必须在三台机器预取
两文件、逐机复算 SHA，并写入模型 manifest；否则首单可能超时，下一单又调度到另一台重复下载。

## 5. 当前三节点事实

三台当前完全一致：

```text
ModelView HEAD: c58249a29c2cc1b1e0cdeef5d26f27265ca28220
workflow SHA-256: eec3a66ded9290b8d7f5c2eb1cbfdeaeec7acd5d5260c08266a8430750d0eaaf
haoze-LiClick source SHA-256: c3c8563f2f6a00757e86cf671dafb2649ca25e3bb307be0538a4de33d97bdf60
SeedVR2LoadDiTModel: present
SeedVR2LoadVAEModel: present
SeedVR2VideoUpscaler: present
Qwen3 VL Plus: present
BatchImagesNode: present
ComfyUI image: registry.local:5000/gpu-control/comfyui:projects-0.2.3
node state: ONLINE / ACTIVE
```

生产 API 已启用新固定工作流；旧版本保留为 disabled，历史任务仍按各自不可变版本追溯。

## 6. 安全滚动升级门禁

1. 等 `RUNNING=0`、`QUEUED=0`，同时确认三台 ComfyUI `/queue` 为空；
2. 固定并构建新的统一 ComfyUI 镜像，执行 `pip check`、镜像扫描和 `/object_info` 缺失 0；
3. 在隔离候选容器把上游 `c7f4242` UI JSON 转成 API prompt；
4. 固定 API bindings：`127.inputs.image`、`94.inputs.prompt`、output `9`；
5. 三台预取 SeedVR2 DiT/VAE 并验证上述 SHA；
6. 先把三节点设为 DRAINING，再逐台替换镜像，每次只动一台；
7. 每台恢复前验证 Git HEAD、UI JSON SHA、镜像 ID、模型 SHA 和全部 class types；
8. 导入新不可变 WorkflowVersion，旧版本保留但停用，不覆盖历史任务；
9. 先测无 prompt，再测有 prompt，确认返回均只有 SeedVR2 后的 `SaveImage #9`；
10. 做三节点各一单、混合调度和重复幂等测试，再恢复全部接单。

第 7 步在每台节点执行同一条只读门禁命令；任意 `FAIL` 都不允许把节点从 DRAINING 改回 ACTIVE：

```bash
cd /opt/gpu-control
scripts/preflight_modelview_seedvr2.sh \
  --repo /opt/modelviewcreator \
  --model-root /opt/imageclip/models \
  --comfy-url http://127.0.0.1:8188
```

预期末行必须是 `modelview_seedvr2_preflight=PASSED`。该脚本固定验证上游提交、工作流 SHA、两份
模型 SHA，以及 `Qwen3 VL Plus` 和 SeedVR2 三个运行节点；它不会拉取 Git、下载模型或修改容器。
`/opt/imageclip/models` 是三台宿主机实际挂载到容器 `/opt/comfyui/models` 的统一模型根目录，SeedVR2
文件必须位于其 `SEEDVR2/` 子目录。

任何一步不一致，保持新 WorkflowVersion disabled，并切回原镜像/原工作流；已有 `prompt_id` 的任务不得
盲目重提。
