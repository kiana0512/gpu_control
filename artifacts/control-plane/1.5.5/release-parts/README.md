# GPU Control control-plane 1.5.5 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `7656aa68ebde9c95f5a41c52db3f066cae00e249`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.5` | `sha256:762dc15ebc72ba8825906a0716e781f9a8d9ec29f0e81793b820489faba3ec43` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.5` | `sha256:6abbaa1ed6a9238109dfa2d6f6fb3804804f73366d5944bd3562331511cf206d` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.5` | `sha256:52c8c96e79074b086884afd4b72a10c4fe6a79479f0a6552721a042fdd96aec6` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.5` | `sha256:80f8651621d2264ce00500180a19fbf6ceaad9887ef4adc44983b67a4341f0bf` | `PENDING_REGISTRY_PUSH` |

All four images were built from the same clean, pushed full Git SHA. Each component uses one
attested OCI solve followed by a Docker-loadable solve that imports the first solve's local cache.
The config bytes inside each Docker archive must hash to the attested OCI config digest; a mismatch
fails closed. Docker Engine 29 with the containerd image store may expose a local manifest/content
identity as `.Id`, so that engine-local value is recorded but is not misidentified as a config digest.
OCI labels and the Python runtime build-version environment were checked before the combined
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
cat gpu-control-control-plane-1.5.5-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.5-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.5-images.tar.gz
docker image load --input gpu-control-control-plane-1.5.5-images.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.5-images.tar.gz`
SHA-256: `c2ad09f1282c546f96a501155a6a041cfe1284e7eb1e91570c76149132e3527a`
Size: `190739722` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.5-images.tar.gz.part-00` — `4bf4bb85953f2bbc428947b981a81c8c1428b51a468e145da965630f421ff1f8`, 134217728 bytes
- `gpu-control-control-plane-1.5.5-images.tar.gz.part-01` — `4553483154a2ae3b57ffee6c33f90697aca266b4d721de578f89714cd0f628fc`, 56521994 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
