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

## Advisory delivery override (2026-08-04)

The product owner explicitly changed V6 quality enforcement from fail-closed to
advisory delivery. When an intact V6 candidate exists and the hard integrity
checks pass, GPU Control now:

- preserves or restores the candidate as `final_low.blend` and `final_low.fbx`;
- continues independent QA and retains all eight gate results;
- publishes both model files under the public `blend` and `fbx` artifact kinds;
- returns terminal `SUCCEEDED` with
  `qa_warning.code=RETOPOLOGY_QUALITY_GATE_WARNING` when QA does not pass;
- keeps source SHA, artifact SHA/size, manifest identity, schema validity and
  source immutability as hard failures.

This override does not turn a missing, empty, corrupt, identity-mismatched or
source-mutating output into a success. It only prevents topology-quality
findings from withholding usable candidate model files.

## Incident timing evidence

Production job `1b1ec519-7b01-4576-85b7-f54c1e5ed68a` ran for 14m29s. Its first
attempt lost about 5m30s to the inherited-stdout lease-renewal defect. The
second attempt ran for about 8m59s: it reached the synthetic 70% marker in 2m,
then spent about 6m58s on geometry correction, multiview evidence and wireflow
inspection before the old fail-closed policy rejected the candidate. The
lease-renewal fix removes the first delay; the advisory override returns the
candidate BLEND/FBX with a warning instead of reporting a quality-gate failure.

## Fast advisory completion follow-up

The next production attempt proved that the formal build could finish and
write both model files, while the independent QA subprocess itself exited
before writing a report. The old error path deleted the temporary workspace
and classified the exception as retryable, causing the expensive formal build
to start a second time.

The follow-up release changes that control-plane behavior:

- an independent-QA runtime exception now produces schema-valid advisory
  `qa_report.json`, `result.json`, `manifest.json`, and an event record carrying
  the exact exception;
- intact `final_low.blend` and `final_low.fbx` remain publishable with
  `RETOPOLOGY_V6_QA_RUNTIME_FAILED` recorded as a warning;
- a V6 `BLENDER_EXECUTION_FAILED` reported at or after the 70% post-build stage
  is not automatically retried from the beginning;
- the complete Worker error message and retry-suppression decision are retained
  in `asset_job_events` for diagnosis.

Deployment images are
`unified-scheduler-asset-api:1.6.2-retopo-v6-fast-advisory` and
`li3d/blender-worker:1.3.2-retopo-v6-fast-advisory`. All three Linux Blender
Workers were rolled while the production queue was empty, returned ONLINE with
zero jobs, and 3090-B was returned from DRAINING to ACTIVE after its new
heartbeat was observed.
