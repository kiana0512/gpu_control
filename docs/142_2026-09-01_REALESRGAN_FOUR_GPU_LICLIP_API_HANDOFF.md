# Real-ESRGAN 四 GPU 部署与 LiClip API 交接

交付时间：2026-09-01（Asia/Singapore）
主控入口：`https://10.3.34.11`
WebUI：`https://10.3.34.11/realesrgan`
状态：已部署、四节点 READY、生产 API 已验收

## 1. 交付结论

- 4090 主机承担唯一外部入口、认证、幂等、队列和四节点统一调度。
- 4090、3090-A、3090-B、4070 Ti 均运行同一份 CUDA/PyTorch/Real-ESRGAN 不可变 Worker 镜像。
- 每台机器只有一个 Real-ESRGAN 模型进程、容量为 1；生产总容量为 4，统一队列上限为 64。
- 禁止 CPU 回退。节点只有在 CUDA、模型 SHA-256 和 RGBA 探针全部通过后才进入 READY。
- 模型内部执行 x4 恢复，但逐 tile 立即回采样；最终输出宽高与输入一致。
- 输出固定为 8-bit RGBA；Alpha 逐像素复制；RGB 按 `round(strength * restored + (1-strength) * source)` 混合。
- 每次推理前后都会执行 CUDA 同步、删除中间张量、Python GC 和 `torch.cuda.empty_cache()`；OOM 会清理后重试一次，第二次失败才摘除节点。
- 幂等结果保留 600 秒，后台自动清理过期磁盘缓存；任务历史独立保留最近 200 条。
- WebUI 已增加“AI 高清化”菜单、四节点状态、显存回收指标、LiClip API 示例以及与调度器每 10 秒同步的任务列表。

## 2. 运行环境与制品

所有节点使用以下锁定环境：

| 项目 | 值 |
|---|---|
| Python | `3.11.13` |
| PyTorch | `2.7.1+cu128` |
| CUDA runtime | `12.8` |
| 精度 | FP16 |
| 模型 | `RealESRGAN_x4plus_anime_6B`，6-block RRDBNet |
| tile / pad | `256 / 16` |
| Worker image | `registry.local:5000/gpu-control/realesrgan-worker:1.0.0` |
| Worker image ID | `sha256:3c5010a6c51750ee19bb472309424abafd41fc6c05cd35995835e4a7bfcf3026` |
| Controller image | `gpu-control-realesrgan-controller:1.0.0` |
| Controller image ID | `sha256:521259a8eb24607ff81aa70d345107eaf342cf91f94697b9d7691795dbf89fb3` |
| Web image | `gpu-control-web:1.5.23-realesrgan-v1` |
| Web image ID | `sha256:1c71510d70dc27d872b7a035bcd8178cd65dbf4f01542e91b227151fa499c263` |
| 模型 SHA-256 | `f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da` |
| 离线镜像包 | `/srv/gpu-control/images/realesrgan-worker-1.0.0.tar.zst` |
| 离线包 SHA-256 | `bf1eb768cd339c8be1f45e93b6fbbcf5746ed7945005fd52b5b1e6e8fa8df793` |

内部 Registry 当前不可解析，因此本次通过带 SHA-256 校验的离线镜像包向三台下属设备分发。四台设备上的 Worker image ID 已逐台核对一致。

## 3. 四节点状态与显存验收

同一张 `384 × 512` RGBA 图片、`strength=0.7` 被分别强制调度到四台设备，结果如下：

| 调度节点 | GPU | 推理耗时 | 单请求额外显存峰值 | 尺寸 | Alpha 差异 |
|---|---|---:|---:|---|---:|
| 4090 | NVIDIA GeForce RTX 4090 | 158 ms | 625 MiB | 原尺寸 | 0 像素 |
| 3090-A | NVIDIA GeForce RTX 3090 | 245 ms | 625 MiB | 原尺寸 | 0 像素 |
| 3090-B | NVIDIA GeForce RTX 3090 | 547 ms | 625 MiB | 原尺寸 | 0 像素 |
| 4070 Ti | NVIDIA GeForce RTX 4070 Ti | 592 ms | 625 MiB | 原尺寸 | 0 像素 |

625 MiB 远低于 4 GiB 验收上限。500 帧长稳测试结束后：

| 节点 | 当前空闲显存 | 最近请求回收后空闲 | 最近额外峰值 |
|---|---:|---:|---:|
| 4090 | 6895 MiB | 6887 MiB | 625 MiB |
| 3090-A | 5561 MiB | 5561 MiB | 625 MiB |
| 3090-B | 23250 MiB | 23250 MiB | 625 MiB |
| 4070 Ti | 10996 MiB | 10996 MiB | 625 MiB |

空闲显存会同时受同机已有任务影响，所以观察重点是本服务的额外峰值和请求结束后的回收，而不是要求四台绝对空闲值相同。

## 4. LiClip 生产 API

### 4.1 入口

| 功能 | 方法与 URL |
|---|---|
| 单帧高清化 | `POST https://10.3.34.11/api/v1/realesrgan/enhance?strength=0.7` |
| Ready | `GET https://10.3.34.11/api/v1/realesrgan/ready` |
| 容量与节点详情 | `GET https://10.3.34.11/api/v1/realesrgan/capacity` |
| 下载 LAN CA | `GET https://10.3.34.11/GPU_CONTROL_LAN_CA.crt` |

请求体是原始 PNG 字节；成功响应体也是原始 PNG 字节，不使用 multipart、JSON 或 Base64。

### 4.2 API Key

已创建独立生产身份 `liclip-realesrgan`。明文密钥只保存一次，文件权限为 `0600`：

```bash
export GPU_CONTROL_API_KEY="$(jq -r .api_key /srv/gpu-control/secrets/liclip-realesrgan-api-key.json)"
```

不要把密钥提交到 Git、写进日志或放入 URL。LiClip 应通过自己的 Secret Manager 注入 `GPU_CONTROL_API_KEY`。

### 4.3 请求合同

查询参数：

- `strength`：可选，浮点数 `0.0–1.0`，默认 `1.0`；越界或非数字返回 422。
- `0`：输出 RGBA 像素与输入一致，不占 GPU。
- `0.7`：推荐默认值。
- `1`：完整使用模型恢复后的 RGB。

必填请求头：

| Header | 说明 |
|---|---|
| `X-API-Key` | `gpc_...` 生产密钥 |
| `X-Request-ID` | LiClip 单帧追踪 ID；响应原样返回；未传时网关生成 |
| `Idempotency-Key` | 同一帧所有重试保持不变，最长 192 字符 |
| `X-Input-SHA256` | 请求体 PNG 字节的十六进制 SHA-256 |
| `Content-Type` | 必须为 `image/png` |
| `Accept` | 建议为 `image/png` |

输入限制：单张不超过 32 MiB、不超过 16,777,216 像素、只接受单帧 RGB/RGBA PNG。

推荐幂等键：

```text
sha256("RealESRGAN_x4plus_anime_6B" + NUL + normalized_strength + NUL + input_sha256)
```

同一 Key、同一输入和同一 strength 会返回同一输出且不再执行 GPU；同一 Key 改输入或 strength 返回 409。

### 4.4 curl 可直接调用的例子

```bash
curl -fsS http://10.3.34.11/GPU_CONTROL_LAN_CA.crt -o GPU_CONTROL_LAN_CA.crt

export GPU_CONTROL_API_KEY="$(jq -r .api_key /srv/gpu-control/secrets/liclip-realesrgan-api-key.json)"
INPUT=rgba-input.png
STRENGTH=0.7
INPUT_SHA=$(sha256sum "$INPUT" | awk '{print $1}')
IDEM=$(printf 'RealESRGAN_x4plus_anime_6B\0%s\0%s' "$STRENGTH" "$INPUT_SHA" \
  | sha256sum | awk '{print $1}')

curl --fail-with-body \
  --cacert GPU_CONTROL_LAN_CA.crt \
  -X POST "https://10.3.34.11/api/v1/realesrgan/enhance?strength=$STRENGTH" \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" \
  -H "X-Request-ID: liclip-esrgan-frame-000001" \
  -H "Idempotency-Key: $IDEM" \
  -H "X-Input-SHA256: $INPUT_SHA" \
  -H 'Content-Type: image/png' \
  -H 'Accept: image/png' \
  --data-binary "@$INPUT" \
  --output rgba-output.png \
  --dump-header rgba-output.headers
```

### 4.5 成功响应头

| Header | 说明 |
|---|---|
| `X-Request-ID` | 请求追踪 ID |
| `X-Compute-Node` | 实际执行的 `4090` / `3090-A` / `3090-B` / `4070 Ti` |
| `X-Model` | 锁定模型名 |
| `X-Queue-Ms` | 在 4090 主控队列中的等待时间 |
| `X-Processing-Ms` | Worker 推理时间 |
| `X-Input-SHA256` | 已校验的输入摘要 |
| `X-Output-SHA256` | 必须与响应体重新计算的摘要一致 |
| `X-Idempotency-Cache` | `MISS`、`HIT` 或 `COALESCED` |
| `X-Peak-Additional-VRAM-MB` | 本请求 GPU 额外显存峰值 |
| `X-VRAM-Free-After-MB` | 请求清理后的空闲显存 |

`X-Target-Node` 仅供运维验收强制指定节点，正常 LiClip 调用不要传，由 4090 统一选择节点。

## 5. LiClip Python 对接参考

以下函数会在返回给下一处理阶段前验证媒体类型、输入/输出 SHA-256、尺寸和 Alpha。网络重试必须复用同一个幂等键。

```python
from __future__ import annotations

import hashlib
import io
import os
import time

import requests
from PIL import Image

URL = "https://10.3.34.11/api/v1/realesrgan/enhance"
CA_FILE = "GPU_CONTROL_LAN_CA.crt"
MODEL = "RealESRGAN_x4plus_anime_6B"


def normalized_strength(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".") or "0"


def enhance_frame(
    session: requests.Session,
    png_bytes: bytes,
    strength: float,
    frame_id: str,
) -> bytes:
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0 and 1")

    with Image.open(io.BytesIO(png_bytes)) as source:
        if source.format != "PNG" or source.mode not in {"RGB", "RGBA"}:
            raise ValueError("source must be a single RGB/RGBA PNG")
        source_rgba = source.convert("RGBA")
        source_size = source_rgba.size
        source_alpha = source_rgba.getchannel("A").tobytes()

    value = normalized_strength(strength)
    input_sha = hashlib.sha256(png_bytes).hexdigest()
    idem = hashlib.sha256(f"{MODEL}\0{value}\0{input_sha}".encode()).hexdigest()
    headers = {
        "X-API-Key": os.environ["GPU_CONTROL_API_KEY"],
        "X-Request-ID": frame_id,
        "Idempotency-Key": idem,
        "X-Input-SHA256": input_sha,
        "Content-Type": "image/png",
        "Accept": "image/png",
    }

    for attempt in range(3):
        response = session.post(
            URL,
            params={"strength": value},
            headers=headers,
            data=png_bytes,
            verify=CA_FILE,
            timeout=(10, 360),
        )
        if response.status_code in {429, 503, 504} and attempt < 2:
            time.sleep(float(response.headers.get("Retry-After", 2 ** attempt)))
            continue
        response.raise_for_status()
        break

    if response.headers.get("Content-Type", "").split(";", 1)[0] != "image/png":
        raise RuntimeError("unexpected response media type")
    output_sha = hashlib.sha256(response.content).hexdigest()
    if output_sha != response.headers.get("X-Output-SHA256"):
        raise RuntimeError("response SHA-256 mismatch")
    if response.headers.get("X-Input-SHA256") != input_sha:
        raise RuntimeError("response belongs to another input frame")

    with Image.open(io.BytesIO(response.content)) as output:
        output_rgba = output.convert("RGBA")
        if output_rgba.size != source_size:
            raise RuntimeError("output dimensions changed")
        if output_rgba.getchannel("A").tobytes() != source_alpha:
            raise RuntimeError("output alpha changed")
    return response.content
```

LiClip 帧循环应按下列顺序执行：完整读取第 N 帧 → 计算输入 SHA → 调用并重试 → 验证响应 SHA/尺寸/Alpha → 原子写入第 N 帧输出 → 再进入第 N+1 帧。不能在响应校验前覆盖流水线输入。

## 6. Ready 与容量

```bash
curl --cacert GPU_CONTROL_LAN_CA.crt \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" \
  https://10.3.34.11/api/v1/realesrgan/ready

curl --cacert GPU_CONTROL_LAN_CA.crt \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" \
  https://10.3.34.11/api/v1/realesrgan/capacity
```

LiClip 开始一个项目之前应确认：

- HTTP 200；
- `status == "ready"`；
- `ready_nodes >= 1`；
- 需要四路吞吐时确认 `ready_nodes == 4`。

返回中的每个节点包含 GPU 名称、PyTorch/CUDA、FP16、tile、当前空闲显存、最近推理耗时和显存回收值。没有任何 CUDA 节点时返回 503，不会使用 CPU 接单。

## 7. 错误合同

所有控制器业务错误统一返回：

```json
{
  "error": {
    "code": "QUEUE_FULL",
    "message": "Real-ESRGAN queue is full",
    "request_id": "liclip-esrgan-frame-000001",
    "retryable": true
  }
}
```

| HTTP | code | 是否建议按原幂等键重试 |
|---:|---|---|
| 400 | `INVALID_IMAGE` | 否，修复输入 |
| 400 | `CHECKSUM_MISMATCH` | 是，重新读取并计算同一帧 |
| 401 | `UNAUTHORIZED` | 否，检查 Secret |
| 409 | `IDEMPOTENCY_CONFLICT` | 否，调用方错误 |
| 413 | `IMAGE_TOO_LARGE` | 否，缩小输入 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 否，转换成单帧 RGB/RGBA PNG |
| 422 | `INVALID_STRENGTH` | 否，修正参数 |
| 429 | `QUEUE_FULL` | 是，遵守 `Retry-After` |
| 503 | `NO_GPU_AVAILABLE` | 是，退避后重试 |
| 504 | `INFERENCE_TIMEOUT` | 是，保持相同幂等键 |
| 500 | `INFERENCE_FAILED` | 视业务策略重试一次 |

已实测 422 数值越界、422 非数字、401 错误 Key、415 错误媒体类型、413 超像素、400 SHA 不匹配，返回码和 JSON code 均符合上表。

## 8. WebUI 与任务列表

登录统一调度中心后打开：

```text
https://10.3.34.11/realesrgan
```

页面显示：

- 四节点 READY/UNREADY/推理中状态；
- GPU、PyTorch、CUDA、空闲显存、最近峰值和回收后显存；
- 集群容量、统一队列深度；
- 最近 200 条任务的提交时间、Request ID、状态、节点、尺寸、strength、排队/推理耗时、防重状态和错误码；
- 可复制的 LiClip curl 调用。

任务历史持久化在 `/srv/gpu-control/realesrgan/tasks.json`。任务列表属于 Real-ESRGAN 独立队列，不修改原有 GPU/资产任务表，也不会影响其他业务任务。

## 9. 验收记录

已完成以下生产入口验收：

1. 四台节点分别强制调度成功，尺寸一致，Alpha 差异 0，RGB 发生变化。
2. `strength=0` 输出 RGBA 像素与输入完全一致。
3. `strength=0.7`、`strength=1` 均真实改变 RGB。
4. 四并发请求同时分配到 4090、3090-A、3090-B、4070 Ti。
5. 隔离队列验收控制器使用容量 4、队列 1 发起 6 并发：5 个完成，第 6 个返回 429 `QUEUE_FULL` 和 `Retry-After: 5`；生产配置仍为队列 64。
6. 停止 4070 Ti 后容量正确变为 3，其余节点继续处理；恢复后回到 4。
7. 暂停全部四个新 Worker 时，Ready 返回 503 `NO_GPU_AVAILABLE`；不存在 CPU 回退；随后四台全部自动恢复 READY。
8. 人为在 100 ms 中断一次 1024 × 1024 响应，使用同一幂等键重试返回 `HIT`，没有重复执行 GPU，输出 SHA-256 为 `dda952666497bd87cc74c400332727e72b50e7200abcc2d94c5001f6c736cae8`。
9. 500 帧、4 客户端并发长稳测试全部通过：帧序、响应 SHA、输入 SHA、尺寸、Alpha、RGB 变化均正确，无截断或连接泄漏。
10. 500 帧耗时 32.64 秒，端到端吞吐 15.32 fps；节点实际处理分布为 4090=202、3090-A=132、3090-B=32、4070 Ti=134。
11. 长稳结束队列为 0、四节点 READY；最大单帧推理耗时 2751 ms，最大额外显存峰值 625 MiB；控制器文件描述符 15、线程 4，日志无 traceback/error/timeout。
12. Python 合同单测 3/3 通过；Ruff、Web TypeScript/Vite 生产构建、Compose 配置和 Nginx 配置检查通过。

## 10. 运维命令

4090 主控：

```bash
cd /opt/gpu-control
docker compose --env-file .env -f deploy/control-plane/compose.yaml \
  ps realesrgan-controller realesrgan-worker-4090 web nginx

docker compose --env-file .env -f deploy/control-plane/compose.yaml \
  logs --tail 200 realesrgan-controller realesrgan-worker-4090
```

3090-B 和 4070 Ti 处于 WSL，4090 上的桥接由 systemd 自动恢复：

```bash
systemctl status gpu-control-realesrgan-tunnel-3090b.service
systemctl status gpu-control-realesrgan-tunnel-4070ti.service
```

远端 Worker 使用 `/opt/gpu-control/deploy/realesrgan/compose.worker.yaml`，独立于原有 ComfyUI/资产 Worker。不要使用多个 Uvicorn worker，也不要删除同机已有 GPU 容器来“腾显存”；本服务的调度容量和 CUDA 缓存清理由自身完成。

## 11. 实现位置

- Controller / Worker：`apps/realesrgan/`
- Worker 部署与 WSL 桥接：`deploy/realesrgan/`
- 主控 Compose：`deploy/control-plane/compose.yaml`
- HTTPS 路由：`deploy/control-plane/nginx/nginx.conf`
- WebUI：`apps/web/src/views/Realesrgan.vue`
- 合同单测：`tests/unit/test_realesrgan_contract.py`

本交付没有改动或覆盖 LiClip/ImageClip 自己的帧流水线；LiClip 侧只需要按本文接入新的 HTTPS PNG API。
