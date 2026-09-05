# UV Multi-Mesh MOF Docker release manifest

This directory is the small Git-side package for the 2026-08-20 Asset API and
Blender Worker build. The actual images remain Docker-native and are identified
by immutable local digest plus the exact source revision. No Docker tar, model,
Canary FBX/Blend or new Git LFS object is included.

- Source revision: `104a67b2512a79236fef8112d8accb5526e24a42`
- Asset API package tag: `unified-scheduler-asset-api:1.5.16-uv-multimesh-mof-v1-git-104a67b2512a`
- Blender Worker package tag: `li3d/blender-worker:1.4.55-uv-multimesh-mof-v1-git-104a67b2512a`
- Python 3.11 test result: `521 passed, 5 skipped`
- Image smoke tests: `ASSET_API_IMAGE_SMOKE_OK`, `BLENDER_WORKER_IMAGE_SMOKE_OK`
- New Git LFS upload: `0 bytes`

`release-manifest.json` is the machine-readable identity. These package tags do
not replace or restart the currently running production containers. A registry
push or offline archive must be a separate release action after choosing the
target registry/storage; it must not be stored in Git LFS by default.
