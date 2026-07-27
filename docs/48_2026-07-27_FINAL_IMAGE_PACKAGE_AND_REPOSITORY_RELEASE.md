# GPU Control 1.3.4 与 ComfyUI projects-0.2.3 最终打包发行记录

日期：2026-07-27

控制节点：`10.3.34.11`

生产源码基线：`f3888e85cb927314a2cb1da07ea78b3e5d028f6d`

## 1. 发行结论

本次只打包 GPU Control 自有控制面镜像与已经验收的 ComfyUI 生产镜像，没有修改 ImageClip、ModelViewCreator 的工作流、节点、模型或业务仓库内容。

正式分发方式为：

1. `/srv/gpu-control/images/` 下的完整离线归档；
2. 本仓库 `artifacts/` 下由 Git LFS 管理的分片归档；
3. 每一层均提供 SHA-256，可在装载前完成闭环校验。

内网 Registry 当前未作为正式分发入口：现有 Registry Compose 仍绑定旧地址 `192.168.10.10:5000`，`registry.local` 也未配置可用解析与 Docker 信任链。本次没有为上传镜像而改动生产网络、Docker daemon 或 TLS 配置，避免影响正在运行的三节点生产集群。

## 2. ComfyUI 生产镜像

- 镜像：`registry.local:5000/gpu-control/comfyui:projects-0.2.3`
- Image ID：`sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea`
- 完整归档：`/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz`
- 归档大小：`8271225047` 字节
- 归档 SHA-256：`20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586`
- Git LFS 分片：`artifacts/comfyui/projects-0.2.3/`

分片校验：

```text
4af8df27d3bcfe35080780be9fda1dd211767f69b2224ff32123bdbaf9f48fce  part-00
02688563a0b818f200bbf60bca15f86a296778ef53860c13b77805c9fbbe79ab  part-01
3ae397be8a7ab5bc9c7d01e854ce316f8d955a8f1649ecfdedf4d22b4a42227b  part-02
789528093912238be820ca6873c30d700d73daf43e9de47cb4921ebf8c13640a  part-03
ec72ff03ec25adb41a48fd0375b8582a4f1e564da2bdb32bc962ccb86717920c  part-04
```

## 3. GPU Control 控制面镜像

完整归档：`/srv/gpu-control/images/gpu-control-control-plane-1.3.4.tar.gz`

- 归档大小：`149836214` 字节
- 归档 SHA-256：`462ab55f9775d4818b97f713a383b640d484f1a6a3a40d34d4204b13d21e1b36`
- Git LFS 分片：`artifacts/control-plane/1.3.4/`
- 分片 SHA-256：`462ab55f9775d4818b97f713a383b640d484f1a6a3a40d34d4204b13d21e1b36`

归档包含：

| 镜像 | Image ID |
|---|---|
| `gpu-control-api:1.3.4` | `sha256:39212e3422ab254d1ad08f4fd7ca08221ac4582cbebacfe3c1286b6453bf3942` |
| `gpu-control-scheduler:1.3.4` | `sha256:38427bca133cdbfd883577642e6d241fc6c793b4d1fb9fc911767353c8a06ee4` |
| `gpu-control-web:1.3.4` | `sha256:b6c9dbdf7dc7dd399ca07c6f3e4bdd76f5e94037515e6f0e4cd7f1a76f4623d9` |

## 4. 校验与装载

完整归档校验：

```bash
sha256sum -c /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz.sha256
sha256sum -c /srv/gpu-control/images/gpu-control-control-plane-1.3.4.tar.gz.sha256
gzip -t /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
gzip -t /srv/gpu-control/images/gpu-control-control-plane-1.3.4.tar.gz
```

从 Git LFS 分片重建：

```bash
git lfs pull
cat artifacts/comfyui/projects-0.2.3/comfyui-projects-registry-0.2.3.tar.gz.part-* > /tmp/comfyui-projects-registry-0.2.3.tar.gz
cat artifacts/control-plane/1.3.4/gpu-control-control-plane-1.3.4.tar.gz.part-* > /tmp/gpu-control-control-plane-1.3.4.tar.gz
sha256sum /tmp/comfyui-projects-registry-0.2.3.tar.gz /tmp/gpu-control-control-plane-1.3.4.tar.gz
docker load -i /tmp/comfyui-projects-registry-0.2.3.tar.gz
docker load -i /tmp/gpu-control-control-plane-1.3.4.tar.gz
```

## 5. 审计结果

- 两个完整归档均通过 `gzip -t`。
- Git LFS 分片逐个通过 SHA-256，重组后的归档 SHA-256 与原归档完全一致。
- API、Scheduler 以及 4090、3090-A、3090-B 的 ComfyUI Python 环境均通过 `pip check`，没有损坏依赖。
- GPU Control 的 API、Scheduler、Node Agent、Asset API、公共 packages 与 Python 运维脚本全部通过源码语法编译审计。
- 生产 Compose 配置通过解析校验；宿主运行虚拟环境不包含开发用 `pytest`，本次没有为了测试而临时安装依赖或改变生产环境。
- Git LFS 属性与暂存区对象已确认：大文件在 Git 中保存为 LFS pointer，不进入普通 Git object。
- 生产版本源代码已经在提交 `f3888e85cb927314a2cb1da07ea78b3e5d028f6d` 固化；本次发行提交只增加归档、校验和与发行文档。
- 发行归档不包含 `.env`、运行密钥、模型目录、任务输入输出、数据库数据或 Docker volume 数据。
- 三节点生产环境在打包前已通过节点在线、服务健康、工作流预检与零运行任务检查；上传完成后再次复核。

## 6. 安全清理记录

已清理：

- 已完成使命的验证容器 `comfyui-projects-test`；
- Registry 上传失败产生的三个临时别名标签；
- 一个无标签候选镜像及其无引用层，回收 `1.127 MB`；
- Docker BuildKit 可重建缓存，回收 `182.1 GB`，最终 Build Cache 为 `0 B`。

明确保留：

- 生产容器与生产镜像；
- 四个 4090 回滚容器；
- 全部模型、任务、数据库、Redis 数据、监控数据；
- 全部 Docker volumes；
- 正式离线归档与 Git LFS 分片。

清理后的 Docker 概览：69 个镜像、20 个容器、19 个本地 volumes；没有执行 volume prune。
