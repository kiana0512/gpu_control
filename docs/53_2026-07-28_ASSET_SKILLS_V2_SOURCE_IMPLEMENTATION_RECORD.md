# 2026-07-28 Asset Skills V2 源码实施与验证记录

> 真实 Blender、真实公开资产、真实 Codex CLI、真实 Web UI 验收结果见
> [54_2026-07-28_ASSET_API_REAL_ACCEPTANCE.md](54_2026-07-28_ASSET_API_REAL_ACCEPTANCE.md)。

## 状态

- 源码：已完成
- 隔离自动化测试：已通过
- WebUI 隔离渲染：已通过
- 生产部署：未执行
- 生产重启：未执行
- 数据库迁移：未执行
- 当前 GPU/ComfyUI 任务：未触碰

## 实施范围

### Asset API

- 新增 `POST /api/v1/assets/uv/process`；
- 新增 `POST /api/v1/assets/retopology/audit`；
- 增加 UV V2 五件套完成门禁；
- 增加拓扑审计完成门禁与 `WAITING_REVIEW`；
- 继续保留旧 `/api/v1/assets/uv/unwrap` 兼容入口；
- 继续复用客户 API Key/来源 IP、幂等键、租约、取消、状态和 Artifact 下载。

### Blender Worker

- UV 改为执行用户批准的 `blender-pbr-uv` Skill 脚本；
- 拓扑执行 `blender-retopology-compare-iterate` 的审计脚本；
- 每次执行前核对固定 SHA-256；
- 启用 `--factory-startup --disable-autoexec`；
- 设置 `CUDA_VISIBLE_DEVICES` 为空，与 GPU 推理槽位隔离；
- UV 运行 BLEND QA 和 FBX 回读 QA；
- 拓扑只发布审计，不生成或覆盖低模。

### 统一管理面

- 主 API 新增 `GET /admin/asset-processing`；
- Web `/asset-processing` 改为真实 Worker/Job/Artifact 数据；
- 新增 UV/拓扑筛选、进度、审核态和父任务详情抽屉；
- 子步骤与制品进入父任务详情，不在顶层任务列表刷屏；
- 删除硬编码 IP、槽位和四件套候选数据。

### Codex CLI 与 Skill

安装位置：

```text
/home/lilithgames/.codex/skills/blender-pbr-uv
/home/lilithgames/.codex/skills/blender-retopology-compare-iterate
```

预检命令：

```bash
cd /opt/gpu-control
scripts/verify_asset_skills.sh
scripts/codex_asset_runtime_preflight.sh
```

Codex CLI 只承担分析、规划、解释与结构化输出；模型文件的实际变更由固定 Hash 的 Blender Worker 完成。

## 验证结果

| 验证 | 结果 |
|---|---|
| Python Ruff（API、Asset API、Worker、Core、测试） | PASS |
| Asset API 集成测试 | 5 passed |
| Admin Asset 真实读模型测试 | 1 passed |
| Vue ESLint | PASS |
| Vue TypeScript + Vite production build | PASS |
| Vue Vitest | 3 passed |
| Skill 关键文件 SHA-256 | 6/6 PASS |
| Codex CLI 参数预检 | PASS |
| Playwright 桌面渲染 | PASS |
| Playwright 拓扑筛选和详情交互 | PASS |
| Playwright 移动视口渲染 | PASS |
| 浏览器 console/page error | 0 |

隔离 UI 证据：

```text
/tmp/gpu-control-ui-evidence/asset-ui-desktop.png
/tmp/gpu-control-ui-evidence/asset-ui-detail.png
/tmp/gpu-control-ui-evidence/asset-ui-mobile.png
```

## 已确认合同

UV 输入：FBX、OBJ、GLB/GLTF、BLEND。默认 `y+ / 75° / 2048 / 10px`。

UV 输出：

```text
<stem>_PBR_UV.blend
<stem>_PBR_UV.fbx
<stem>_PBR_UV_report.json
<stem>_PBR_UV_QA.json
<stem>_PBR_UV_FBX_QA.json
```

拓扑输入：一个 BLEND，metadata 明确 high/reference/low 对象名。输出
`retopology_audit.json + retopology_manifest.json`，状态 `WAITING_REVIEW`。

## 未完成项

- 未实现自动生成新低模的确定性 Operator；
- 未实现拓扑三行四视图渲染 Artifact；
- 未实现人工批准/拒绝写 API；
- 未执行 Blender 5.1.2 Golden Asset；
- 未构建或部署 V2 生产镜像；
- 未把 Skill 同步到其他节点；
- 未启用生产 Asset Worker。

## 安全上线顺序

必须等当前生产任务清空后执行：

1. 记录 GPU 与 Asset 队列为空；
2. 备份数据库与当前镜像指纹；
3. 在目标节点安装并验证相同 Skill Hash；
4. 构建 API、Asset API、Web、Blender Worker 镜像；
5. 先滚动更新 Worker 并保持新 Asset 提交入口未开放；
6. 更新主 API、Asset API 与 Web；
7. 主控单 Worker Canary 跑 Golden FBX；
8. 核验五件套文件名、双 QA、SHA 与原子下载；
9. 跑拓扑 BLEND，核验 `WAITING_REVIEW`；
10. 再逐台启用其他 Asset Worker。

严禁在新 Asset API 已接受 V2 任务、但 Worker 仍为旧版本的中间状态对外开放提交。
