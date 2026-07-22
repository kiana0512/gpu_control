# ADR 0009：使用 Loki 与 Grafana Alloy

状态：接受。三机容器/systemd 日志需要统一字段检索；Alloy 在源端采集，Loki 集中存储，与 Grafana 指标关联。应用继续输出 stdout JSON，故障时仍可本地查看。

