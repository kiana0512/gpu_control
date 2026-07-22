# 初学者概览

GPU Control 把“接收请求”和“执行推理”分开。API 把任务写入 PostgreSQL 后立即返回 `202`；调度器等 GPU 空闲后才领取一项、上传输入、提交一个 ComfyUI API prompt、监听进度并保存输出。Redis 只负责叫醒调度器和推送瞬时事件。

三个关键概念：任务状态保存在数据库；每张 GPU 对应一个 ComfyUI；真实工作流必须由 ComfyUI 的 **Export Workflow (API)** 导出。普通 UI 保存 JSON 含坐标和连线，不能直接提交。

第一次上线顺序：准备三台 Ubuntu → 安装 NVIDIA 驱动/Container Toolkit → 4090 部署控制面 → 构建并分发同一 ComfyUI 镜像 → 同步并校验模型 → 注册工作流 → 先用 3090 冒烟 → 配置监控和飞书 → 最后按验收清单放量。

回滚原则：应用镜像按 tag 回退；数据库先备份再迁移；模型不进镜像；4090 保持 `RESERVED` 是安全默认值。

