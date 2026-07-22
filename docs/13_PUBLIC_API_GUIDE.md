# 公共 API

认证头：`X-API-Key: gpc_PREFIX_SECRET`。Key 只显示一次；任务创建推荐使用唯一 `Idempotency-Key`。

```bash
curl -X POST https://CONTROL/api/v1/jobs \
  -H "X-API-Key: $GPU_CONTROL_API_KEY" \
  -H "Idempotency-Key: order-123-attempt-1" \
  -F workflow_key=inpaint -F workflow_version=1 \
  -F 'parameters={"steps":20}' \
  -F input_image=@input.png -F mask=@mask.png
```

成功立即返回 `202`、`job_id/status_url/events_url`。同 Key 同内容返回已有任务；不同内容返回 409。查询 `GET /api/v1/jobs/{id}`，SSE 订阅 `/events`，产物列表 `/artifacts`，下载 `/artifacts/{artifact_id}`，取消 `POST /cancel`。

可传 `callback_url`，但必须是管理员为该客户批准的 HTTPS 域名。响应中的 `callback_secret` 只显示一次；接收端用它校验 `HMAC-SHA256(timestamp + "." + raw_body)`，并校验时间窗。系统不跟随重定向、拒绝私网解析、最多六次指数退避并记录每次尝试。

稳定错误码包括 `AUTH_FAILED`、`RATE_LIMITED`、`INPUT_INVALID`、`WORKFLOW_NOT_FOUND`、`IDEMPOTENCY_CONFLICT`、`CALLBACK_URL_REJECTED`。记录响应的 `X-Request-ID` 供排障。

