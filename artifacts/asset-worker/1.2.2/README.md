# Asset Worker 1.2.2 image

Image: `li3d/blender-worker:1.2.2`

Image ID:

`sha256:9bf4344503041abec7dd67067ccbbb0946223af53b06d1a4a67a27acfeaab6ad`

The zstd-compressed Docker archive is stored as one Git LFS part:

`li3d-blender-worker-1.2.2.tar.zst.part-00`

Reconstruct, verify, and load:

```bash
cp li3d-blender-worker-1.2.2.tar.zst.part-00 /tmp/li3d-blender-worker-1.2.2.tar.zst
sha256sum -c SHA256SUMS.txt
zstd -t /tmp/li3d-blender-worker-1.2.2.tar.zst
zstd -dc /tmp/li3d-blender-worker-1.2.2.tar.zst | docker load
docker image inspect li3d/blender-worker:1.2.2 --format '{{.Id}}'
```

Expected archive size: `685495065` bytes.

This archive contains the worker image only. It does not contain production jobs,
credentials, TLS private keys, external business repositories, or model data.
