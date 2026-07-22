# E2E 测试

无 GPU 验证由 `tests/fake_comfyui`、API 集成测试与 `tests/load` 共同覆盖。需要服务级全链路时，按 `docs/21_LOAD_TEST_AND_CAPACITY.md` 启动 PostgreSQL、Redis、API、scheduler 与三个 Fake ComfyUI 实例；真实 GPU 验证步骤记录在 `docs/23_ACCEPTANCE_CHECKLIST.md`。

