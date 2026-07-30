# Six API 120 VU R8 raw evidence

This directory preserves the complete, scrubbed result bundle for production load-test session
`sixapi-20260730-r8`.

- Result: `BOUNDED_STRESS_ACCEPTED`
- Archive: `sixapi-20260730-r8-results.tar.gz`
- Size: `842402` bytes
- SHA-256: `ceddded2588b139ad5971c76ee70561c49e877908bf9d2decb0c07ab74ebbabc`
- Storage: Git LFS
- Secrets: the harness reports `secrets_recorded=false`; an additional repository-side scan found no
  bearer token, API-key value, private-key header, or OpenAI-style key pattern.

The archive was built deterministically from
`/srv/gpu-control/load-results/sixapi-20260730-r8` with sorted paths, a fixed mtime, numeric owner/group,
and `gzip -n`. Two independent builds were byte-identical. It contains the original JSON/JSONL/CSV/HTML
reports, configuration snapshots, manifest, and the internal `checksums.sha256` file.

Verify after cloning:

```bash
git lfs pull
sha256sum -c artifacts/load-tests/sixapi-20260730-r8/SHA256SUMS.txt
gzip -t artifacts/load-tests/sixapi-20260730-r8/sixapi-20260730-r8-results.tar.gz
```

After extraction, run `sha256sum -c checksums.sha256` inside the
`sixapi-20260730-r8` directory. All 15 internal entries passed before archival.

The human-readable acceptance decision and scope limits are in
[`docs/76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md`](../../../docs/76_2026-07-31_SIX_API_120VU_FINAL_ACCEPTANCE.md).
