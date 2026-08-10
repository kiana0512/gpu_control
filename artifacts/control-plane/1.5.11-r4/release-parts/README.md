# GPU Control control-plane 1.5.11 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `07414f496c1b58cd6e258fc8f2de61cd16f51aa9`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.11` | `sha256:66887292ecb72f72bc55c2131cbd931e06e4471e5c8041ad269d399fecc57d19` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.11` | `sha256:2190342114153501c2c260d0781825775f8b03644a256ef9ccdd72c082397ef8` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.11` | `sha256:c320bb709fa1b5724b9a74fcaef3b18f393531c1fc8849f0dd1a3546c77fa25c` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.11` | `sha256:0cd1bcefb10f1ee67b46f9ea8af3fd5ce1827145973ec07d866cb072f5aabf79` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.7-retopology-coordinate-restore-v2` | `sha256:c295610f05a4299b7bd9118986214df7bd9245203a34b96b7100cf829a09a300` | `PENDING_REGISTRY_PUSH` |

All five first-party images were built from the same clean, pushed full Git SHA. Control-plane
components use release `1.5.11` and the Blender Worker uses
`1.4.7-retopology-coordinate-restore-v2`. Each component uses one
attested OCI solve followed by a Docker-loadable solve that imports the first solve's local cache.
The config bytes inside each Docker archive must hash to the attested OCI config digest; a mismatch
fails closed. Docker Engine 29 with the containerd image store may expose a local manifest/content
identity as `.Id`, so that engine-local value is recorded but is not misidentified as a config digest.
OCI labels and each applicable runtime build-version environment were checked before the combined
`docker image save` archive was created. No Compose command, service restart, production migration,
registry push, or Git LFS push is performed by the packager.

## Offline attestation state

- BuildKit provenance: `VERIFIED_OFFLINE_OCI`
- SBOM: `PENDING_PINNED_SBOM_GENERATOR`
- Registry-bound SBOM/manifest identity: `PENDING_REGISTRY_PUSH`

An offline OCI digest is evidence about the local OCI export only. It is **not** a registry digest
and must never be copied into the registry fields in the V4.1 receipt.

## Reassemble

```bash
cat gpu-control-control-plane-1.5.11-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.11-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.11-images.tar.gz
docker image load --input gpu-control-control-plane-1.5.11-images.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.11-images.tar.gz`
SHA-256: `b7d16b1288d7bcfb0e481fb25975c2b3b116112bed3ce72a89e33a671696fe69`
Size: `836367741` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.11-images.tar.gz.part-00` — `38072f5674b539aae44126bb4a490a3a552b7b16fde3a65c12f5f88a21b53708`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-01` — `793fbb9df92084193961396f539031c7f380830148021538102bc79be6cec3ab`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-02` — `77869f05c08663f91c245db1127dd3539622e1e99367dffe9edd6d841086bab9`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-03` — `739235beb4742d251b368b50c71d525ad4ae0ecee962fdb576c824f55e35217b`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-04` — `7aa5ea025cce126e228ea758018b880418e11405611ebf4452e087b2bfe68316`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-05` — `afa278aed151e58de5a7dcc25a7acb8e86ac01e8ea7ebcb4835aa1026218609e`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-06` — `2d04f8cdad03df06695ec915cc1d79f1faa6de271080dd0e515c69df0ddb7669`, 31061373 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
