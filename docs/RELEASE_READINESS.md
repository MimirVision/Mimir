# Free Beta Release Readiness

The free private beta uses staged distribution, not runtime licensing. No billing,
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

The strict command must fail until every blocking item is complete. Do not override
it to ship an invite build. Required evidence includes locked dataset metrics,
clean repositories, signed artifacts, SBOM/security reports,
clean Windows 10/11 VM results, accessibility evidence, and update/rollback evidence.

Rollout is internal dogfood, then approximately 25 invited users, then approximately
100. Promotion requires accuracy, crash-free, data-safety, support-volume, and
usability review for each cohort.
