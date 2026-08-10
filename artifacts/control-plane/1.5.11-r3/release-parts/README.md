# GPU Control control-plane 1.5.11 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `3583023db112a684a757fa2f1a10fec5fcd47463`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.11` | `sha256:cc377158b512661b56a30fc49b38266c6a05e92e944f0263b924a286e2326ffe` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.11` | `sha256:8f7d2490da9d1e211f1342034f8c1b4a0843b1b92fbedd7047631ec768c2671c` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.11` | `sha256:e35fa7d2c10e16186fb8dc619a538b9639ac5f580f5301e76a73deb16b9771cb` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.11` | `sha256:e647fc8341fb12084e209d4d29be1f6ad4260c8488eb5f8ba450b29dc23a1696` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.7-retopology-coordinate-restore-v2` | `sha256:072b4175101f8e8d0a64d5bb89fdcc11a738ba6077b990aba57beadcb81c3a27` | `PENDING_REGISTRY_PUSH` |

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
SHA-256: `e51c810aed37a274b6dd349c10cfa874be5ce9e473dfe42f55594ea80afde705`
Size: `836362966` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.11-images.tar.gz.part-00` — `5d8334b7e22b70ab378c8dc0ce467f029e0a6942f92f39bda3fdda5deae5f7e5`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-01` — `043b17ec705a7f7e8445beb7316ab6c882bb0e7c490f2c55279e90429956e753`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-02` — `3dbc6415cb928544236e790c319bfd38829d08aff99300f94d0cf3923527dd20`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-03` — `b1e995463eb72b49bfac495553b229da3019fc0aa480a514fbef7e8a83f197d7`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-04` — `d2e1a4585912741e4580998932c2993f1030263cc87ef178ea7afd08da1d06e5`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-05` — `a334507958339c8a7ee7bf5f335846fa2e25622d26eda30cde0da37d8e0076d1`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-06` — `d298f92d011560956ef465c5cb369f6c9183797abdfa4bc0a13cea3b79318e87`, 31056598 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
