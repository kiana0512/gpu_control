# 准备清单

目标系统为三台 Ubuntu 24.04 LTS x86_64，4090 至少 32 GB 系统内存，两台 3090 建议至少 64 GB；系统盘和 `/srv` 预留镜像、模型、日志与任务空间。三机时间必须同步。

上线前填写 [USER_INPUT_REQUIRED](USER_INPUT_REQUIRED.md)，并完成：

- 固定局域网 IP/主机名和 SSH 管理账号；4090 能访问两台 3090 的 8188、9201、9100、9400。
- 备份旧数据；确认新架构允许全量替换。
- 准备 NVIDIA 官方支持的驱动、Docker Engine、Compose plugin、NVIDIA Container Toolkit。
- 准备 API 工作流、模型 SHA-256、自定义节点 commit、域名证书和飞书配置。
- 禁止把 `.env`、模型、输入输出、证书或日志提交到 Git。

检查点：`timedatectl status` 显示同步；`df -h /srv` 容量充足；`ip route` 可达三机。任何一项不满足时不要开始生产部署。

