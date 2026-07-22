# ADR 0001：不使用 Kubernetes

状态：接受。固定三台主机、三张 GPU 和单槽 ComfyUI 不需要集群编排成本；Compose、systemd、PostgreSQL 租约与明确故障手册足够。若未来扩展到动态节点/多租户隔离，再重新评估 Kubernetes。

