# ADR 0007：不使用 docker commit

状态：接受。commit 无法审计安装过程、依赖版本和供应链来源。Dockerfile、完整 Git commit 和 lock 文件是唯一镜像来源，旧 tag 提供可验证回滚。

