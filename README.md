# Mimir

Mimir is a free, invite-only Windows beta for local review of Tesla Sentry footage.
It finds moments worth checking, keeps source video local, and leaves the final
decision with the user. Generic MP4 input is best-effort.

There are no payments, subscriptions, accounts, activation servers, or mandatory
internet connection. Experimental local AI is optional, off by default, and never
overrides hard local safety evidence.

## Development

```powershell
npm install
npm run type-check
npm run build
npm run desktop:dev
```

Core v2 lives in `C:\Mimir_Backend`. Development can point at that workspace, while
packaged builds use self-contained executables and a per-user application data
directory. Python and packaged runners share the same scan arguments and versioned
JSON-lines progress protocol.

## Release Safety

External distribution is fail-closed. See [release readiness](docs/RELEASE_READINESS.md),
[privacy](docs/PRIVACY.md), [limitations](docs/LIMITATIONS.md), and the
[model card](docs/MODEL_CARD.md).

The strict gate must pass before an installer is shared:

```powershell
C:\Mimir_Backend\.venv-runtime\Scripts\python.exe C:\Mimir_Backend\mimir_core_v2_release_check.py --gate-only
```

The runtime detector is the checksum-pinned RF-DETR Nano ONNX model described in
the model card and backend model manifest. External release remains blocked until
the locked evaluation, signatures, clean-VM, accessibility, and update evidence
also pass.

Consent-first collection and training instructions live in
`C:\Mimir_Backend\TRAINING_DATA_GUIDE.md`.
