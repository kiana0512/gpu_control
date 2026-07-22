# 负载测试与容量

无 GPU 的 100 并发验收已由 `tests/integration/test_api.py` 覆盖并确保 100 个请求返回 202。服务级测试先注册 Fake 工作流和测试 API Key，再启动 PostgreSQL、Redis、API、scheduler 与三个 Fake ComfyUI；设置：

```bash
export LOAD_TEST_API_KEY='gpc_...'
export LOAD_TEST_WORKFLOW=fake
make load-test
```

Locust 默认 100 用户、每秒增加 25、运行 20 秒；429 是配额保护的允许响应，其他非 200/202 均失败。验收同时观察 API p95、scheduler decision/loop lag、DB 锁等待、队列最老等待和节点 `current_jobs`。

真实容量不是“请求数”，而是工作流平均推理时间：两台 3090 理论吞吐约 `2 / 平均秒数` 任务/秒；4090 只作为人工/阈值溢出，不计稳态容量。用生产代表工作流逐级 1→10→25→50→100 用户压测，温度/显存或错误率越阈值立即停止。真实 GPU 结果必须填入验收清单。

