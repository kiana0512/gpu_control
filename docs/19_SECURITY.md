# 安全

- 外部只开放 Nginx 443；PostgreSQL、Redis、ComfyUI、exporter 不暴露公网。UFW 对 Node Agent/ComfyUI 仅允许 4090。
- API Key 只存 HMAC hash，管理员密码用 Argon2，JWT 短时有效；管理操作按 admin/operator/viewer RBAC、二次确认并审计。
- `.env` 权限 600，日志处理器按键名脱敏。回调必须预批准 HTTPS host，投递前 DNS 解析必须全部为公网地址，不跟随重定向并有 HMAC。
- 上传限制大小、安全文件名、原子写入、路径归一化和 SHA-256；工作流只允许 schema 参数与显式 binding，禁止服务端路径注入。
- Node Agent 只接受时间戳/nonce/HMAC 请求，动作映射到固定 argv，无 shell、无任意命令、无 Docker Socket。Alloy 为读 Docker 日志可只读挂 socket，它不是运维控制面；可改用 Docker logging driver 进一步移除。
- ComfyUI/应用尽可能非 root；模型只读挂载；镜像固定版本，不用 latest、commit 或运行时插件安装。

季度轮换 JWT/API pepper/Agent/飞书 Secret；API pepper 轮换会使旧 Key 和回调密钥失效，应先通知客户并双轨换 Key。发现泄露立即禁用 Key、保存审计、轮换并检查 Loki 中是否有未脱敏痕迹。

