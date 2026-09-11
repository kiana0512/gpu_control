# Rebuild the pinned control-plane contexts

The API/Scheduler modules are stored once in Git history. This directory retains
the Dockerfiles, layer installers, exact SHA-256 manifests and historical patches.
`prepare_context.py` reads module bytes with `git show`; it never reads modules
from the current worktree or applies the patches to it.

Required Git tag: `runtime/5070-control-c61648f`, resolving exactly to
`c61648fe96fd5be429cd1f7b470da43131b88204`. Both locked commits must be available:

| Stage | Locked source commit | Resulting package version |
| --- | --- | --- |
| `resource` | `13ef0a129759404c2313bc7d7a31827c1244eea8` | `1.5.23.post2` |
| `final` | `c61648fe96fd5be429cd1f7b470da43131b88204` | `1.5.23.post3` |

The resource commit is an ancestor of the required tag. Fetch that exact tag in
the repository before preparing contexts; a shallow clone may also need its
missing history fetched.

```sh
git fetch origin tag runtime/5070-control-c61648f
python3 artifacts/control-plane/5070ti-20260911/source/prepare_context.py \
  --stage resource --output /tmp/5070-resource-context
python3 artifacts/control-plane/5070ti-20260911/source/prepare_context.py \
  --stage final --output /tmp/5070-final-context
```

Use absent or empty temporary directories. The script rejects a moved tag,
missing commit, mismatched module hash or nonempty output. It copies the selected
Dockerfile, installer and `base-modules.json`, reconstructs every required module,
and writes `context-manifest.json` containing the source revision and file hashes.

Use the resulting directory as the Docker build context with the unchanged
stage Dockerfile. Supply the reviewed `BASE_IMAGE`, `SOURCE_REVISION` from the
table and `COMPONENT=api` or `scheduler`; the final stage also requires
`BASE_IMAGE_ID`. The resource stage layers onto the matching post1 component
image; the final stage layers onto the matching post2 component image. Existing
layer installers validate the base module hashes before replacement. Preparing a
context does not build an image, access credentials or change running services.

`context-verification.json` records the comparison against all six former curated
module copies before their removal and the successful rebuild after removal.
