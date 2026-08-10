# GPU Control control-plane 1.5.11 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `04e281cd5a4a4c865e738255af7edd31e78a6c06`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.11` | `sha256:3f6901b70c655c3a00b0b6ccb33cc2b09a63ed74fe1118c85136ba064683decd` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.11` | `sha256:eb94f14c17cf832d4d31151be7c9f67dd8fdc174e3b070e945d8a52b88a17683` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.11` | `sha256:708efc1693d614dacdc126e2ee76e78359426d89f1ff92e56a1c9a0eaf77e576` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.11` | `sha256:da60eeca9bfa157032b59bdcf00dfdd6992b5534a563a90e491d9d14964f44ad` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.7-retopology-coordinate-restore-v2` | `sha256:026f18d613901b42b1590661ba86df45e395131b000268407d719013c44c3a9d` | `PENDING_REGISTRY_PUSH` |

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
SHA-256: `964b16389f8726903065aa3b2e39ab475207294d8cb2fccec0e74bdd08a7f998`
Size: `836357874` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.11-images.tar.gz.part-00` — `6bca66dc7739369a5e605c0d17fd29ac5ca36d67b341c7c15d168e4d6ebddb57`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-01` — `ed785d6ad4055aa0cb28f1fe06b8b33d1cddcbe6811e719a77e09fdc8fe675d8`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-02` — `37b1da893c912d7ff1582b75c7b0744e5fd24b1cfc1b4e3e4e4b33b603e155c1`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-03` — `d1190bb3b95fe8fb70bf8404949767798d500405e133be1081176e12aac64559`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-04` — `0e99461880d48450f42a3f9ca12ec931c8a6d99b4d938674d9f7cb41b8b9a90f`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-05` — `7a5b0bd4cf1c58bff77e626951f7ba8e0e7074003d200a10bedba13aec3f2507`, 134217728 bytes
- `gpu-control-control-plane-1.5.11-images.tar.gz.part-06` — `8d6c4b590897c2ac7fc9887549db47c8325aa0529e550b393379856b63e87109`, 31051506 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
