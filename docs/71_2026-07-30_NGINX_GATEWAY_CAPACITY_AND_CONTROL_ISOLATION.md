# Nginx 网关容量与控制流隔离（源码候选，未部署）

日期：2026-07-30  
状态：`CODE_AND_TEST_ONLY / NOT_DEPLOYED`

## 背景

r4 的 25 VU 运行证明旧网关的单来源 IP `10 r/s + burst 20 + 20 active
requests` 会先于应用层限流返回 429。业务请求、节点心跳和 capacity 查询还共用同一个
request/connection budget，因此同一出口 IP 的业务流量可以让控制流一起被拒绝。

本候选只修改仓库中的 Nginx 配置与验证测试，没有 reload、重启、部署或生产流量操作。

## 固定预算

| 流量类别 | 精确路径/范围 | 速率/IP | burst | 活动请求/IP | zone |
| --- | --- | ---: | ---: | ---: | --- |
| 业务 API | `/api/`、`/api/v1/assets/` | 240 r/s | 480 | 256 | `business_api` / `business_connections` |
| 节点心跳 | `/api/v1/nodes/heartbeat` | 10 r/s | 20 | 16 | `node_heartbeat` / `node_heartbeat_connections` |
| Scheduler capacity | `/api/v1/scheduler/capacity` | 60 r/s | 120 | 64 | `scheduler_capacity` / `scheduler_capacity_connections` |
| Asset capacity | `/api/v1/assets/capacity` | 60 r/s | 120 | 64 | `asset_capacity` / `asset_capacity_connections` |
| Admin | `/admin/` | 5 r/s | 10 | 10 | `admin` / `admin_connections` |

业务预算按单一 NAT 来源的 120 VU 设计：稳态约有 `2 req/s/VU`，burst 可吸收约
`4 requests/VU` 的同步尖峰。它仍是有限的粗粒度边缘保护；主 API 认证后继续执行数据库
中的 `RateLimitPolicy`，默认按 client/API key 使用 `5 r/s、burst 10`。三个控制端点使用
精确匹配和彼此独立的 request/connection zone，capacity 轮询不能消耗心跳预算，业务流量
也不能消耗任何控制预算。Admin 保持原速率并获得独立连接预算。

## 风险与边界

- 网关放宽后，应用、Redis、PostgreSQL 或上传 I/O 会更早成为瓶颈；应用层 429 仍是正常
  保护行为，不能将其误判为网关回归。
- 单 NAT IP 超过 120 VU，或每个 VU 同时持有多条上传/SSE 长连接时，256 个活动请求仍可
  返回 429。该上限不是“无限并发”承诺。
- Asset API 当前没有主 API 等价的 Redis client 级请求速率器，因此 `business_api`
  的 240 r/s 仍是 Asset 路由的重要边缘保护；不能在未补应用保护前继续无证据上调。
- 独立 zone 会增加少量 Nginx shared-memory 使用；每个控制 request/connection zone 为
  1 MiB，业务与 Admin zone 各为 10 MiB。
- 路径只对文档中的 canonical URI 使用精确匹配。多余尾斜杠会落入业务 zone，客户端应
  使用 canonical URI。

## 离线验证

静态契约测试确保预算、路径、upstream 和 zone 之间没有重新耦合：

```bash
pytest -q tests/unit/test_nginx_gateway_config.py
```

语法验证使用一次性自签名证书和无网络临时容器，不读取生产私钥，也不接入生产网络：

```bash
scripts/validate_nginx_config.sh
```

成功标准包含：

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

验证脚本固定默认镜像 `nginx:1.28.0-alpine`，可通过
`NGINX_CONFIG_TEST_IMAGE` 指向已经批准且已缓存的相同版本 digest。脚本只执行
`nginx -t`；它不会 reload 或连接任何 upstream。

## 后续生产门禁

本文件不授权部署。后续变更窗口必须先确认无活动变更冲突，再执行配置备份、候选
`nginx -t`、受控 reload，并观察网关 429、API/Asset API 延迟、数据库连接池和三个控制
端点连续可用性。若出现 5xx、心跳丢失或数据库饱和，按既有回滚流程恢复旧配置。
