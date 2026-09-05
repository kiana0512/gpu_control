# GPU Control 1.5.16 control-plane hotfix archive

Source revision: `3f0084b86436ac735cb7dbf50def4901b8568710`  
Scope: 3090-B expired Substance soft-drain automatic recovery and WebUI recovery indication.

## Images

| Image | Local immutable ID |
|---|---|
| `gpu-control-api:1.5.16` | `sha256:f55c762925f0c7300f4e655746241955c2535f0ac9f853c9436f6bc2075d6f85` |
| `gpu-control-scheduler:1.5.16` | `sha256:b74fd2242061d96267f652b1134d1435087ae4358bbe165491183b1f7fb29532` |
| `unified-scheduler-asset-api:1.5.16` | `sha256:7dfc6c14f23a62464fa59f2bf59877069237270cad9f7157d580a7996a91b8ce` |
| `gpu-control-web:1.5.16` | `sha256:4d6f134c99669fc8e85cd09211b9ff265061393a2f5bdccefb4f359b4d343e6c` |

All four images carry version `1.5.16` and the exact source revision above. This hotfix does not
replace ComfyUI, Blender Worker, Node Agent, ImageClip, ModelView workflows, custom nodes or models.

## Reassemble and verify

```bash
cat gpu-control-control-plane-1.5.16-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.16-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.16-images.tar.gz
docker image load --input gpu-control-control-plane-1.5.16-images.tar.gz
```

Combined archive:

- Size: `196203691` bytes
- SHA-256: `01d453cf3e3e3accb42e13d84e8abffa6e75c2b06feeb090ad042daafa533666`
- LFS parts: 2

Registry push and registry digest remain `PENDING`; local image IDs are not represented as registry
digests.
