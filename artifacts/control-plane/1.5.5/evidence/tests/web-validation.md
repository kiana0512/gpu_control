# Web candidate validation

- Session: `web-final-audit-20260730T0854Z`
- Candidate version: `1.5.5`
- Execution boundary: `node:22-alpine`, `--network=none`, at most 2 CPUs / 4 GiB
- Production access: none

| Gate | Command | Exit | Result |
|---|---|---:|---|
| Format | `npm run format:check` | 0 | all files match Prettier |
| ESLint | `npm run lint` | 0 | zero warnings/errors |
| Vue typecheck | `npx vue-tsc -b --pretty false` | 0 | zero type errors |
| Vitest | `npx --no-install vitest run --reporter=junit --outputFile=/evidence/web-vitest.junit.xml` | 0 | 2 files, 8 passed, 0 failed/errors |
| Type/build | `npm run build` | 0 | `vue-tsc -b` and Vite production build passed; 2239 modules transformed |
| Mocked browser QA | Playwright 1.55.0, Chromium, 1440×1000 and 390×844 | 0 | 20 interaction/render checks passed; zero console warnings/errors |

Raw JUnit:

- Path: `artifacts/control-plane/1.5.5/evidence/tests/web-vitest.junit.xml`
- SHA-256: `e2b1d20a5f73cfbf51ab540ab69474ff0ba9cdf85194d7c83648ded388a3ff3f`
- JUnit timestamp: `2026-07-30T08:59:21.673Z`

Browser-plugin classification: unavailable in this session. Regular Playwright was used as the
documented fallback. A localhost-only Vite server rendered the candidate; every `/admin/**`
request was intercepted with deterministic mock data, so the browser did not authenticate to or
read production. Verified flows included:

- task function filtering, visible submit/start/end times, the nine-stage parent-batch timeline,
  workflow identity and SHA evidence;
- production/test analysis switching, stale-filter reset, and preservation of `kind=test` when
  navigating from analysis to task details;
- plain-language scheduler explanation and the collapsed/expanded advanced-policy interaction;
- desktop dashboard scope copy that does not claim unimplemented test-queue priority;
- mobile task cards at 390×844, no document-width overflow, and a 16 px primary task label.

Temporary screenshot evidence was captured outside the repository at:

- `/tmp/gpu-control-webui-final-audit-jobs-desktop.png`
- `/tmp/gpu-control-webui-final-audit-scheduling-desktop.png`
- `/tmp/gpu-control-webui-final-audit-dashboard-desktop.png`
- `/tmp/gpu-control-webui-final-audit-jobs-mobile.png`

The build emitted only two non-blocking bundler notices: Rollup removed misplaced
third-party `@vueuse` PURE annotations, and the lazily loaded Dashboard/ECharts chunk is
504.46 kB (173.52 kB gzip). There were no TypeScript, lint, test, browser-console, or build
failures. Safari/Firefox and live API responses remain outside this offline candidate gate.
