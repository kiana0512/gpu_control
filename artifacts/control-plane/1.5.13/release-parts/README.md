# GPU Control control-plane 1.5.13 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `94022a699b12a5928597664d0ecffdcee582d1b7`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.13` | `sha256:83e1f86f1fdf1e2d130d688f735634f72ec4b87e5652b53c1500704be993bb84` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.13` | `sha256:0322ad3d50331dd5c27bc68e38a5ec88203455a7ec951dd928bfcb6c1b571720` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.13` | `sha256:29a56671d8edf7642a51f822e392bd8c8db64766802aad0393adf8532881f84f` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.13` | `sha256:23c13b92b94ea6aa844baefe8b7cfb4bc3315d4ea79a0cae8a9df9bfc550e9f5` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.48` | `sha256:c3e20f206889fbb8fcdf2d9532b68130dc41ffa6fcc3ff0be2a6bf4e0d382698` | `PENDING_REGISTRY_PUSH` |

The WSL telemetry component is archived separately after the five-image packager completed:

| Image | Local image ID | Source revision |
|---|---|---|
| `gpu-control-node-agent:1.5.13` | `sha256:1f8d1a43df8e7ba62875e438717355d0e81fc0434ccc753c3f6fae79fe55b093` | `94022a699b12a5928597664d0ecffdcee582d1b7` |

All five first-party images were built from the same clean, pushed full Git SHA. Control-plane
components use release `1.5.13` and the Blender Worker uses
`1.4.48`. Each component uses one
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
cat gpu-control-control-plane-1.5.13-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.13-images.tar.gz
cat gpu-control-node-agent-1.5.13-image.tar.gz.part-* \
  > gpu-control-node-agent-1.5.13-image.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.13-images.tar.gz
gzip -t gpu-control-node-agent-1.5.13-image.tar.gz
docker image load --input gpu-control-control-plane-1.5.13-images.tar.gz
docker image load --input gpu-control-node-agent-1.5.13-image.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.13-images.tar.gz`
SHA-256: `f57ac79e6ec937f8ca3d060f81d4ea72d6ffee03b724462f23b2dc3d1cbbf0d9`
Size: `837019163` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.13-images.tar.gz.part-00` — `08ba1dff23d87624598c5c306643e2a365006e47b04a434e3227e8043454b4ee`, 134217728 bytes
- `gpu-control-control-plane-1.5.13-images.tar.gz.part-01` — `0615aebf7e420eef0db5ba6b3fb104a925c8bd9325fc5fad71dd1d9458272d01`, 134217728 bytes
- `gpu-control-control-plane-1.5.13-images.tar.gz.part-02` — `85999730284019d2e29149a294a39d57ce26c3bb55354d463742a224d031b053`, 134217728 bytes
- `gpu-control-control-plane-1.5.13-images.tar.gz.part-03` — `62042b4c479fd26582d5532233ec6e8533e1297191186a3e9ad4b6783eb14807`, 134217728 bytes
- `gpu-control-control-plane-1.5.13-images.tar.gz.part-04` — `8f55476b0b9cb61605ce7630acc30513cb09e50c250c7bf787feb7c5e254f957`, 134217728 bytes
- `gpu-control-control-plane-1.5.13-images.tar.gz.part-05` — `bea041ad02db2b76c2145a8abefc23db54ae993290a12c2fe42cde7e72f0c89e`, 134217728 bytes
- `gpu-control-control-plane-1.5.13-images.tar.gz.part-06` — `0a207ad7172bd0dcc246ee9920d673cc04e54b42a014a67521391f8c9da87708`, 31712795 bytes
- `gpu-control-node-agent-1.5.13-image.tar.gz.part-00` — `32f2ad35bff9a5ef00f0408517847f99dca9ea7fbef43fe8bcf4c200c8d93450`, 89212344 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
