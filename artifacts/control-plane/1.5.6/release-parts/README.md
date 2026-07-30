# GPU Control control-plane 1.5.6 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `310a44c70c20f7cbfc601d19e19858380a61c20a`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.6` | `sha256:26f622257facfbc74199c6d266b2a02e31b28dad6910596f5c4bd8fecf458cf4` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.6` | `sha256:c2c420e6fa8fd2d8d84852e5b509f5248cf6e1e8b1239c6f8053eee5e3a6845b` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.6` | `sha256:f83bed46d7540de4cf7d08e4cff8d7675dd7dd4675bf13d301226f5f5c4cb01f` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.6` | `sha256:54005b4091b37de0805f2561d3b53a3e470e2b1ed3a795ec6bd7b0e98b0ebc14` | `PENDING_REGISTRY_PUSH` |

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
cat gpu-control-control-plane-1.5.6-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.6-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.6-images.tar.gz
docker image load --input gpu-control-control-plane-1.5.6-images.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.6-images.tar.gz`
SHA-256: `577b251c215f252f0920e7db007c9b5e5e2993db2b4dd17e846d0c8dacf5bb87`
Size: `190862791` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.6-images.tar.gz.part-00` — `fc307632f1ce17973f99a0158e4abfe0f26ee0f2edd92ccd15adf226c5393e0a`, 134217728 bytes
- `gpu-control-control-plane-1.5.6-images.tar.gz.part-01` — `bd2f5a2561f9d91c752f6e54bcfe6a15ab4cfb82fd7b7c757d360f1c8063a82d`, 56645063 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
