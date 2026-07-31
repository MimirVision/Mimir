# Free Beta Release Evidence

The strict release check is what gates *promotion out of beta* and any accuracy
claim made about Mimir. It requires:

- trusted Authenticode signatures for every sidecar, application, and installer;
- signed Tauri updater artifacts plus update, rollback, and session-preservation evidence;
- clean Windows 10 and Windows 11 install, scan, upgrade, rollback, and uninstall records;
- a current SBOM, dependency audit, secret scan, model-license scan, and packaged-content scan;
- automated accessibility checks plus manual screen-reader and scaling evidence;
- 500 real-fixture reliability runs with source integrity and media availability results;
- a locked candidate evaluation report that passes every promotion gate.

Missing evidence is a blocker for those claims, not a warning: nothing here may
be marked satisfied on the strength of an assurance rather than a report.

The free beta itself ships before this gate is green, unsigned, with the gaps
stated plainly in [MODEL_CARD.md](MODEL_CARD.md) and
[LIMITATIONS.md](LIMITATIONS.md) rather than hidden. That is a deliberate
decision: the gate's accuracy evidence depends on consented real-world footage,
and the beta is how that footage is gathered, so requiring the gate first would
be circular. What it does *not* license is claiming the numbers already exist.
The beta is free; whatever distribution model is used, there is no runtime
activation, accounts, billing, or expiration switch.

External builds require `MIMIR_SIGNING_CERT_THUMBPRINT` and an HTTPS
`MIMIR_UPDATE_ENDPOINT`. The release script signs every packaged executable,
including the dataset exporter and bundled `age` binary, then verifies installer and
updater signatures before the clean-VM update and rollback test may be recorded.

Record each stage from the matching VM, using the exact signed installer under
test:

```powershell
.\scripts\record_clean_vm_check.ps1 -Platform windows_11 -Stage install `
  -Passed $true -Artifact C:\Release\MimirSetup.exe
```

The recorder rejects platform mismatches, Python-enabled machines, unsigned
artifacts, and missing artifact hashes. Repeat for every required stage on both
Windows versions; a boolean-only report cannot satisfy the gate.

After `npm run test:accessibility` passes, record the manual screen-reader and
desktop scaling pass with:

```powershell
.\scripts\record_accessibility_manual_check.ps1 `
  -Tester "Name" `
  -ScreenReader "NVDA 2026.1 on Windows 11" `
  -Scaling "125%, 150%, and 200%" `
  -Notes "Import, scan setup, incident library, and incident viewer checked."
```
