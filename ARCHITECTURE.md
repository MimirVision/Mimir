# Mimir monorepo

```
desktop/         Tauri 2 app -- React + TypeScript frontend, Rust shell
backend/         mimir_core_v2 -- the Python detection engine and its tooling
ingest-worker/   Cloudflare Worker that receives encrypted submissions
forge/           Internal annotation/triage tool (not shipped to users)
```

`C:\Mimir_Data` is deliberately **outside** this repo. It holds keys and
external source receipts, and nothing in it should ever be committed.

## Why one repo

These were four repos until 2026-08-04. Two bugs in a single day came from
changes that had to land in more than one of them at once:

- The chunked-upload contract spans `ingest-worker/src/index.ts`,
  `desktop/scripts/dev_intake_mock.py`, and `desktop/src-tauri/src/outbox.rs`.
- A too-short upload-id bound in the Worker passed every local test because
  the mock issued unrealistically short ids, then rejected every real upload.

`index.ts` carried a comment reading *"if this file's behavior drifts from the
mock's, update both together"* -- a convention doing a job that repository
structure now does. One commit can change both sides, and one CI run can check
them.

It also retired the cross-repo checkout in the release workflow, which needed
`vars.MIMIR_BACKEND_REPO` and `secrets.MIMIR_BACKEND_TOKEN` configured before a
tag build could produce sidecars at all.

## Contracts that span directories

Worth knowing before changing either side:

| Contract | Lives in |
|---|---|
| Intake HTTP routes, headers, rejection vocabulary | `ingest-worker/src/index.ts` ↔ `desktop/scripts/dev_intake_mock.py` |
| Scan progress protocol (`MIMIR_PROGRESS` json-lines) | `backend/mimir_core_v2/progress.py` ↔ `desktop/src/App.tsx` |
| Sidecar CLI arguments | `backend/mimir_core_v2/cli.py` ↔ `desktop/src-tauri/src/main.rs` |
| Session JSON shape | `backend/mimir_core_v2/output_writer.py` ↔ `desktop/src/types.ts` |

## CI

Workflows live at the repo root -- GitHub only runs them from there, so a
workflow inside a subdirectory silently never fires. Each is path-filtered so a
Worker typo does not spend twenty minutes running the desktop suite.

| Workflow | Fires on |
|---|---|
| `desktop-verify.yml` | `desktop/**` |
| `backend-verify.yml` | `backend/**` |
| `ingest-worker-verify.yml` | `ingest-worker/**`, and the dev mock |
| `release.yml` | tags matching `v*` |

## History

All four repositories were merged with `git subtree add`, so every original
commit is preserved -- 106 of them, oldest first being "Working mimir". The
earlier per-repo histories were also rewritten that day with `git filter-repo`
to strip committed build artifacts (PyInstaller sidecars, a `.venv/`, a built
installer, and ~140 MB of dashcam footage), which took the two main repos from
1.3 GB and 942 MB down to about 50 MB each.
