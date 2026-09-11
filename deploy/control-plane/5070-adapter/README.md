# 5070 Ti control adapter

This source patch starts at `143098c479664e71d9743ae26ee56391726e462a`.
Both live API/Scheduler settings modules were verified byte-for-byte against
that commit before editing. The derived images retain all other application
modules and dependencies from the exact running image IDs.

The image build changes the settings module, the API bootstrap script,
and installed wheel version/RECORD metadata. No UV, MOF, workflow or model
changes are included. The image environment, OCI revision, package version and
in-wheel `gpu-control-5070-layer.json` record the new source commit and exact
base image. This local source commit is not a claim of remote publication.

Runtime configuration is `NODE_AGENT_HMAC_SECRETS`, a JSON object mapping node
IDs to distinct secrets. Load it from a mode-0600 local file through the
replacement preparation script; do not put secrets in command arguments or logs.
Existing four per-node environment variables remain valid, and the new mapping
is excluded from settings dumps and repr output.

Registration is explicit and additive:

```sh
python /app/scripts/bootstrap_nodes.py --config /path/to/one-reviewed-node.yaml --add-only
```

The inventory must contain exactly one node, its verified physical IPv4/MAC/GPU
UUID/hostname, and `mode: DISABLED` or `DRAINING` (omitted mode defaults to
DISABLED). Existing IDs and physical identities are rejected. No old Node row
is updated. Keep the new node isolated until ComfyUI and asset acceptance pass;
RealESRGAN has separate admission and must not be prematurely listed in its
production controller configuration.

The optional Prometheus/UI source changes are separate from the two Python
runtime image layers. Apply the alert file only after reviewing its diff and
validating with promtool; rendering the new node does not require rebuilding
the web image because node enumeration is already dynamic.
