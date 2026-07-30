# 负载测试与容量

无 GPU 的 100 并发验收由 `tests/integration/test_api.py` 覆盖。六个真实业务 API 的 GPU+CPU
混合负载、生产专用门禁、素材合同、只读预检、结果归档和停止规则，以
`docs/67_2026-07-30_SIX_API_MIXED_LOAD_TEST_RUNBOOK.md` 为唯一当前手册。

`make load-test` 现在**只生成计划且不联网**。真实执行必须显式使用
`make load-test-execute`，并通过该手册列出的全部环境门禁；生产还需要独立生产开关、变更单、
有效时间窗口、生产域确认令牌，以及活动任务/队列为 0 的只读预检。

隔离服务级测试可先注册 Fake 工作流和测试 API Key，再启动 PostgreSQL、Redis、API、scheduler
与三个 Fake ComfyUI；六接口计划入口为：

```bash
make load-test
```

示例 scenario 是 `1→10→25→50→100→120` 用户，并因 `weights_confirmed: false`、占位 SHA、
外部素材未配置和授权变量缺失而默认无法执行。429 会作为 admission/retry 证据记录，不再被笼统
视为成功。验收同时观察 HTTP/业务 P50/P90/P95/P99、队列时间、吞吐、重试/恢复、节点/Worker
分布、scheduler loop lag、DB 锁等待、队列最老等待和节点 `current_jobs`。

真实容量不是“请求数”，而是工作流平均推理时间和资源混合后的闭环吞吐。权重必须由近期真实
流量复核，温度/显存、失败率、队列或拖尾越过手册阈值立即停止。真实 GPU 结果必须归档原始
Locust 文件、任务闭环记录、清单与 SHA-256；没有执行证据不得写成“通过”。
