# GPU Control repository constraints

These constraints apply to every automated agent working in this repository.

## Ownership boundary

- GPU Control owns this repository, its control-plane API, scheduler, Web UI, deployment files,
  observability, queueing, transport, storage, and node-management code.
- ImageClip and ModelViewCreator are externally owned business pipelines. Their Git repositories,
  workflow JSON files, custom-node implementations, model choices, inference parameters, prompts,
  graph topology, and output semantics are outside GPU Control's ownership boundary.
- Unless the user explicitly and unambiguously requests a specific out-of-boundary edit, agents MUST
  NOT edit, commit, revert, reset, push, or otherwise change those external repositories or pipeline
  contents. A general request such as “optimize speed”, “fix deployment”, “make it stable”, or “sync
  the three nodes” is not authorization to change a business workflow.
- If an out-of-boundary change appears necessary, stop and ask for explicit approval that names the
  repository/file and intended semantic change. Do not infer permission from urgency.

## Allowed pipeline operations

- Read-only inspection, hash calculation, version comparison, health checks, and compatibility checks.
- Deploying or synchronizing an exact user-approved upstream commit without modifying its contents.
- Mounting the approved files, prefetching approved models, validating SHA-256, and exposing the
  approved input/output contract through GPU Control.
- Rolling nodes safely between `DRAINING` and `ACTIVE` without changing the pipeline itself.

## Performance work

- Optimize only inside the GPU Control boundary unless the user explicitly expands scope.
- Preferred areas are scheduling, warm-node affinity, workflow-switch policy, prewarming, admission
  control, queue feedback, upload/download paths, artifact I/O, HTTP connection reuse, observability,
  and control-plane/database efficiency.
- Never meet a latency target by changing an external workflow's node parameters, prompt logic,
  models, resolution, sampling steps, graph, or final output node.
- Never return a preview or intermediate artifact in place of the approved final output.

## Production safety

- Preserve active production jobs. Drain a node before restart or replacement and verify it has no
  running job before mutation.
- Keep externally owned pipelines hash-aligned across all three nodes and with the approved remote
  revision before returning nodes to service.
- Record any authorized production change and its verification result in repository documentation.
