# Retopology V6 runtime stability hotfix

Date: 2026-08-04  
Scope: GPU Control Asset API, Linux Blender Workers, and GPU Control Web UI.

## Incident summary

The first production V6 attempts exposed three control-plane defects rather than
changes to the externally owned retopology policy:

1. shared bundle extraction accepted only `retopology_input.v1`, so a valid V6
   manifest could be rejected before dispatch;
2. the formal Agent requires a nested workspace sandbox, but the Worker image
   did not contain `bubblewrap` and the container lacked the explicitly approved
   namespace capability/security profile;
3. after a retried attempt was claimed, the mutable job row retained the prior
   attempt's error. External clients could therefore render a contradictory
   `RUNNING` plus `ASSET_LEASE_EXPIRED` state.

A fourth issue was found during the live run: if a direct subprocess exited
while a descendant still held its stdout pipe, the Worker waited for output
drain without renewing the lease. The task could be reaped after five minutes
despite useful descendant work still running.

## Implemented changes

- accept the frozen V1 and V6 manifest schema versions at shared extraction;
- install `bubblewrap` in the Blender Worker image;
- enable the user-approved `SYS_ADMIN`, `seccomp=unconfined`, and
  `apparmor=unconfined` settings only on Blender Worker containers;
- raise the V6 subprocess hard timeout from 600 to 1800 seconds;
- renew the asset lease while an inherited stdout pipe is being drained and
  continue honoring cancellation and the hard timeout;
- clear stale attempt errors when a queued job is claimed again;
- suppress caller-facing `error` for every non-terminal asset status while
  keeping all prior-attempt evidence in `asset_job_events`;
- label V6 clearly in the Web UI and prevent a prior-attempt error card from
  appearing while the job is still running.

No ComfyUI service, model cache, workflow, prompt, or externally owned business
pipeline content was modified.

## Deployment and verification

- 3090-A and 3090-B Blender Workers were rolled only after their effective
  production assignment was empty.
- Worker image ID: `sha256:e0b43178cd4a0a3c2990364fdaa20a7bb638b7971c0cb266b9143c1794c86ef2`.
- Both remote Workers report `CODEX_JOB_TIMEOUT_SECONDS=1800`.
- The running production job's stale mutable error was cleared without changing
  its event history or interrupting the active 4090 attempt.
- Focused regression suites: 18 Worker/V6 tests passed; the expired-lease reclaim
  and public-response regression test passed.
- GPU Control Web production bundle built successfully and was hot-recreated
  without restarting API, Scheduler, Workers, or ComfyUI.

## Acceptance rule

V6 is accepted only after a terminal `SUCCEEDED` response publishes both the
formal BLEND and FBX artifacts and their SHA-256 values. Candidate or rejected
artifacts remain diagnostic-only and must never be substituted for final output.
