# ADR 0002：不使用 Celery/Flower

状态：接受。用户允许尝试 Celery，但固定三 GPU 的关键问题是事务领取、单槽、4090 Guard 和 prompt 恢复，不是通用消息执行。PostgreSQL + asyncio 更少双写状态，Flower 也不能替代业务生命周期。若任务类型扩展到大量非 GPU 通用 worker，再重新评估。

