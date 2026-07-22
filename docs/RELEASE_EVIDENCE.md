# Free Beta Release Evidence

External invitations are fail-closed. The release check requires:

- trusted Authenticode signatures for every sidecar, application, and installer;
- signed Tauri updater artifacts plus update, rollback, and session-preservation evidence;
- clean Windows 10 and Windows 11 install, scan, upgrade, rollback, and uninstall records;
- a current SBOM, dependency audit, secret scan, model-license scan, and packaged-content scan;
- automated accessibility checks plus manual screen-reader and scaling evidence;
- 500 real-fixture reliability runs with source integrity and media availability results;
- a locked candidate evaluation report that passes every promotion gate.

Missing evidence is a blocker, not a warning. The current unsigned installer remains
for internal dogfood only. The beta is free and invite-only; distribution controls
access without runtime activation, accounts, billing, or an expiration switch.

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
