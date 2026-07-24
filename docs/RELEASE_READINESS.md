# Free Beta Release Readiness

The free beta uses staged distribution, not runtime licensing. No billing,
entitlement server, or destructive expiration switch is permitted.

Run the internal operational check:

```powershell
C:\Mimir_Backend\.venv-runtime\Scripts\python.exe C:\Mimir_Backend\mimir_core_v2_release_check.py `
  --internal --input "C:\Mimir\Test" --source-set current_crash_door_ding_set
```

Run the strict external gate:

```powershell
C:\Mimir_Backend\.venv-runtime\Scripts\python.exe C:\Mimir_Backend\mimir_core_v2_release_check.py --gate-only
```

Build an unsigned installer for internal dogfood without creating updater
artifacts:

```powershell
npm run desktop:build:internal
```

The normal `npm run desktop:build` remains the trusted release path and requires
the updater private key. An internal build must never be distributed as an
external beta release.

The strict command must fail until every blocking item is complete. Do not override
it to ship an invite build. Required evidence includes locked dataset metrics,
clean repositories, signed artifacts, SBOM/security reports,
clean Windows 10/11 VM results, accessibility evidence, and update/rollback evidence.

Run the real-fixture reliability gate with the packaged scanner only after every
required fixture is connected:

```powershell
C:\Mimir_Backend\.venv-runtime\Scripts\python.exe C:\Mimir_Backend\mimir_core_v2_reliability.py `
  --scanner "C:\Mimir\src-tauri\resources\mimir-backend\mimir-core-v2-scan.exe" `
  --iterations 500 `
  --report "C:\Mimir\release_assets\reliability_report.json"
```

For a short developer smoke run, add `--iterations 5 --allow-missing-required`
and write to a non-canonical report path. Smoke reports are deliberately unable
to pass the release gate, even when all executed runs succeed.

Rollout is internal dogfood, then approximately 25 invited users, then approximately
100. Promotion requires accuracy, crash-free, data-safety, support-volume, and
usability review for each cohort.
