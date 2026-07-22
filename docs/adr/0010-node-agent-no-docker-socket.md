# ADR 0010：Node Agent 不挂 Docker Socket

状态：接受。Docker Socket 等价宿主 root。Agent 只接受 HMAC、时间戳和 nonce 校验的固定动作，使用 `create_subprocess_exec` 调用 root 管理员安装的白名单包装器。Alloy 的只读日志采集挂载不赋予 Agent。

