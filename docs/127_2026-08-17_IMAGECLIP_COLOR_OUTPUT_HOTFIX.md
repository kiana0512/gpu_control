# ImageClip 校色输出热修记录

## 结论

旧生产版本 `2026.08.12-c39ed0b-fp8-r1` 的最终输出声明为
`SaveImage #102`。该节点直接接收 `CherryHoldoutSimple #116`，没有经过上游工作流
已经提供的 `122_ColorMatchToSource #123`，因此 API、动画批次 ZIP 和 WebUI 下载都可能
出现主体颜色与原图不一致。

新版本 `2026.08.17-c39ed0b-colorfix-r1` 的唯一最终输出改为 `SaveImage #109`，固定链路为：

```text
LoadImage #108（颜色基准）
  + CherrySelfComposite #105（RGBA 抠图）
  -> 122_ColorMatchToSource #123
  -> CherryAlphaDenoise #119
  -> SaveImage #109
```

UI 工作流末端原本是 `CherryMirrorSave #109`，它返回 Windows 镜像保存路径，不能作为
跨主机 API 产物。构建器仅把这个 UI 文件落盘外壳适配为标准 `SaveImage`，其 IMAGE 输入
仍严格取自 `CherryAlphaDenoise #119`，未修改模型、提示词、采样参数或校色参数。

## 影响范围

- `POST /api/v1/services/imageclip-rgba`：返回校色并去噪后的 RGBA PNG。
- `POST /api/v1/batches/imageclip-rgba`：每帧及最终 ZIP 使用相同最终节点。
- GPU Control WebUI：单图下载、批次归档沿用后端最终产物，无独立旧分支。
- 已用旧不可变工作流版本开始执行的批次不会在中途混用两个输出版本；新提交使用新版本。

上线时发现两个旧版动画批次仍有待执行帧。按用户要求已整体取消：已开始前的剩余
`461` 帧全部标记为 `CANCELLED`，生产队列清零；旧版已经完成的结果不复用、不伪装成
新校色版本，需由调用方重新提交后才能获得正确颜色结果。

## 四节点要求

4090、3090-A、3090-B、4070 Ti 必须同时具备：

- `122_ColorMatchToSource`
- `CherryAlphaDenoise`
- `CherrySelfComposite`
- `SaveImage`
- `Int`

其中 4090 曾因容器漏挂载 `/opt/imageclip/ComfyLiterals` 缺少 `Int`，已重建容器并恢复
ImageClip 兼容性。4070 Ti 仍可执行 ImageClip、粗糙度和其它兼容任务，但不接局部重绘。

## 实测

同一张 `768×768` 真实帧以新模板直接在四个节点执行，四个返回文件均为 8-bit RGBA PNG：

| 节点 | 完成时间 | 结果 |
|---|---:|---|
| 4090 | 4.0 s | PASS |
| 3090-B | 9.3 s | PASS |
| 3090-A | 9.4 s | PASS |
| 4070 Ti | 25.2 s | PASS |

控制面 HTTPS 单图 API canary 返回 `200 image/png`，任务
`85d53218-c785-439e-90cb-8297312d7a4e` 使用版本
`2026.08.17-c39ed0b-colorfix-r1` 并从 `SaveImage #109` 收集最终产物。

4090 的十分钟局部重绘策略同时改为“有局部重绘时优先/抢占，无局部重绘时继续接兼容
普通任务”，不再因为只有热缓存保护而空转。单卡并发仍为 1，4070 Ti 的局部重绘禁用规则不变。

生产调度器已升级为 `gpu-control-scheduler:1.5.18`。两个取消操作均写入完整审计记录，
最终批次状态为 `CANCELLED`，取消后 `QUEUED/CLAIMED/RUNNING` 数量为 `0`。

4070 Ti 完成 HAGS/WSL 重启恢复后又在真实生产批次完成三帧新版本任务，其中任务
`26437498-cdca-4f34-83c5-93756e95a134` 用时 `21.97 s`，ComfyUI history 只包含输出节点
`#109`；落盘结果为 `768×768`、8-bit RGBA PNG。这是重启后的独立验收，不复用重启前 canary。
