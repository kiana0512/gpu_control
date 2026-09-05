# GPU Control control-plane 1.5.14 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `850a606352ad64fe5feed02f7896bdae79375f6c`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.14` | `sha256:f3a02cee0260da94da4eb1c46f335e44562e46ec0f48e4811295903c86098e0f` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.14` | `sha256:0c443e700907df0a8a1cc3bf4b10f118a4ae1fe171fb4f9f6787cd233fe5c9bc` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.14` | `sha256:694f5c6b2e1c229a304d22f43bf0c825c740b67d72a7b7f8001dc1793cf172e6` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.14` | `sha256:075314ef22852b486d94752245f52d61c27d5bb301b5da0b7446b882a76bec66` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.48` | `sha256:38acbb0d21f55a442d55ebaa893755838b50373a35ca862581f455c2ed86fad3` | `PENDING_REGISTRY_PUSH` |

The WSL telemetry component is archived separately after the five-image packager completed:

| Image | Local image ID | Source revision |
|---|---|---|
| `gpu-control-node-agent:1.5.14` | `sha256:5375a173251aebdc3ff0dc00df39755b31b3270ab361ac7254d96933d014627a` | `850a606352ad64fe5feed02f7896bdae79375f6c` |

All five first-party images were built from the same clean, pushed full Git SHA. Control-plane
components use release `1.5.14` and the Blender Worker uses
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
cat gpu-control-control-plane-1.5.14-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.14-images.tar.gz
cat gpu-control-node-agent-1.5.14-image.tar.gz.part-* \
  > gpu-control-node-agent-1.5.14-image.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.14-images.tar.gz
gzip -t gpu-control-node-agent-1.5.14-image.tar.gz
docker image load --input gpu-control-control-plane-1.5.14-images.tar.gz
docker image load --input gpu-control-node-agent-1.5.14-image.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.14-images.tar.gz`
SHA-256: `8aceac4df70a61973b99860a77509ef6e53967972bf23716e69ae907db79f8d4`
Size: `837034048` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.14-images.tar.gz.part-00` — `4ef3adde47e85451077479b48b87b560e91f2fac7a966e50d26383e1565aa018`, 134217728 bytes
- `gpu-control-control-plane-1.5.14-images.tar.gz.part-01` — `570c18109fdfea1a8401b5ba13a830fe885e8f906b9f6692e09283c2062dbf9b`, 134217728 bytes
- `gpu-control-control-plane-1.5.14-images.tar.gz.part-02` — `236fbcd8d366305369f0289dce420e76e15dcae4470e0ca4780bfd34df8e663c`, 134217728 bytes
- `gpu-control-control-plane-1.5.14-images.tar.gz.part-03` — `f82c26bacb8d9227a79b2c4763fc92b3a1dcb68e43956abad6a763a1b7362ed3`, 134217728 bytes
- `gpu-control-control-plane-1.5.14-images.tar.gz.part-04` — `d5d4084842504079e6ebb397c2d4183932acc82527cd4277ebd98f79eb02c208`, 134217728 bytes
- `gpu-control-control-plane-1.5.14-images.tar.gz.part-05` — `2516d0abbb8a2e0b3c9f83e041d8c7bda91a32a7bab7cc55f439454c0ede485e`, 134217728 bytes
- `gpu-control-control-plane-1.5.14-images.tar.gz.part-06` — `54bb1d305de820307b9160f3e636ef29a2eb6d57307a10703acf07d4cf7eb04f`, 31727680 bytes
- `gpu-control-node-agent-1.5.14-image.tar.gz.part-00` — `6a694eb3a917ae65615bd42082cdefd7440c8fd2d8832c43c96d1cd5c4d4afca`, 89215655 bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
