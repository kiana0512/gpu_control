# ADR 0003：PostgreSQL 是任务真相来源

状态：接受。任务、事件、尝试、prompt_id、租约和回调必须事务一致且可恢复；`FOR UPDATE SKIP LOCKED` 适合队列消费者。任何 Redis/进程内状态都可丢弃并从数据库重建。

