# GPU Control control-plane 1.5.9 superseded candidate archive

> Status: **SUPERSEDED / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `4f055a0f284eed5e1a8274cef3922356b2023bc3`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.
>
> This archive was superseded on 2026-08-04 by the idle-only fast recovery fix for
> transient Codex probe failures. It remains immutable historical build evidence and
> **must not be deployed**; a replacement archive must bind the later source commit.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.9` | `sha256:6c1fd45e061bc00bdb80a06e3ee1c1f98547170f06efe53405b5dec96e7451f0` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.9` | `sha256:bc4ea0310f685f9ab7bf60a9f8b06421b6f94637cfa8f5bf1e8e49a367a22ba7` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.9` | `sha256:902ae543f550ed4a29a277f77fa2dd85a01e1f0924ec6be37299252f8272728c` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.9` | `sha256:ca3a524a52a9ddc697a17b553101bf6da965b89c93f6430fe84e14fe9e8867d2` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.2.5` | `sha256:849d261a2746a92a5704d9c6399e90035dd5dab7da184889ec18d79a5cdb1ae7` | `PENDING_REGISTRY_PUSH` |

All five first-party images were built from the same clean, pushed full Git SHA. Control-plane
components use release `1.5.9` and the Blender Worker uses
`1.2.5`. Each component uses one
attested OCI solve followed by a Docker-loadable solve that imports the first solve's local cache.
The config bytes inside each Docker archive must hash to the attested OCI config digest; a mismatch
fails closed. Docker Engine 29 with the containerd image store may expose a local manifest/content
identity as `.Id`, so that engine-local value is recorded but is not misidentified as a config digest.
OCI labels and each applicable runtime build-version environment were checked before the combined
`docker image save` archive was created. No Compose command, service restart, production migration,
registry push, or Git LFS push is performed by the packager.

## Offline attestation state

- BuildKit provenance: `VERIFIED_OFFLINE_OCI`
- SBOM: `VERIFIED_OFFLINE_OCI`
- Registry-bound SBOM/manifest identity: `PENDING_REGISTRY_PUSH`

An offline OCI digest is evidence about the local OCI export only. It is **not** a registry digest
and must never be copied into the registry fields in the V4.1 receipt.

## Reassemble

```bash
cat gpu-control-control-plane-1.5.9-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.9-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.9-images.tar.gz
docker image load --input gpu-control-control-plane-1.5.9-images.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.9-images.tar.gz`
SHA-256: `19e3d1abc3c04007947409f6fde5c3cbc14417d6c4aeb4d11c26487829b2a76a`
Size: `830265301` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.9-images.tar.gz.part-00` — `65a0b799f893327d979937b9c7ba65f3efb1a51c6462179267605c0e8ff6dfc3`, 134217728 bytes
- `gpu-control-control-plane-1.5.9-images.tar.gz.part-01` — `842d814112029980f6df85ca9ce392b07a23d24474537f4efb9d0ac379a5e4a6`, 134217728 bytes
- `gpu-control-control-plane-1.5.9-images.tar.gz.part-02` — `e39462f1ee7d9988f40acc232a03f12b796f98c605a4ef36445941b77a5ae42f`, 134217728 bytes
- `gpu-control-control-plane-1.5.9-images.tar.gz.part-03` — `a96f22fa6ed648e848ec727cf00bc429a2617606d9a764d5cc0b6db40e053af3`, 134217728 bytes
- `gpu-control-control-plane-1.5.9-images.tar.gz.part-04` — `2dfc8b38e32f033453cc8b3a5ced8ab36bc2b630aef40011cc76c05237757604`, 134217728 bytes
- `gpu-control-control-plane-1.5.9-images.tar.gz.part-05` — `0f5c6e6eb023f5aef722982a1cc406f84fdcb9c85b47ece0355dec23be4458ee`, 134217728 bytes
- `gpu-control-control-plane-1.5.9-images.tar.gz.part-06` — `d73b0d5a0b450b93a1fae1887abdd3781b543a68101eabdf51e1d0ecd344701f`, 24958933 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
