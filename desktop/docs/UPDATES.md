# Signed Update And Rollback Policy

Beta updates must be signed with the same trusted publisher identity as the
installer. The update manifest, application, backend executables, and installer are
release artifacts and must be verified before distribution.

Mimir must preserve prior session folders across upgrade, failed upgrade, rollback,
and uninstall unless the user explicitly requests data removal. A failed update must
leave the previous signed build usable. The beta does not fetch unsigned
packages or execute arbitrary updater commands.

The updater verification public key is pinned in the packaged application and Tauri
produces signed updater artifacts during the trusted release build. The HTTPS update
endpoint remains deliberately unset until a trusted distribution host exists; an
internal build therefore cannot fetch updates accidentally. This holds regardless of
whether the installer itself is currently signed -- auto-updates are a separate,
higher-risk surface and stay off until they can be fully verified. The strict release
checker continues to block distribution until a signed update and rollback are tested
on clean Windows 10 and Windows 11 machines with session preservation verified.
The release build injects the approved manifest URL through
`MIMIR_UPDATE_ENDPOINT`; development and unsigned internal builds contain no update
endpoint.

## Detector Model Updates (separate from app updates)

The detector model can be replaced without reinstalling the app, and this path is
deliberately unrelated to the Tauri updater described above.

`mimir_core_v2/model_manifest.py` checks an override directory named by the
`MIMIR_MODEL_OVERRIDE_DIR` environment variable *before* falling back to the
model baked into the packaged scanner. The override is used only if every model
file the accompanying manifest declares exists on disk with a matching SHA-256;
a partial, corrupted or tampered override is ignored and the bundled model
remains in force. The app passes this directory when spawning a scan
(`src-tauri/src/main.rs`, `MODEL_OVERRIDE_DIR_ENV`).

Installation is handled by `mimir_core_v2/model_update.py`, exposed as the
`mimir-core-v2-model-update.exe` sidecar and driven from the UI by
`src/components/ModelUpdatePanel.tsx`. It validates a candidate package before
touching anything, and rejects any package that fails its checksum, is missing a
declared licence file, sets `release_blocker: true`, or does not set
`commercial_distribution_approved: true`. Installation is atomic: a failed
install leaves the previous model in place.

**This mechanism never touches the network.** It only validates and moves files
that are already on disk, so the "no internet connection required to scan"
statement in SYSTEM_REQUIREMENTS.md and PRIVACY.md holds. Obtaining a model package is a
separate, manual, human action -- there is no automatic model download, and no
model changes without someone explicitly installing it.

Model provenance for whatever is currently active is recorded in every scan
session under `detector_manifest`, including `_model_source` (`bundled` or
`override`) so a session's results can always be traced to the model that
produced them.
