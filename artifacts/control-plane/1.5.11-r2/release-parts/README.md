# GPU Control control-plane 1.5.11 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `969b535645e05be32597a9a86d1510cd84febd51`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.11` | `sha256:2898f261047f3d8952dc8ead9a077adbe8cd747c05c827854103b1b93bfeaaff` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.11` | `sha256:deaa744716cd4f9dd12c2c85b30398aa9787e2e4ab22e868bb9edf2060a3108d` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.11` | `sha256:224c71431319f9da0cb9e50690d9e7d3d3df2fafbb99ebd538ff5b2216015e8d` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.11` | `sha256:3df278ecae3fa0227694781989b029716f22c35c75458131c5c2849d39dc843c` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.7-retopology-coordinate-restore-v2` | `sha256:e062df651929232f0c26633e1634e0526d06ce54b121485d30fbe5a166e9d8a8` | `PENDING_REGISTRY_PUSH` |

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
SHA-256: `4cb3836718c6060d9785a6a92eba9fe3b3c8039a5ac93e4fedba13f3ae21b7dd`
Size: `836357948` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.11-images.tar.gz.part-00` — `49a6da8a501a25e9583bed5230ac53cde78271c1575028c3538b1d97519079db`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-01` — `5730cd6149cb396521af5986584220e5fb708948d31c600cc566bc87b7344749`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-02` — `0976c77df8f7a94d953e40fc8e7bbdca84cc01699f94151d582b5cdbde0d3a03`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-03` — `3a42904b38759b31489c9c9192ff152608ca762e951831f8463846447f3d80d7`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-04` — `e6dbaa4368b8998ff9b99fa75f18e6877e93c05323691d3decff2e79b58e96a0`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-05` — `92c72f2837c19577b08b32acb1ffab62adfdd6d36db6e305b377ea7611e4efdf`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-06` — `d7a2ac0451b0af364756e0fa27f524889696c0215c5403247c3b0d84735941d5`, 31051580 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
