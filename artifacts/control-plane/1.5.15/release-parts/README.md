# GPU Control control-plane 1.5.15 candidate archive

> Status: **CANDIDATE_ARCHIVE_ONLY / NOT DEPLOYED / NOT PRODUCTION ACCEPTED**
>
> Source revision: `917646957755cd0583768d412f72f94fd2cb6043`. Registry manifest digests and strict
> `verify_release_identity.py` acceptance remain `PENDING_REGISTRY_PUSH`.

## Images

| Image | Local image ID | Registry digest |
|---|---|---|
| `gpu-control-api:1.5.15` | `sha256:9771235bd4bd5933d133b6aa8154699c0f5251295e161ef805755fc8ff2952dc` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-scheduler:1.5.15` | `sha256:67c9f1fd5a36a856067d08a9bfaf6d7fdd9770590bfec3c9f87521e5df5ea290` | `PENDING_REGISTRY_PUSH` |
| `unified-scheduler-asset-api:1.5.15` | `sha256:e800a0cde5e5277e5fe2cff42c8b9f33501cc82aa61517fd98c540716023ee4b` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-web:1.5.15` | `sha256:cbf44e1766404640719c69d90caec904fba9a0e51eb528371081fa8526766280` | `PENDING_REGISTRY_PUSH` |
| `li3d/blender-worker:1.4.48` | `sha256:713cbf4258c91ac586a042421826374d3ff2f5edf5438e6885fb6667a3916ddf` | `PENDING_REGISTRY_PUSH` |
| `gpu-control-node-agent:1.5.15` | `sha256:0bd52c863be5bb5fbaf8c87df08782faf618b9f84e199701fe497e28df3e96ab` | `PENDING_REGISTRY_PUSH` |

All five first-party images were built from the same clean, pushed full Git SHA. Control-plane
components use release `1.5.15` and the Blender Worker uses
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
cat gpu-control-control-plane-1.5.15-images.tar.gz.part-* \
  > gpu-control-control-plane-1.5.15-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t gpu-control-control-plane-1.5.15-images.tar.gz
docker image load --input gpu-control-control-plane-1.5.15-images.tar.gz
```

Combined archive: `gpu-control-control-plane-1.5.15-images.tar.gz`
SHA-256: `1c5193adb7d537fab34238623496b0c4a0604ab6baa6896e2568259a627461f5`
Size: `837037754` bytes

## Git LFS candidate parts

- `gpu-control-control-plane-1.5.15-images.tar.gz.part-00` — `bf875121ad05a86cc51a8f0fcfcd41b6a496ba80c45957723970752e5e3ee7ce`, 134217728 bytes
- `gpu-control-control-plane-1.5.15-images.tar.gz.part-01` — `798bc77c5c1f5547b29053e502db08b549de52762e3c7727303ec40b8aeb0dde`, 134217728 bytes
- `gpu-control-control-plane-1.5.15-images.tar.gz.part-02` — `08ed5511c3b6d7a6c1b902d2b016cc89b3552fa8942aaa0b58bc410b26697d0c`, 134217728 bytes
- `gpu-control-control-plane-1.5.15-images.tar.gz.part-03` — `a60c2b987782ce0d0671e02a1200c3fd9f2813a12c1bfbc4b15a0859a68101db`, 134217728 bytes
- `gpu-control-control-plane-1.5.15-images.tar.gz.part-04` — `7008b0af2776edae72768ff1d7fd6f7e828a7f5f7e3f968f74d99b0b163adea4`, 134217728 bytes
- `gpu-control-control-plane-1.5.15-images.tar.gz.part-05` — `ac58e65877391ebd4406c9f61c4c0a0eb550de15be749ad8b4f4557cccb0b845`, 134217728 bytes
- `gpu-control-control-plane-1.5.15-images.tar.gz.part-06` — `c1f2a1788b99fb9b0cd2b9177b690a0c1c57c70e08508deb761a462b03de5c18`, 31731386 bytes
- `gpu-control-node-agent-1.5.15-image.tar.gz.part-00` — `908df1d70284a4257d8fdb116a3840960d8f6f1f4dea3f0953ef631dce0a323d`, 89245100 bytes

Node Agent archive: `gpu-control-node-agent-1.5.15-image.tar.gz`  
SHA-256: `908df1d70284a4257d8fdb116a3840960d8f6f1f4dea3f0953ef631dce0a323d`  
Size: `89245100` bytes

The OIDs in `release-candidate-evidence.json` are content-hash candidates only. They become Git LFS
pointers only after a separate reviewed `git add`/commit and must be confirmed with `git lfs fsck`
and remote-object checks. This script intentionally does not run those commands.
