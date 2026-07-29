# Asset Worker 1.2.1 image

Image: `li3d/blender-worker:1.2.1`

Image ID:

`sha256:737f182435d6cb25e1b2c574ed5eedeb587da9bf043cf6aa49fe1c19cea95459`

The zstd-compressed Docker archive is stored as one Git LFS part:

`li3d-blender-worker-1.2.1.tar.zst.part-00`

Reconstruct and load:

```bash
cp li3d-blender-worker-1.2.1.tar.zst.part-00 /tmp/li3d-blender-worker-1.2.1.tar.zst
zstd -dc /tmp/li3d-blender-worker-1.2.1.tar.zst | docker load
```

Always verify `SHA256SUMS.txt` before loading.
